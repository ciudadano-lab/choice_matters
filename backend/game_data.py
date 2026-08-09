"""
Seed data and prompt-building logic for the zombie sandbox.
All previous campaign history (Rounds 1-11) has been wiped per request —
every player now starts fresh at Day 1.
"""

import copy
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import WorldState, assign_spawn_to_player

VALID_PLAYERS = [1, 2, 3]

PLAYER_SEEDS = {
    1: {
        "displayName": "Survivor",
        "philosophy": "a survivor trying to make sense of the outbreak one decision at a time.",
        "facts": {
            "day": 1,
            "location": "Unknown shelter",
            "population": 1,
            "survivalScore": 5.0,
            "ethicsScore": 5.0,
        },
        "threads": [],
        "recap": [],
        "log": [
            {
                "role": "narrator",
                "text": "Day 1. The outbreak has only just begun. The world outside is uncertain, and the next choice could decide how this story unfolds. What do you do?",
            }
        ],
    },
    2: {
        "displayName": "Survivor",
        "philosophy": "a survivor trying to make sense of the outbreak one decision at a time.",
        "facts": {
            "day": 1,
            "location": "Unknown shelter",
            "population": 1,
            "survivalScore": 5.0,
            "ethicsScore": 5.0,
        },
        "threads": [],
        "recap": [],
        "log": [
            {
                "role": "narrator",
                "text": "Day 1. The outbreak has only just begun. The world outside is uncertain, and the next choice could decide how this story unfolds. What do you do?",
            }
        ],
    },
    3: {
        "displayName": "Survivor",
        "philosophy": "a survivor trying to make sense of the outbreak one decision at a time.",
        "facts": {
            "day": 1,
            "location": "Unknown shelter",
            "population": 1,
            "survivalScore": 5.0,
            "ethicsScore": 5.0,
        },
        "threads": [],
        "recap": [],
        "log": [
            {
                "role": "narrator",
                "text": "Day 1. The outbreak has only just begun. The world outside is uncertain, and the next choice could decide how this story unfolds. What do you do?",
            }
        ],
    },
}

SHARED_RULES = """CORE RULES — follow all of these:
1. Continuity over novelty. Before inventing a new crisis, look at the OPEN THREADS provided below. At least one beat of your scenario or outcome should advance, complicate, or resolve an existing thread where it plausibly fits. You may still introduce new complications, but never as a replacement for following through on what's already open.
2. No plot armor. If a decision leads to death, failure, or loss, it happens for real and stays real in future turns. Never retroactively soften an outcome.
3. Score every decision on two INDEPENDENT axes, 0-10 each:
   - Survival Score: pure strategy. Does this realistically improve long-term odds? A ruthless-but-effective choice can score high. A kind-but-reckless choice can score low.
   - Ethics Score: pure morality/fairness, scored completely independently of whether the choice "worked."
   Do not let one axis bleed into the other.
4. The player may attempt literally anything, phrased however they like. Never refuse a premise as "not allowed" — resolve it with realistic, sometimes harsh, cause and effect instead.
5. Dark and violent choices (killing, sabotage, betrayal, letting someone die) are all in-bounds and get real consequences — do not soften or moralize them. However, narrate cruelty at the level of outcome and cost, not graphic step-by-step brutality: say that it happened and what it cost the player, rather than lingering on the mechanics of the act itself.
6. Voice/format: write a scenario or outcome in the tone of a survival-simulation log — grounded, a little clinical, escalating stakes. End your narrative with an open, non-multiple-choice prompt for what the player does next (a short bulleted list of considerations is fine, but never limit them to only those options).
7. Keep the narrative portion roughly 120-250 words. This is a chat UI, not a novel.
8. If the player mentions another player by name/number inside their own decision (e.g. "I go talk to Player 3 about an alliance"), resolve that as this player interacting with them as an NPC inside this player's own storyline. Never switch to narrating the other player's independent thread just because they were mentioned — each player's storyline only ever advances from their own actions.

OUTPUT FORMAT — mandatory, every single response:
Write the narrative first, exactly as the player should read it.
Then, on its own line, output exactly one fenced json block and nothing after it, matching this schema:

```json
{
  "facts_delta": { "day": 0, "population": 0, "survivalScore": 0.0, "ethicsScore": 0.0 },
  "threads": [
    { "id": "snake_case_id", "status": "open|dormant|resolved", "summary": "one sentence", "stakes": "one sentence, omit if resolved" }
  ],
  "recap_line": "one sentence capturing this round for future continuity"
}
```

In facts_delta, include ONLY the keys that actually changed this turn (omit unchanged ones entirely). In threads, include ONLY threads you are creating or updating this turn (omit threads that didn't change). Always include recap_line."""


def fresh_room_state(seed: int | None = None):
    """A brand-new room: all 3 players at Day 1, no chat yet, no slots claimed."""
    room_state = {
        "players": {str(k): copy.deepcopy(v) for k, v in PLAYER_SEEDS.items()},
        "chat": [],
        "occupied": {"1": False, "2": False, "3": False},
        "interactions": [],
    }
    world_state = WorldState.from_room_state(room_state, seed=seed)
    for slot in [1, 2, 3]:
        player = room_state["players"][str(slot)]
        world_player = next((entry for entry in world_state.players if entry.get("id") == f"player_{slot}"), None)
        if world_player is None:
            continue
        assign_spawn_to_player(player, seed, slot, world_state.locations)
    room_state["world_state"] = world_state.to_dict()
    return room_state


def build_system_prompt(player_num: int, room_state: dict, interaction_target: int | None = None) -> str:
    seed = PLAYER_SEEDS[player_num]
    st = room_state["players"][str(player_num)]
    f = st["facts"]
    open_threads = [t for t in st["threads"] if t["status"] != "resolved"]
    if open_threads:
        thread_block = "\n".join(
            f"- [{t['status'].upper()}] {t['id']}: {t['summary']}" + (f" Stakes: {t['stakes']}" if t.get("stakes") else "")
            for t in open_threads
        )
    else:
        thread_block = "(none currently open)"
    recap_block = "\n".join(st["recap"][-6:]) if st["recap"] else "(campaign just started, no history yet)"
    interaction_block = "(no direct player interactions yet)"
    recent_interactions = room_state.get("interactions", [])[-6:]
    if recent_interactions:
        interaction_block = "\n".join(
            f"- Player {entry['actor']} attempted to interact with Player {entry['target']}: {entry['text']}"
            for entry in recent_interactions
        )

    interaction_context = ""
    if interaction_target is not None:
        interaction_context = f"\nCURRENT INTERACTION: This turn, Player {player_num} is attempting to interact directly with Player {interaction_target}. Treat that as a meaningful social event that can change trust, tension, alliances, or conflict in the story."

    local_context = ""
    nearby_locations = f.get("knownNearbyLocations") or []
    if nearby_locations:
        nearby_text = ", ".join(nearby_locations)
        local_context = f"\nLOCAL CONTEXT: The survivor knows the surrounding area includes {nearby_text}."

    return f"""You are the narrator and game master of a zombie-apocalypse sandbox survival simulation. You are running the storyline for {seed['displayName']}, who is {seed['philosophy']} It has been only a few days since the outbreak began; the undead are still the most immediate threat, but resource scarcity and other survivors will matter increasingly as time goes on.

{SHARED_RULES}
CURRENT FACTS:
Day {f['day']} · Location: {f['location']} · Population: {f['population']} · Survival Score: {f['survivalScore']} · Ethics Score: {f['ethicsScore']}{local_context}

OPEN THREADS:
{thread_block}

RECENT RECAP (for tone/continuity, most recent last):
{recap_block}

RECENT INTERACTIONS:
{interaction_block}{interaction_context}"""
