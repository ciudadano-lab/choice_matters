import asyncio
import ast
import hashlib
import json
import copy
import os
import re
import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from fastapi.responses import Response

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from env_config import load_backend_env
from game_data import (
    POLICY_ACTION_TYPES,
    STRATEGIC_PERIOD_DAYS,
    VALID_PLAYERS,
    build_story_summary_prompt,
    build_system_prompt,
)
from information_layer import InformationLayer
from llm_client import call_narrator
from narrator import narrate_simulation_result
from relationships import RelationshipSystem
from rooms_store import _seed_from_code, init_db, create_room, get_room, save_room, room_exists, normalize_room_state
from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState
from world_tick import WorldTick, WorldEventQueue
from ws_manager import manager

load_backend_env()

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

@app.get('/ping', status_code=status.HTTP_200_OK, include_in_schema=False)
def ping():
    return {'ping':'pong'}

@app.get("/assets/environment-manifest")
def environment_manifest():
    base = frontend_dir / "assets" / "environment"
    manifest: dict[str, list[str]] = {}
    if base.is_dir():
        for role in os.listdir(base):
            role_dir = base / role
            if role_dir.is_dir():
                manifest[role] = sorted(
                    filename
                    for filename in os.listdir(role_dir)
                    if filename.lower().endswith((".png", ".webp"))
                )
    return manifest

@app.get("/assets/locations-manifest")
def locations_manifest():
    base = frontend_dir / "assets" / "locations"
    if not base.is_dir():
        return []
    return sorted(filename for filename in os.listdir(base) if filename.lower().endswith(".svg"))

@app.get("/assets/environment/{role}/{filename}", include_in_schema=False)
def environment_asset(role: str, filename: str):
    base = (frontend_dir / "assets" / "environment").resolve()
    asset_path = (base / role / filename).resolve()
    if base not in asset_path.parents or asset_path.suffix.lower() not in {".png", ".webp"} or not asset_path.is_file():
        raise HTTPException(404, "Environment asset not found")
    return FileResponse(asset_path)

@app.get("/assets/locations/{filename}", include_in_schema=False)
def location_asset(filename: str):
    base = (frontend_dir / "assets" / "locations").resolve()
    asset_path = (base / filename).resolve()
    if base not in asset_path.parents or asset_path.suffix.lower() != ".svg" or not asset_path.is_file():
        raise HTTPException(404, "Location asset not found")
    return FileResponse(asset_path)

@app.get("/assets/roads/{filename}", include_in_schema=False)
def roads_asset(filename: str):
    base = (frontend_dir / "assets" / "roads").resolve()
    asset_path = (base / filename).resolve()
    if base not in asset_path.parents or asset_path.suffix.lower() not in {".png", ".webp", ".svg"} or not asset_path.is_file():
        raise HTTPException(404, "Road asset not found")
    return FileResponse(asset_path)

@app.get("/assets/district/{filename}", include_in_schema=False)
def district_asset(filename: str):
    base = (frontend_dir / "assets" / "district").resolve()
    asset_path = (base / filename).resolve()
    if base not in asset_path.parents or asset_path.name != "ground_texture.svg" or not asset_path.is_file():
        raise HTTPException(404, "District asset not found")
    return FileResponse(asset_path)

@app.get("/assets/icons/{filename}", include_in_schema=False)
def asset_icon(filename: str):
    base = (frontend_dir / "assets" / "icons").resolve()
    asset_path = (base / filename).resolve()
    if base not in asset_path.parents or asset_path.suffix.lower() != ".svg" or not asset_path.is_file():
        raise HTTPException(404, "Asset icon not found")
    return FileResponse(asset_path)

@app.head('/ping', include_in_schema=False)
def ping_head():
    return Response(status_code=status.HTTP_200_OK)

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
_round_processing: set[str] = set()
_round_timeout_tasks: dict[str, asyncio.Task] = {}
ROUND_TIMEOUT_SECONDS = 90
SURVIVOR_CALLSIGNS = ["Ash", "Beacon", "Cinder", "Drift", "Ember", "Flint", "Harbor", "Morrow", "Rook"]


def get_room_lock(room_code: str) -> asyncio.Lock:
    if room_code not in _room_locks:
        _room_locks[room_code] = asyncio.Lock()
    return _room_locks[room_code]


def build_client_state(state: dict, recipient_slot: int | None) -> dict:
    client_state = copy.deepcopy(state)
    players = client_state.get("players", {})
    for slot, player in players.items():
        if recipient_slot is not None and int(slot) == recipient_slot:
            continue
        facts = player.get("facts", {})
        world_player = next(
            (item for item in client_state.get("world_state", {}).get("players", [])
             if item.get("id") == f"player_{slot}"),
            {},
        )
        players[slot] = {
            "facts": {"name": facts.get("name", f"Survivor {slot}")},
            "status": "alive" if world_player.get("alive", True) else "dead",
        }

    world = client_state.get("world_state", {})
    world_players = []
    for player in world.get("players", []):
        if recipient_slot is not None and player.get("id") == f"player_{recipient_slot}":
            world_players.append(player)
        else:
            world_players.append({
                "id": player.get("id"),
                "name": player.get("name"),
                "location": player.get("location"),
                "alive": player.get("alive", True),
                "currentActivity": player.get("currentActivity", "observing"),
            })
    world["players"] = world_players
    world.pop("actualWorld", None)
    world.pop("rumoredWorld", None)
    world["playerKnownWorld"] = {
        "seed": world.get("seed"),
        "locations": copy.deepcopy(world.get("playerKnownWorld", {}).get("locations", [])),
        "rumors": copy.deepcopy(world.get("playerKnownWorld", {}).get("rumors", [])),
    }
    client_state["world_state"] = world
    return client_state


async def broadcast_state(room_code: str, state: dict) -> None:
    await manager.broadcast_state(room_code, state, build_client_state)


def _callsign_for_player(room_seed: int, slot: int) -> str:
    digest = hashlib.sha256(f"callsign:{room_seed}:{slot}".encode("utf-8")).hexdigest()
    return SURVIVOR_CALLSIGNS[int(digest[:8], 16) % len(SURVIVOR_CALLSIGNS)]


_relationship_system = RelationshipSystem()

# Coarse mapping from a structured action's type to the relationship-engine
# event vocabulary (mission_risk / save / order / betray) so direct
# player-to-player interactions actually move trust/loyalty/fear/resentment.
_RELATIONSHIP_EVENT_BY_ACTION = {
    "attack": "betray",
    "send_mission": "mission_risk",
    "communicate": "save",
    "negotiate": "save",
    "trade": "save",
    "recruit": "save",
}

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


def _resolved_target_slot(action: StructuredAction, explicit_target_slot: int | None) -> int | None:
    if explicit_target_slot in VALID_PLAYERS:
        return explicit_target_slot
    target_entity = action.parameters.get("targetEntity")
    if not isinstance(target_entity, dict):
        return None
    match = re.fullmatch(r"player_(\d+)", str(target_entity.get("owner")))
    if not match:
        return None
    target_slot = int(match.group(1))
    return target_slot if target_slot in VALID_PLAYERS else None


def parse_reply(raw_text: str):
    match = re.search(r"```(?:json|JSON)?\s*([\s\S]*?)```", raw_text)
    if not match:
        match = re.search(r"```\s*([\s\S]*?)```", raw_text)
    if not match:
        return raw_text.strip(), None

    narrative = raw_text[: match.start()].strip()
    body = match.group(1).strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    return narrative, payload


# Numeric facts are true deltas (increments) per the SHARED_RULES schema —
# the AI is told to send 0 / 0.0 for anything unchanged, which only makes
# sense if these values get ADDED to the current fact, not used to replace
# it outright.
_ADDITIVE_FACT_KEYS = {"day", "population", "survivalScore", "ethicsScore"}
_SCORE_KEYS = {"survivalScore", "ethicsScore"}


def merge_state(room_state: dict, player_num: int, payload: dict | None):
    if not payload:
        return
    st = room_state["players"][str(player_num)]
    facts_delta = payload.get("facts_delta")
    if facts_delta:
        for key, value in facts_delta.items():
            if key in _ADDITIVE_FACT_KEYS and isinstance(value, (int, float)):
                current = st["facts"].get(key, 0)
                updated = current + value
                if key in _SCORE_KEYS:
                    updated = max(0.0, min(10.0, updated))
                elif key == "day":
                    updated = max(1, int(updated))
                elif key == "population":
                    updated = max(0, int(updated))
                st["facts"][key] = updated
            else:
                # Non-numeric or unrecognized fact keys (e.g. anything the
                # AI adds beyond the known schema) are just set directly.
                st["facts"][key] = value
    threads = payload.get("threads")
    if isinstance(threads, list):
        for update in threads:
            existing = next((t for t in st["threads"] if t["id"] == update.get("id")), None)
            if existing:
                existing.update(update)
            else:
                st["threads"].append(update)
    characters_delta = payload.get("characters_delta")
    if isinstance(characters_delta, list):
        entities = st.setdefault("entities", {})
        known_characters = entities.setdefault("knownCharacters", [])
        for update in characters_delta:
            if not isinstance(update, dict):
                continue
            existing = next((entry for entry in known_characters if entry.get("id") == update.get("id")), None)
            if existing:
                existing.update(update)
            else:
                known_characters.append(update)
    recap_line = payload.get("recap_line")
    if recap_line:
        st["recap"].append(recap_line)
        st["recap"] = st["recap"][-10:]


def _normalize_location_ids(world_state: dict[str, Any], player_id: str = "player_1") -> str:
    visible_locations = InformationLayer().get_visible_state(world_state, player_id=player_id)
    location_ids = [
        item.get("id")
        for item in world_state.get("locations", [])
        if item.get("id")
        and (
            item.get("discovered") is True
            or visible_locations.get(item.get("id"), {}).get("knownInformation")
        )
    ]
    return ", ".join(location_ids) if location_ids else "(none)"


def _build_ai_action_parse_prompt(player_num: int, actor_id: str, room_state: dict, user_text: str, interaction_target: int | None = None) -> str:
    st = room_state["players"][str(player_num)]
    known_locations = _normalize_location_ids(room_state.get("world_state", {}), player_id=actor_id)
    open_threads = [t for t in st.get("threads", []) if t.get("status") != "resolved"]
    thread_block = "\n".join(
        f"- [{t['status']}] {t['id']}: {t['summary']}" for t in open_threads
    ) if open_threads else "(none currently open)"
    interaction_context = ""
    if interaction_target is not None:
        interaction_context = f"\nThe player is attempting to interact with Player {interaction_target}. Treat that as a meaningful social event."
    entity_lines = []
    for player in room_state.get("world_state", {}).get("players", []):
        if not isinstance(player, dict):
            continue
        entities = player.get("entities", {})
        if not isinstance(entities, dict):
            continue
        for entity_type in ("companions", "knownCharacters", "possessions"):
            names = [
                item.get("name") if isinstance(item, dict) else item
                for item in entities.get(entity_type, [])
            ]
            names = [name for name in names if isinstance(name, str)]
            if names:
                entity_lines.append(f"- {player.get('id')}: {entity_type}: {', '.join(names)}")
    entity_block = "\n".join(entity_lines) if entity_lines else "(none currently tracked)"
    status_effects = room_state["players"].get(str(player_num), {}).get("statusEffects", [])
    status_effects_block = "\n".join(
        f"- {effect.get('name', 'status effect')} (expires day {effect.get('expiresDay', '?')}; penalty: {effect.get('mechanicalEffect', {})})"
        for effect in status_effects
        if isinstance(effect, dict)
    ) if status_effects else "(none)"
    pending_events = room_state["players"].get(str(player_num), {}).get("pendingEvents", [])
    pending_events_block = "\n".join(
        f"- From Player {event.get('from')}: {event.get('summary')}"
        for event in pending_events
        if isinstance(event, dict) and event.get("summary")
    ) if pending_events else "(none)"

    action_types = "|".join(POLICY_ACTION_TYPES)
    return f"""You are a structured-action parser for a zombie survival sandbox.
The current player is Player {player_num}.
Strategic cadence: one policy decision normally covers {STRATEGIC_PERIOD_DAYS} days (three weeks). Choose one policy category and state its concrete focus. Tactical actions are internal simulation details and must not be returned as the player's action type.
Player facts: day {st['facts'].get('day')}, location {st['facts'].get('location')}, population {st['facts'].get('population')}, survivalScore {st['facts'].get('survivalScore')}, ethicsScore {st['facts'].get('ethicsScore')}.
Available location ids: {known_locations}.
Tracked player entities:
{entity_block}
Active status effects:
{status_effects_block}
Pending events for this player:
{pending_events_block}
WORLD PHYSICS: This is a grounded, non-supernatural outbreak setting. Mark only magic, superpowers, physics-defying feats, or a claimed CURE/REVERSAL of the infection as impossible. Detecting, observing, or verifying infection status through mundane means (quarantine, symptom-watching, time) is always grounded, never impossible — do not confuse "checking for infection" with "curing infection."
Open threads:\n{thread_block}.{interaction_context}

Player input:
{user_text}

Return exactly one fenced JSON code block and nothing else. The block must contain only valid JSON conforming to this schema:
```json
{{
  "actionId": "string",
    "actionType": "{action_types}",
  "actor": "{actor_id}",
  "target": "string",
    "feasibility": "grounded|impossible",
    "parameters": {{
        "targetEntity": null
    }},
    "timeCost": 1,
    "resourceCost": {{ }},
    "policy": {{ "focus": "string", "commitmentDays": {STRATEGIC_PERIOD_DAYS}, "pivotalTiming": "immediate|culmination" }}
}}
```
Include `targetEntity` only when the action specifically names another player's companion or possession; otherwise omit it entirely or leave it `null`. Never emit an empty placeholder object for an action without an entity claim.
Use `policy.focus` to state the concrete priority or stance and `policy.commitmentDays` for the intended commitment length. Use `policy.pivotalTiming` as `immediate` when the focus describes an action happening right away, such as attacking or traveling immediately. Use `culmination` when the focus describes building toward an outcome over the commitment, such as gathering resources before founding a settlement. When ambiguous, use `culmination`. Use only the available location ids for the target. Do not include any explanation, narrative, or text before or after the fenced JSON block."""


def _build_action_from_payload(payload: Any) -> tuple[StructuredAction | None, str | None]:
    if not isinstance(payload, dict):
        return None, "AI parse returned invalid JSON payload."
    try:
        feasibility = str(payload.get("feasibility", "grounded")).casefold()
        if feasibility == "impossible":
            return StructuredAction(
                actionId=str(payload["actionId"]),
                actionType="failed_attempt",
                actor=str(payload["actor"]),
                target=str(payload.get("target", "")),
                parameters={
                    "physicallyImpossible": True,
                    "claimedActionType": str(payload.get("actionType", "unknown")),
                },
                timeCost=max(0, int(payload.get("timeCost", 1))),
            ), None
        action_type = str(payload["actionType"]).strip().casefold()
        if action_type not in POLICY_ACTION_TYPES:
            return None, "Player-facing actions must use a strategic policy category."
        return (
            StructuredAction(
                actionId=str(payload["actionId"]),
                actionType=action_type,
                actor=str(payload["actor"]),
                target=str(payload["target"]),
                parameters={
                    **(payload.get("parameters") or {}),
                    **({"policy": payload["policy"]} if isinstance(payload.get("policy"), dict) else {}),
                },
                timeCost=int(payload.get("timeCost", 1)),
                resourceCost={k: int(v) for k, v in (payload.get("resourceCost") or {}).items()},
            ),
            None,
        )
    except Exception as exc:
        return None, f"AI parse failed to build action: {exc}"


def _extract_json_object(raw_text: str) -> Any:
    if not raw_text:
        return None

    fenced_blocks = re.findall(r"```(?:json|JSON)?\s*([\s\S]*?)```", raw_text)
    for block in fenced_blocks:
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

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
        cleaned = re.sub(r"```(?:json|JSON)?", "", cleaned)
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
        f"encounterOccurred: {result.encounterOccurred}",
        f"encounterDetails: {result.encounterDetails}",
        f"rejected: {result.rejected}",
        f"rejectionReason: {result.rejectionReason}",
        f"physicallyImpossible: {result.physicallyImpossible}",
        f"impossibleReason: {result.impossibleReason}",
        f"outcomeTier: {getattr(result, 'outcomeTier', 'success')}",
        f"socialOutcome: {getattr(result, 'socialOutcome', {})}",
        f"informationDiscovered: {result.informationDiscovered}",
        f"worldEventsTriggered: {result.worldEventsTriggered}",
    ]
    result_summary = "\n".join(result_lines)
    prompt_lines = []
    if result.physicallyImpossible:
        prompt_lines.append(
            "THIS ACTION WAS REJECTED AS PHYSICALLY IMPOSSIBLE. You MUST narrate it "
            "failing to produce the claimed effect — the character attempts it and "
            "nothing supernatural happens. Do NOT narrate the claimed effect as "
            "succeeding, regardless of what the player wrote."
        )
    else:
        prompt_lines.append(
            f"This action's outcome tier is {getattr(result, 'outcomeTier', 'success')}. "
            "Narrate a believable outcome at that tier: critical_success = clear win, "
            "success = solid success, partial_success = mixed result with notable trade-offs, "
            "failure = clear setback, critical_failure = severe loss or collapse."
        )
        if action.actionType in SimulationEngine.POLICY_ACTIONS:
            policy = action.parameters.get("policy", {})
            commitment_days = policy.get("commitmentDays", STRATEGIC_PERIOD_DAYS) if isinstance(policy, dict) else STRATEGIC_PERIOD_DAYS
            prompt_lines.append(
                f"This is a strategic policy commitment covering {commitment_days} days. "
                "Write at period scale: summarize the consequential developments and trade-offs "
                "across the commitment, not a single day's scene. The Outcome beat must state "
                "the period-level result and its concrete cost or benefit."
            )
            prior_period = room_state.get("strategicPeriodOutcome", {})
            if prior_period:
                prompt_lines.append(
                    "Prior strategic-period result and accumulated developments are settled context: "
                    f"{prior_period}. Do not contradict them."
                )
        social_outcome = getattr(result, 'socialOutcome', None) or {}
        if social_outcome.get("reaction"):
            prompt_lines.append(
                "This social interaction has already been resolved by the simulation: "
                f"target={social_outcome.get('targetId')}, reaction={social_outcome.get('reaction')}, "
                f"threshold={social_outcome.get('threshold')}, roll={social_outcome.get('roll')}. "
                "Narrate the settled reaction, do not re-decide it."
            )
    prompt_block = "\n".join(prompt_lines)

    return f"""{base_prompt}

PLAYER INPUT:
{user_text}

STRUCTURED ACTION:
actionId: {action.actionId}\nactionType: {action.actionType}\nactor: {action.actor}\ntarget: {action.target}\n{action_params_text}timeCost: {action.timeCost}\nresourceCost: {action.resourceCost}

SIMULATION RESULT:
{result_summary}

{prompt_block}

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
async def api_save_room(code: str, payload: SaveRoomRequest):
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
    await save_room(room_code, state)
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
        await websocket.send_json({"type": "state", "state": build_client_state(state, manager.slot_for(room_code, websocket))})

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
                    state.setdefault("pendingDecisions", {}).pop(str(slot), None)
                    await save_room(room_code, state)
                    await broadcast_state(room_code, state)
            await _maybe_start_round(room_code)


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
        world_state = WorldState.from_room_state(state)
        callsign = _callsign_for_player(world_state.seed, slot)
        state["players"][str(slot)].setdefault("facts", {})["name"] = callsign
        world_player = next(
            (player for player in world_state.players if player.get("id") == f"player_{slot}"),
            None,
        )
        if world_player is not None:
            world_player["name"] = callsign
        state["world_state"] = world_state.to_dict()
        state["occupied"][str(slot)] = True
        await save_room(room_code, state)
        manager.claim_slot(room_code, websocket, slot)
        await websocket.send_json({"type": "slot_claimed", "slot": slot})
        await broadcast_state(room_code, state)


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
        await save_room(room_code, state)
        await manager.broadcast(room_code, {"type": "chat", "entry": entry})


async def _maybe_start_round(room_code: str) -> None:
    async with get_room_lock(room_code):
        if room_code in _round_processing:
            return
        state = get_room(room_code)
        if state is None:
            return
        claimed_slots = manager.claimed_slots(room_code)
        pending = state.get("pendingDecisions", {})
        if not claimed_slots or not claimed_slots.issubset({int(slot) for slot in pending}):
            return
        _round_processing.add(room_code)
        timeout_task = _round_timeout_tasks.pop(room_code, None)
        if timeout_task is not None and timeout_task is not asyncio.current_task():
            timeout_task.cancel()
        decisions = {slot: pending[str(slot)] for slot in claimed_slots}
        state["pendingDecisions"] = {}
        await save_room(room_code, state)

    asyncio.create_task(_run_round(room_code, decisions))


async def _round_timeout(room_code: str, round_number: int) -> None:
    await asyncio.sleep(ROUND_TIMEOUT_SECONDS)
    async with get_room_lock(room_code):
        if room_code in _round_processing:
            return
        state = get_room(room_code)
        if state is None or int(state.get("roundNumber", 1)) != round_number:
            return
        pending = state.setdefault("pendingDecisions", {})
        for slot in manager.claimed_slots(room_code):
            pending.setdefault(str(slot), {
                "text": "wait and observe",
                "target": None,
                "round": round_number,
            })
        await save_room(room_code, state)
    await _maybe_start_round(room_code)


async def _run_round(room_code: str, decisions: dict[int, str]) -> None:
    try:
        for slot in sorted(decisions):
            websocket = manager.connection_for_slot(room_code, slot)
            if websocket is not None:
                await _process_decision(
                    room_code,
                    websocket,
                    {
                        "type": "decision",
                        "slot": slot,
                        "text": decisions[slot].get("text", "wait and observe"),
                        "target_slot": decisions[slot].get("target"),
                        "defer_period_tick": True,
                    },
                )

        await _advance_policy_period(room_code)

        async with get_room_lock(room_code):
            state = get_room(room_code)
            if state is not None:
                state["roundNumber"] = int(state.get("roundNumber", 1)) + 1
                await save_room(room_code, state)
                await manager.broadcast(room_code, {
                    "type": "round_complete",
                    "roundNumber": state["roundNumber"],
                    "state": state,
                })
    finally:
        _round_processing.discard(room_code)
        await _maybe_start_round(room_code)


async def _advance_policy_period(room_code: str) -> None:
    async with get_room_lock(room_code):
        state = get_room(room_code)
        if state is None:
            return
        world_state = WorldState.from_room_state(state)
        commitment_days = max(
            [
                int(player.get("currentPolicy", {}).get("commitmentDays", STRATEGIC_PERIOD_DAYS))
                for player in world_state.players
                if isinstance(player, dict) and isinstance(player.get("currentPolicy"), dict)
            ]
            or [STRATEGIC_PERIOD_DAYS]
        )
        tick = WorldTick(seed=_seed_from_code(room_code))
        queue = WorldEventQueue()
        world_state = tick.advance(world_state, queue, hours=24 * commitment_days, room_state=state)
        state["world_state"] = world_state.to_dict()

        world_context = state.setdefault("worldContext", [])
        while queue.has_pending_events():
            event = queue.next_event()
            if event:
                world_context.append({"day": event["day"], "summary": event["summary"]})
        state["worldContext"] = world_context[-8:]

        period_event = tick.last_period_outcome
        if period_event:
            state["strategicPeriodOutcome"] = copy.deepcopy(period_event)
            for player in state["players"].values():
                player.setdefault("log", []).append({
                    "role": "system",
                    "text": f"[Strategic period] Outcome: {period_event['outcomeTier']} after {period_event['daysResolved']} days.",
                })
        await save_room(room_code, state)


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

    should_start = False
    async with get_room_lock(room_code):
        state = get_room(room_code)
        if state is None:
            return
        pending = state.setdefault("pendingDecisions", {})
        if str(slot) in pending:
            await websocket.send_json({"type": "error", "message": "You already submitted this round."})
            return
        pending[str(slot)] = {
            "text": text,
            "target": target_slot,
            "round": int(state.get("roundNumber", 1)),
        }
        claimed_slots = manager.claimed_slots(room_code)
        waiting_on = sorted(claimed_slots - {int(item) for item in pending})
        await save_room(room_code, state)
        await manager.broadcast(room_code, {
            "type": "round_status",
            "roundNumber": state.get("roundNumber", 1),
            "waitingOn": waiting_on,
            "pendingDecisions": pending,
        })
        should_start = bool(claimed_slots) and not waiting_on and room_code not in _round_processing
        if should_start:
            _round_processing.add(room_code)
            timeout_task = _round_timeout_tasks.pop(room_code, None)
            if timeout_task is not None:
                timeout_task.cancel()
            decisions = {item: pending[str(item)] for item in claimed_slots}
            state["pendingDecisions"] = {}
            await save_room(room_code, state)

    if should_start:
        asyncio.create_task(_run_round(room_code, decisions))
    elif room_code not in _round_timeout_tasks and room_code not in _round_processing:
        round_number = int(state.get("roundNumber", 1))
        _round_timeout_tasks[room_code] = asyncio.create_task(_round_timeout(room_code, round_number))


async def _process_decision(room_code: str, websocket: WebSocket, msg: dict):
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

    snapshot = get_room(room_code)
    if snapshot is None:
        return

    actor_id = f"player_{slot}"
    snapshot_world = WorldState.from_room_state(snapshot)
    snapshot_player = snapshot["players"][str(slot)]
    ai_parse_prompt = _build_ai_action_parse_prompt(
        slot, actor_id, snapshot, text, interaction_target=target_slot
    )
    action = None
    parse_error = None
    parse_raw_response = None
    if text.casefold() == "wait and observe":
        action = StructuredAction(
            actionId=f"wait-and-observe-{slot}-{snapshot.get('roundNumber', 1)}",
            actionType="resource_policy",
            actor=actor_id,
            target="settlement",
            parameters={
                "policy": {
                    "focus": "conservation and observation",
                    "commitmentDays": STRATEGIC_PERIOD_DAYS,
                    "startDay": snapshot_world.currentDay,
                },
            },
        )
    else:
        try:
            raw_parse = await call_narrator(ai_parse_prompt, "", purpose="parse")
            payload = _parse_json_payload(raw_parse)
            action, parse_error = _build_action_from_payload(payload)
        except RuntimeError as exc:
            parse_error = str(exc)
            parse_raw_response = getattr(exc, "raw_response", None)
            append_debug_log(f"AI parse error for room={room_code} slot={slot}: {parse_error}")

    fallback_used = False
    fallback_error = None
    if action is None:
        action = StructuredAction(
            actionId=f"policy-fallback-{slot}-{snapshot.get('roundNumber', 1)}",
            actionType="resource_policy",
            actor=actor_id,
            target="settlement",
            parameters={
                "policy": {
                    "focus": text,
                    "commitmentDays": STRATEGIC_PERIOD_DAYS,
                    "startDay": snapshot_world.currentDay,
                },
            },
        )
        fallback_used = True

    summary_text = None
    next_turn = int(snapshot_player.get("turnCount", 0)) + 1
    if next_turn % 10 == 0 and snapshot_player.get("recap"):
        summary_prompt = build_story_summary_prompt(
            slot,
            snapshot_player.get("storySummary", ""),
            snapshot_player["recap"][:-6] or snapshot_player["recap"],
        )
        try:
            candidate_summary = await call_narrator(summary_prompt, "", purpose="summary")
            if candidate_summary and not candidate_summary.lower().startswith("fallback mode:"):
                summary_text = candidate_summary.strip()
        except RuntimeError as exc:
            append_debug_log(f"Story summary error for room={room_code} slot={slot}: {exc}")

    narration_room_state = snapshot
    if summary_text:
        narration_room_state = json.loads(json.dumps(snapshot))
        narration_room_state["players"][str(slot)]["storySummary"] = summary_text
        narration_room_state["worldEra"] = summary_text

    preview_result = SimulationEngine(seed=_seed_from_code(room_code)).apply(snapshot_world, action)
    period_preview = None
    if action.actionType in SimulationEngine.POLICY_ACTIONS:
        policy = action.parameters.get("policy", {})
        commitment_days = max(
            1,
            int(policy.get("commitmentDays", STRATEGIC_PERIOD_DAYS))
            if isinstance(policy, dict)
            else STRATEGIC_PERIOD_DAYS,
        )
        preview_world = WorldState.from_dict(preview_result.updatedWorldState)
        preview_tick = WorldTick(seed=_seed_from_code(room_code))
        period_preview = preview_tick.advance(
            preview_world,
            WorldEventQueue(),
            hours=24 * commitment_days,
        )
        narration_room_state = copy.deepcopy(narration_room_state)
        narration_room_state["strategicPeriodOutcome"] = copy.deepcopy(preview_tick.last_period_outcome)
        narration_room_state["worldContext"] = [
            *narration_room_state.get("worldContext", [])[-5:],
            *[
                {"day": event.get("day"), "summary": event.get("summary")}
                for event in period_preview.activeEvents[-4:]
                if event.get("type") in {"faction_action", "food_shortage", "tick"}
            ],
        ]
    ai_narration_prompt = _build_ai_narration_prompt(
        slot, narration_room_state, action, preview_result, text, interaction_target=target_slot
    )

    narrative = None
    narration_payload = None
    narration_error = None
    for attempt in range(2):
        try:
            raw_narrative = await call_narrator(ai_narration_prompt, "", purpose="narration")
            narrative, narration_payload = parse_reply(raw_narrative)
            if not narrative:
                narrative = raw_narrative.strip()
            break
        except RuntimeError as exc:
            narrative = None
            narration_payload = None
            narration_error = exc
            if attempt == 0:
                await asyncio.sleep(1)

    if narration_error is not None and not narrative:
        append_debug_log(f"AI narration error for room={room_code} slot={slot}: {narration_error}")

    async with get_room_lock(room_code):
        state = get_room(room_code)
        if state is None:
            return
        if summary_text:
            state["worldEra"] = summary_text
        player_state = state["players"][str(slot)]
        player_state.setdefault("pendingEvents", [])
        player_state["pendingEvents"] = []
        player_state["log"].append({"role": "player", "text": text})
        current_turn = int(player_state.get("turnCount", 0)) + 1
        player_state["turnCount"] = current_turn
        if summary_text and current_turn == next_turn and current_turn > int(player_state.get("storySummaryTurn", 0)):
            player_state["storySummary"] = summary_text
            player_state["storySummaryTurn"] = current_turn

        if target_slot is not None:
            state.setdefault("interactions", [])
            state["interactions"].append({"actor": slot, "target": target_slot, "text": text})
            state["interactions"] = state["interactions"][-20:]
            player_state["log"].append({
                "role": "system",
                "text": f"You attempt to interact with Player {target_slot}.",
            })

        unknown_note = check_unknown_mention(text)
        if unknown_note:
            player_state["log"].append({"role": "system", "text": unknown_note})
        if parse_raw_response:
            player_state["log"].append({"role": "system", "text": f"RAW_LLM_RESPONSE: {parse_raw_response}"})
        if fallback_used:
            player_state["log"].append({"role": "system", "text": "AI parse didn't match the expected action schema; using a generic policy fallback."})

        world_state = WorldState.from_room_state(state)
        previous_day = world_state.currentDay
        result = SimulationEngine(seed=_seed_from_code(room_code)).apply(world_state, action)

        if not narrative:
            player_known_info = {
                "location": player_state["facts"].get("location"),
                "knownNearbyLocations": player_state["facts"].get("knownNearbyLocations", []),
                "recap": player_state.get("recap", []),
                "relationships": world_state.relationships,
            }
            narrative = narrate_simulation_result(world_state.to_dict(), action, result, player_known_info)
            if narration_error is not None:
                player_state["log"].append({
                    "role": "system",
                    "text": "AI narration failed after retry; using deterministic fallback.",
                })
                player_state["log"].append({
                    "role": "system",
                    "text": "Your stats didn't update this turn — the narrator was unavailable. Try your action again.",
                })

        player_state["log"].append({
            "role": "narrator",
            "text": narrative,
            "rejected": result.rejected,
            "rejectionReason": result.rejectionReason,
            "physicallyImpossible": result.physicallyImpossible,
        })
        merge_state(state, slot, narration_payload)

        if target_slot is not None:
            relationship_event = _RELATIONSHIP_EVENT_BY_ACTION.get(action.actionType, "order")
            relationship = _relationship_system.apply_event(
                result.updatedWorldState,
                actor_id,
                f"player_{target_slot}",
                relationship_event,
                severity=max(1, int(result.encounterProbability * 3)),
            )
            if relationship:
                player_state["log"].append({
                    "role": "system",
                    "text": (
                        f"Player {target_slot}'s trust in you shifts to "
                        f"{relationship.get('trust', 0.5):.2f} "
                        f"(loyalty {relationship.get('loyalty', 0.5):.2f}, "
                        f"resentment {relationship.get('resentment', 0.0):.2f})."
                    ),
                })

        state["world_state"] = result.updatedWorldState
        resolved_target_slot = _resolved_target_slot(action, target_slot)
        if resolved_target_slot is not None and not result.rejected:
            target_player = state["players"].get(str(resolved_target_slot))
            if target_player is not None:
                target_player.setdefault("pendingEvents", []).append({
                    "from": slot,
                    "summary": f"Player {slot} acts regarding you and says: {text}",
                })
                target_player["pendingEvents"] = target_player["pendingEvents"][-20:]
        new_day = state["world_state"].get("currentDay", 1)
        policy_days = 0
        if action.actionType in SimulationEngine.POLICY_ACTIONS:
            policy = action.parameters.get("policy", {})
            if isinstance(policy, dict):
                policy_days = max(1, int(policy.get("commitmentDays", STRATEGIC_PERIOD_DAYS)))
        if (new_day > previous_day or policy_days) and not msg.get("defer_period_tick"):
            days_passed = policy_days or (new_day - previous_day)
            tick = WorldTick(seed=_seed_from_code(room_code))
            queue = WorldEventQueue()
            ticked_world = WorldState.from_dict(state["world_state"])
            ticked_world = tick.advance(ticked_world, queue, hours=24 * days_passed, room_state=state)
            state["world_state"] = ticked_world.to_dict()

            world_context = state.setdefault("worldContext", [])
            while queue.has_pending_events():
                event = queue.next_event()
                if event:
                    world_context.append({"day": event["day"], "summary": event["summary"]})
            state["worldContext"] = world_context[-8:]

            notable_events = [
                event for event in ticked_world.activeEvents[-6:]
                if event.get("type") in {"food_shortage", "faction_action"}
            ]
            for room_player in state["players"].values():
                for event in notable_events:
                    room_player["log"].append({"role": "system", "text": f"[World] {event.get('summary')}"})

        await save_room(room_code, state)
        await broadcast_state(room_code, state)
