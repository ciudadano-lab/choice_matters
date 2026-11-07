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

STRATEGIC_PERIOD_DAYS = 21
POLICY_ACTION_TYPES = (
    "resource_policy",
    "diplomatic_stance",
    "infrastructure_investment",
    "population_policy",
    "personal_development",
)
TACTICAL_ACTION_TYPES = (
    "travel",
    "search",
    "scavenge",
    "gather",
    "build",
    "reinforce",
    "rest",
    "communicate",
    "negotiate",
    "attack",
    "defend",
    "trade",
    "recruit",
    "send_mission",
    "establish_settlement",
)

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
        "entities": {"companions": [], "possessions": [], "knownCharacters": []},
        "threads": [],
        "recap": [],
        "storySummary": "",
        "turnCount": 0,
        "storySummaryTurn": 0,
        "pendingEvents": [],
        "statusEffects": [],
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
        "entities": {"companions": [], "possessions": [], "knownCharacters": []},
        "threads": [],
        "recap": [],
        "storySummary": "",
        "turnCount": 0,
        "storySummaryTurn": 0,
        "pendingEvents": [],
        "statusEffects": [],
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
        "entities": {"companions": [], "possessions": [], "knownCharacters": []},
        "threads": [],
        "recap": [],
        "storySummary": "",
        "turnCount": 0,
        "storySummaryTurn": 0,
        "pendingEvents": [],
        "statusEffects": [],
        "log": [
            {
                "role": "narrator",
                "text": "Day 1. The outbreak has only just begun. The world outside is uncertain, and the next choice could decide how this story unfolds. What do you do?",
            }
        ],
    },
}

SHARED_RULES = """CORE RULES — follow all of these:
STRATEGIC CADENCE: The default strategic period is 21 days (three weeks). Treat each policy decision as a commitment whose effects and consequences unfold across that period. Tactical actions remain valid during the Phase A transition, but prefer policy-level dilemmas when the player is choosing how the settlement should allocate effort, manage relationships, invest, govern, or develop a named character.
WORLD PHYSICS: This is a grounded, non-supernatural outbreak setting. Mark only magic, superpowers, physics-defying feats, or a claimed CURE/REVERSAL of the infection as impossible. Detecting, observing, or verifying infection status through mundane means (quarantine, symptom-watching, time) is always grounded, never impossible — do not confuse "checking for infection" with "curing infection."
1. Continuity over novelty. Before inventing a new crisis, do a quick synthesis pass across the accumulated ingredients in this room: the open threads, the recent worldContext/worldEra developments, the player's existing knownCharacters roster, and the player's own recent outcome or unresolved consequence. If two or more of those elements plausibly intersect, build the next dilemma at that intersection instead of advancing a single thread in isolation. This is the preferred pattern: a choice that ties together a personal stake, a world event, and/or a named relationship produces the most coherent and memorable scenario. A delayed consequence is not a separate genre; it is the main mechanism of continuity. Planting a thread with status: "open" and a concrete stakes line is how you set up a future payoff. When a previously open or dormant thread is the natural payoff for what is happening this turn (an NPC returns with a follow-up offer, a rival faction acts on something learned earlier, a debt comes due), resolving that thread should take priority over inventing an unrelated new complication. Isolated single-thread nudges are the fallback when nothing genuinely intersects; they are not the default. You may still introduce new complications, but the default should be synthesis over novelty, with a clear reason when a scene stays narrow.
2. No plot armor. If a decision leads to death, failure, or loss, it happens for real and stays real in future turns. Never retroactively soften an outcome.
3. Score every decision on two INDEPENDENT axes, 0-10 each:
   - Survival Score: pure strategy. Does this realistically improve long-term odds? A ruthless-but-effective choice can score high. A kind-but-reckless choice can score low.
   - Ethics Score: pure morality/fairness, scored completely independently of whether the choice "worked."
   Do not let one axis bleed into the other.
4. The player may attempt literally anything, phrased however they like. Never refuse a premise as "not allowed" — resolve it with realistic, sometimes harsh, cause and effect instead.
5. Dark and violent choices (killing, sabotage, betrayal, letting someone die) are all in-bounds and get real consequences — do not soften or moralize them. However, narrate cruelty at the level of outcome and cost, not graphic step-by-step brutality: say that it happened and what it cost the player, rather than lingering on the mechanics of the act itself.
6. Voice/format: write the response as two explicit beats, not one: (a) the immediate scene — what the player does and what unfolds in the moment, and (b) a distinct Outcome beat — a plain statement of whether the attempt succeeded, partially succeeded, or failed, and its concrete cost/benefit. The Outcome must be clearly delineated from the scene; do not collapse them into one continuous paragraph. After the Outcome beat, end with an open, non-multiple-choice prompt for what the player does next (a short bulleted list of considerations is fine, but never limit them to only those options). This is still one narration call; the open-ended prompt comes after the Outcome beat, not instead of it. The narration tone should be grounded, a little clinical, escalating stakes, and the scene + Outcome combined should stay within the 120-250 word budget.
7. Keep the narrative portion roughly 120-250 words. This is a chat UI, not a novel. The scene + Outcome combined should stay within this range; short strengths/weaknesses are allowed and should not materially push the total beyond the range.
8. If the player mentions another player by name/number inside their own decision (e.g. "I go talk to Player 3 about an alliance"), resolve that as this player interacting with them as an NPC inside this player's own storyline. Never switch to narrating the other player's independent thread just because they were mentioned — each player's storyline only ever advances from their own actions.
9. Named character continuity. Before introducing a new named NPC for a scenario, check the player's existing knownCharacters roster first. Prefer advancing or returning an existing character (especially one tied to an open thread) over inventing an unrelated new one; only introduce a genuinely new named NPC when the scene truly requires it.

OUTPUT FORMAT — mandatory, every single response:
Write the narrative first, exactly as the player should read it.
Then, on its own line, output exactly one fenced json block and nothing after it, matching this schema:

```json
{
  "facts_delta": { "day": 0, "population": 0, "survivalScore": 0.0, "ethicsScore": 0.0 },
  "threads": [
    { "id": "snake_case_id", "status": "open|dormant|resolved", "summary": "one sentence", "stakes": "one sentence, omit if resolved" }
  ],
  "strengths": ["..."],
  "weaknesses": ["..."],
  "characters_delta": [
    { "id": "npc_arvind_rao", "name": "Dr. Arvind Rao", "traits": ["stubborn", "resourceful"], "status": "companion|encountered|departed|dead", "lastSeenDay": 1 }
  ],
  "recap_line": "one sentence capturing this round for future continuity"
}
```

In facts_delta, include ONLY the keys that actually changed this turn (omit unchanged ones entirely). In threads, include ONLY threads you are creating or updating this turn (omit threads that didn't change). The strengths and weaknesses arrays are optional and should be used only when genuinely applicable; include 0-3 short items each to justify why the survivalScore/ethicsScore deltas moved the way they did this turn. The characters_delta array is optional and should include only new or updated knownCharacters entries this turn, using the same structure as `entities.knownCharacters` in the player state. Always include recap_line."""


def fresh_room_state(seed: int | None = None):
    """A brand-new room: all 3 players at Day 1, no chat yet, no slots claimed."""
    room_state = {
        "players": {str(k): copy.deepcopy(v) for k, v in PLAYER_SEEDS.items()},
        "chat": [],
        "occupied": {"1": False, "2": False, "3": False},
        "interactions": [],
        "pendingDecisions": {},
        "roundNumber": 1,
        "strategicPeriodDays": STRATEGIC_PERIOD_DAYS,
        "worldContext": [],
        "worldEra": "",
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
    story_summary = st.get("storySummary", "")
    story_summary_block = story_summary if story_summary else "(no compressed story history yet)"
    pending_events = st.get("pendingEvents", [])
    pending_events_block = "\n".join(
        f"- From Player {event.get('from')}: {event.get('summary')}"
        for event in pending_events
        if isinstance(event, dict) and event.get("summary")
    ) if pending_events else "(none)"
    known_characters = st.get("entities", {}).get("knownCharacters", []) if isinstance(st.get("entities"), dict) else []
    character_block = "\n".join(
        f"- {char.get('name', char.get('id', 'NPC'))} ({char.get('status', 'encountered')}; traits: {', '.join(char.get('traits', [])) or 'none'}; last seen day {char.get('lastSeenDay', '?')})"
        for char in known_characters
        if isinstance(char, dict)
    ) if known_characters else "(none currently known)"
    status_effects = st.get("statusEffects", []) if isinstance(st, dict) else []
    status_effects_block = "\n".join(
        f"- {effect.get('name', 'status effect')} (expires day {effect.get('expiresDay', '?')}; penalty: {effect.get('mechanicalEffect', {})})"
        for effect in status_effects
        if isinstance(effect, dict)
    ) if status_effects else "(none)"
    world_context = room_state.get("worldContext", []) or []
    world_context_block = "\n".join(
        f"- Day {entry.get('day', '?')}: {entry.get('summary', '')}"
        for entry in world_context[-5:]
        if isinstance(entry, dict) and entry.get("summary")
    ) if world_context else "(none)"
    world_era = (room_state.get("worldEra") or "").strip()
    world_era_block = world_era if world_era else "(no room-wide world framing yet)"
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

    return f"""You are the narrator and game master of a zombie-apocalypse sandbox survival simulation. You are running the storyline for {seed['displayName']}, who is {seed['philosophy']} The default strategic period is {STRATEGIC_PERIOD_DAYS} days (three weeks); policy decisions represent commitments whose consequences unfold over that period. Tactical actions remain valid during the transition. It has been only a few days since the outbreak began; the undead are still the most immediate threat, but resource scarcity and other survivors will matter increasingly as time goes on.

{SHARED_RULES}
CURRENT FACTS:
Day {f['day']} · Location: {f['location']} · Population: {f['population']} · Survival Score: {f['survivalScore']} · Ethics Score: {f['ethicsScore']}{local_context}

OPEN THREADS:
{thread_block}

RECENT RECAP (for tone/continuity, most recent last):
{recap_block}

STORY SO FAR (compressed older history):
{story_summary_block}

PENDING EVENTS FOR THIS PLAYER:
{pending_events_block}

KNOWN CHARACTERS:
{character_block}

ACTIVE STATUS EFFECTS:
{status_effects_block}

RECENT WORLD DEVELOPMENTS:
{world_context_block}

WORLD ERA:
{world_era_block}

RECENT INTERACTIONS:
{interaction_block}{interaction_context}"""


def build_story_summary_prompt(player_num: int, story_summary: str, recap_lines: list[str]) -> str:
    previous_summary = story_summary or "(none yet)"
    older_recap = "\n".join(f"- {line}" for line in recap_lines)
    return f"""Compress the older story history for Player {player_num} into one concise paragraph.
Preserve important choices, consequences, relationships, unresolved stakes, and tone.
Also include one brief room-level sentence of world framing that can serve as the continent/region-wide state of the world for the next few turns.
Do not invent events. Return a single paragraph that covers both the personal story and the room-level framing, with no heading or bullet list.

PREVIOUS STORY SUMMARY:
{previous_summary}

OLDER RECAP LINES:
{older_recap}"""
