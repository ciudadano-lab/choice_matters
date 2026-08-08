import asyncio
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from game_data import VALID_PLAYERS, build_system_prompt
from intent_parser import parse_player_intent
from llm_client import call_narrator
from narrator import narrate_simulation_result
from rooms_store import init_db, create_room, get_room, save_room, room_exists, normalize_room_state
from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState
from ws_manager import manager

app = FastAPI(title="Zombie Sandbox Online")

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def append_debug_log(message: str) -> None:
    log_path = LOG_DIR / "decision_errors.log"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before hosting this anywhere public
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(frontend_dir / "index.html")


@app.get("/api/rooms/{code}", include_in_schema=False)
def api_get_room(code: str):
    state = get_room(code.upper())
    if state is None:
        raise HTTPException(404, "Room not found")
    return {"code": code.upper(), "state": state}


# One lock per room so concurrent rooms don't block each other, but two
# actions inside the SAME room (e.g. two people submitting at once) still
# resolve one at a time against consistent state.
_room_locks: dict[str, asyncio.Lock] = {}


def get_room_lock(room_code: str) -> asyncio.Lock:
    if room_code not in _room_locks:
        _room_locks[room_code] = asyncio.Lock()
    return _room_locks[room_code]


MENTION_RE = re.compile(r"player\s*#?\s*(\d+)", re.IGNORECASE)


def check_unknown_mention(text: str) -> str | None:
    """Non-blocking heads-up only — see game_data SHARED_RULES point 8.
    Mentioning an existing player never redirects the message; only a
    genuinely nonexistent number (4+) gets a note appended."""
    m = MENTION_RE.search(text)
    if not m:
        return None
    num = int(m.group(1))
    if num in VALID_PLAYERS:
        return None
    return f'There\'s no profile for "{m.group(0)}" in this world — only Player 1, 2, and 3 exist.'


def parse_reply(raw_text: str):
    match = re.search(r"```json\s*([\s\S]*?)```", raw_text)
    if not match:
        return raw_text.strip(), None
    narrative = raw_text[: match.start()].strip()
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        payload = None
    return narrative, payload


def merge_state(room_state: dict, player_num: int, payload: dict | None):
    if not payload:
        return
    st = room_state["players"][str(player_num)]
    facts_delta = payload.get("facts_delta")
    if facts_delta:
        st["facts"].update(facts_delta)
    threads = payload.get("threads")
    if isinstance(threads, list):
        for update in threads:
            existing = next((t for t in st["threads"] if t["id"] == update.get("id")), None)
            if existing:
                existing.update(update)
            else:
                st["threads"].append(update)
    recap_line = payload.get("recap_line")
    if recap_line:
        st["recap"].append(recap_line)
        st["recap"] = st["recap"][-10:]


def _normalize_location_ids(world_state: dict[str, Any]) -> str:
    location_ids = [item.get("id") for item in world_state.get("locations", []) if item.get("id")]
    return ", ".join(location_ids) if location_ids else "(none)"


def _build_ai_action_parse_prompt(player_num: int, actor_id: str, room_state: dict, user_text: str, interaction_target: int | None = None) -> str:
    st = room_state["players"][str(player_num)]
    known_locations = _normalize_location_ids(room_state.get("world_state", {}))
    open_threads = [t for t in st.get("threads", []) if t.get("status") != "resolved"]
    thread_block = "\n".join(
        f"- [{t['status']}] {t['id']}: {t['summary']}" for t in open_threads
    ) if open_threads else "(none currently open)"
    interaction_context = ""
    if interaction_target is not None:
        interaction_context = f"\nThe player is attempting to interact with Player {interaction_target}. Treat that as a meaningful social event."

    return f"""You are a structured-action parser for a zombie survival sandbox.
The current player is Player {player_num}.
Player facts: day {st['facts'].get('day')}, location {st['facts'].get('location')}, population {st['facts'].get('population')}, survivalScore {st['facts'].get('survivalScore')}, ethicsScore {st['facts'].get('ethicsScore')}.
Available location ids: {known_locations}.
Open threads:\n{thread_block}.{interaction_context}

Player input:
{user_text}

Return exactly one fenced JSON code block and nothing else. The block must contain only valid JSON conforming to this schema:
```json
{{
  "actionId": "string",
  "actionType": "travel|search|gather|build|reinforce|rest|communicate|negotiate|attack|defend|trade|recruit|send_mission|establish_settlement",
  "actor": "{actor_id}",
  "target": "string",
  "parameters": {{ }},
  "timeCost": 1,
  "resourceCost": {{ }},
  "risks": [],
  "prerequisites": []
}}
```
Use only the available location ids for the target. Do not include any explanation, narrative, or text before or after the fenced JSON block."""


def _build_action_from_payload(payload: Any) -> tuple[StructuredAction | None, str | None]:
    if not isinstance(payload, dict):
        return None, "AI parse returned invalid JSON payload."
    try:
        return (
            StructuredAction(
                actionId=str(payload["actionId"]),
                actionType=str(payload["actionType"]),
                actor=str(payload["actor"]),
                target=str(payload["target"]),
                parameters=payload.get("parameters") or {},
                timeCost=int(payload.get("timeCost", 1)),
                resourceCost={k: int(v) for k, v in (payload.get("resourceCost") or {}).items()},
                risks=[str(item) for item in (payload.get("risks") or [])],
                prerequisites=[str(item) for item in (payload.get("prerequisites") or [])],
            ),
            None,
        )
    except Exception as exc:
        return None, f"AI parse failed to build action: {exc}"


def _extract_json_object(raw_text: str) -> Any:
    starts = [pos for pos, char in enumerate(raw_text) if char == "{"]
    for start in starts:
        stack = 0
        in_string = False
        escape = False
        for index, char in enumerate(raw_text[start:], start):
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                stack += 1
            elif char == "}":
                stack -= 1
                if stack == 0:
                    candidate = raw_text[start:index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    return None


def _parse_json_payload(raw_text: str) -> Any:
    _, payload = parse_reply(raw_text)
    if payload is not None:
        return payload
    payload = _extract_json_object(raw_text)
    if payload is not None:
        return payload
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(raw_text.strip())
    except Exception:
        pass

    try:
        cleaned = raw_text.strip()
        cleaned = cleaned.replace("\n", " ").replace("'", '"')
        return json.loads(cleaned)
    except Exception:
        return None


def _build_ai_narration_prompt(player_num: int, room_state: dict, action: StructuredAction, result: Any, user_text: str, interaction_target: int | None = None) -> str:
    base_prompt = build_system_prompt(player_num, room_state, interaction_target=interaction_target)
    action_parameters = {k: v for k, v in action.parameters.items() if v}
    action_params_text = "" if not action_parameters else f"Parameters: {action_parameters}\n"
    result_lines = [
        f"timeConsumed: {result.timeConsumed}",
        f"resourceChanges: {result.resourceChanges}",
        f"playerStatusChanges: {result.playerStatusChanges}",
        f"locationChanges: {result.locationChanges}",
        f"encounterProbability: {result.encounterProbability}",
        f"informationDiscovered: {result.informationDiscovered}",
        f"worldEventsTriggered: {result.worldEventsTriggered}",
    ]
    result_summary = "\n".join(result_lines)

    return f"""{base_prompt}

PLAYER INPUT:
{user_text}

STRUCTURED ACTION:
actionId: {action.actionId}\nactionType: {action.actionType}\nactor: {action.actor}\ntarget: {action.target}\n{action_params_text}timeCost: {action.timeCost}\nresourceCost: {action.resourceCost}\nrisks: {action.risks}\nprerequisites: {action.prerequisites}

SIMULATION RESULT:
{result_summary}

Write the narrative first, then output exactly one fenced json block in the schema already described in the system prompt. Do not invent additional state changes beyond what the simulation result describes."""


# ---------------------------------------------------------------------------
# REST: creating / checking rooms
# ---------------------------------------------------------------------------

@app.post("/api/rooms")
def api_create_room():
    code, state = create_room()
    return {"code": code, "state": state}


class SaveRoomRequest(BaseModel):
    state: dict
    saved_by_slot: int | None = None


@app.post("/api/rooms/{code}/save", include_in_schema=False)
def api_save_room(code: str, payload: SaveRoomRequest):
    room_code = code.upper()
    if not room_exists(room_code):
        raise HTTPException(404, "Room not found")

    state = normalize_room_state(payload.state)
    state.setdefault("meta", {})
    state["meta"].update({
        "saved": True,
        "save_count": int(state.get("meta", {}).get("save_count", 0)) + 1,
        "saved_by_slot": payload.saved_by_slot,
        "saved_at": state.get("meta", {}).get("saved_at") or "saved",
    })
    save_room(room_code, state)
    return {"code": room_code, "saved": True, "state": state}


@app.get("/api/rooms/{code}/load", include_in_schema=False)
def api_load_room(code: str):
    room_code = code.upper()
    state = get_room(room_code)
    if state is None:
        raise HTTPException(404, "Room not found")
    return {"code": room_code, "saved": bool(state.get("meta", {}).get("saved", False)), "state": state}


# ---------------------------------------------------------------------------
# WebSocket: live gameplay + chat
# ---------------------------------------------------------------------------

@app.websocket("/ws/{room_code}")
async def room_ws(websocket: WebSocket, room_code: str):
    room_code = room_code.upper()
    if not room_exists(room_code):
        await websocket.close(code=4404)
        return

    await manager.connect(room_code, websocket)
    try:
        state = get_room(room_code)
        await websocket.send_json({"type": "state", "state": state})

        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")

            if mtype == "claim_slot":
                await handle_claim_slot(room_code, websocket, msg)
            elif mtype == "chat":
                await handle_chat(room_code, websocket, msg)
            elif mtype == "decision":
                await handle_decision(room_code, websocket, msg)
            else:
                await websocket.send_json({"type": "error", "message": f"Unknown message type: {mtype}"})

    except WebSocketDisconnect:
        pass
    finally:
        slot = manager.slot_for(room_code, websocket)
        manager.disconnect(room_code, websocket)
        if slot is not None:
            # Free the slot so a reconnect (or someone else) can take it.
            async with get_room_lock(room_code):
                state = get_room(room_code)
                if state:
                    state["occupied"][str(slot)] = False
                    save_room(room_code, state)
                    await manager.broadcast(room_code, {"type": "state", "state": state})


async def handle_claim_slot(room_code: str, websocket: WebSocket, msg: dict):
    slot = msg.get("slot")
    if slot not in VALID_PLAYERS:
        await websocket.send_json({"type": "error", "message": "Invalid player slot."})
        return

    async with get_room_lock(room_code):
        state = get_room(room_code)
        if state["occupied"].get(str(slot)):
            await websocket.send_json({"type": "error", "message": f"Player {slot} is already taken."})
            return
        state["occupied"][str(slot)] = True
        save_room(room_code, state)
        manager.claim_slot(room_code, websocket, slot)
        await websocket.send_json({"type": "slot_claimed", "slot": slot})
        await manager.broadcast(room_code, {"type": "state", "state": state})


async def handle_chat(room_code: str, websocket: WebSocket, msg: dict):
    slot = manager.slot_for(room_code, websocket)
    text = (msg.get("text") or "").strip()
    if slot is None:
        await websocket.send_json({"type": "error", "message": "Claim a player slot before chatting."})
        return
    if not text:
        return

    async with get_room_lock(room_code):
        state = get_room(room_code)
        entry = {"slot": slot, "text": text}
        state["chat"].append(entry)
        state["chat"] = state["chat"][-200:]  # cap history, this is a chat log not a database
        save_room(room_code, state)
        await manager.broadcast(room_code, {"type": "chat", "entry": entry})


async def handle_decision(room_code: str, websocket: WebSocket, msg: dict):
    slot = manager.slot_for(room_code, websocket)
    text = (msg.get("text") or "").strip()
    requested_slot = msg.get("slot")

    if slot is None:
        await websocket.send_json({"type": "error", "message": "Claim a player slot before playing."})
        return
    if requested_slot != slot:
        await websocket.send_json({"type": "error", "message": "You can only act as your own player."})
        return
    if not text:
        return

    target_slot = msg.get("target_slot")
    if target_slot is not None:
        if target_slot not in VALID_PLAYERS:
            await websocket.send_json({"type": "error", "message": "Invalid target player."})
            return
        if target_slot == slot:
            await websocket.send_json({"type": "error", "message": "You cannot interact with yourself."})
            return

    async with get_room_lock(room_code):
        state = get_room(room_code)
        player_state = state["players"][str(slot)]
        player_state["log"].append({"role": "player", "text": text})

        if target_slot is not None:
            state.setdefault("interactions", [])
            state["interactions"].append({
                "actor": slot,
                "target": target_slot,
                "text": text,
            })
            state["interactions"] = state["interactions"][-20:]
            player_state["log"].append({
                "role": "system",
                "text": f"You attempt to interact with Player {target_slot}.",
            })

        unknown_note = check_unknown_mention(text)
        if unknown_note:
            player_state["log"].append({"role": "system", "text": unknown_note})

        actor_id = f"player_{slot}"
        world_state = WorldState.from_room_state(state)
        ai_parse_prompt = _build_ai_action_parse_prompt(slot, actor_id, state, text, interaction_target=target_slot)
        action = None
        parse_error = None
        raw_parse = ""
        try:
            raw_parse = await call_narrator(ai_parse_prompt, "")
            payload = _parse_json_payload(raw_parse)
            action, parse_error = _build_action_from_payload(payload)
        except RuntimeError as exc:
            parse_error = str(exc)
            append_debug_log(f"AI parse error for room={room_code} slot={slot}: {parse_error}")
            # If the LLM returned a raw response body, append it to the player's
            # system log so it appears in the web UI for analysis.
            raw = getattr(exc, "raw_response", None)
            if raw:
                player_state["log"].append({"role": "system", "text": f"RAW_LLM_RESPONSE: {raw}"})

        if action is None:
            fallback_actions, fallback_error = parse_player_intent(text, actor=actor_id, world_state=world_state)
            if fallback_actions:
                action = fallback_actions[0]
                player_state["log"].append({"role": "system", "text": "AI parse failed; using rule-based fallback."})
            else:
                player_state["log"].append({"role": "system", "text": parse_error or fallback_error or "Could not interpret the command."})
                save_room(room_code, state)
                await manager.broadcast(room_code, {"type": "state", "state": state})
                return

        engine = SimulationEngine(seed=sum(ord(ch) for ch in room_code))
        result = engine.apply(world_state, action)

        narrative = None
        raw_narrative = ""
        try:
            ai_narration_prompt = _build_ai_narration_prompt(slot, state, action, result, text, interaction_target=target_slot)
            raw_narrative = await call_narrator(ai_narration_prompt, "")
            narrative, _ = parse_reply(raw_narrative)
            if not narrative:
                narrative = raw_narrative.strip()
        except RuntimeError as exc:
            narrative = None
            error_text = str(exc)
            append_debug_log(f"AI narration error for room={room_code} slot={slot}: {error_text}")
            # Surface raw LLM response in player's log for debugging/analysis
            raw = getattr(exc, "raw_response", None)
            if raw:
                player_state["log"].append({"role": "system", "text": f"RAW_LLM_RESPONSE: {raw}"})
            else:
                player_state["log"].append({"role": "system", "text": "AI narration failed; using deterministic fallback."})

        if not narrative:
            player_known_info = {
                "location": player_state["facts"].get("location"),
                "knownNearbyLocations": player_state["facts"].get("knownNearbyLocations", []),
                "recap": player_state.get("recap", []),
                "relationships": world_state.relationships,
            }
            narrative = narrate_simulation_result(world_state.to_dict(), action, result, player_known_info)

        if action.actionType == "travel" and result.playerStatusChanges:
            for change in result.playerStatusChanges:
                if change.get("playerId") == actor_id and "location" in change:
                    player_state["facts"]["location"] = change["location"]

        player_state["log"].append({"role": "narrator", "text": narrative})

        state["world_state"] = result.updatedWorldState
        save_room(room_code, state)
        await manager.broadcast(room_code, {"type": "state", "state": state})
