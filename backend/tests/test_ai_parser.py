import copy
import json

from game_data import build_system_prompt, fresh_room_state
import main
from main import (
    _build_action_from_payload,
    _build_ai_action_parse_prompt,
    _log_player_command_parse,
    _parse_json_payload,
    _resolved_target_slot,
    parse_reply,
)


def test_phase_a_prompt_exposes_policy_taxonomy_and_strategic_cadence():
    room_state = fresh_room_state(seed=404)

    prompt = _build_ai_action_parse_prompt(1, "player_1", room_state, "Set priorities for the settlement")

    assert "resource_policy|diplomatic_stance|infrastructure_investment|population_policy|personal_development" in prompt
    assert "21 days (three weeks)" in prompt
    assert '"policy"' in prompt
    assert '"planFeatures"' in prompt
    assert "Facts only" in prompt
    assert "Do NOT judge or score the plan" in prompt
    assert "targetLocationIds must come from the available location ids" in prompt
    assert '"pivotalTiming": "immediate|culmination"' in prompt
    assert "When ambiguous, use `culmination`" in prompt
    assert "travel|search|gather" not in prompt


def test_phase_a_policy_payload_is_preserved_in_action_parameters():
    action, error = _build_action_from_payload({
        "actionId": "policy-1",
        "actionType": "resource_policy",
        "actor": "player_1",
        "target": "settlement",
        "parameters": {"focus": "food production"},
        "policy": {"focus": "food production", "commitmentDays": 21},
        "timeCost": 21,
        "resourceCost": {},
    })

    assert error is None
    assert action.actionType == "resource_policy"
    assert action.parameters["policy"]["commitmentDays"] == 21


def test_policy_action_type_is_normalized_before_validation():
    action, error = _build_action_from_payload({
        "actionId": "policy-normalized",
        "actionType": " Diplomatic_Stance ",
        "actor": "player_1",
        "target": "settlement",
        "policy": {"focus": "alliance", "commitmentDays": 21},
        "timeCost": 21,
        "resourceCost": {},
    })

    assert error is None
    assert action.actionType == "diplomatic_stance"


def test_policy_narration_prompt_uses_strategic_period_context():
    room_state = fresh_room_state(seed=404)
    room_state["strategicPeriodOutcome"] = {
        "periodDays": 21,
        "daysResolved": 21,
        "outcomeTier": "success",
        "dailyOutcomes": [{"actionType": "gather", "outcomeTier": "success"}],
    }
    from simulation_engine import SimulationResult, StructuredAction

    action = StructuredAction(
        "policy-2",
        "resource_policy",
        "player_1",
        "settlement",
        parameters={"policy": {"focus": "scavenging", "commitmentDays": 21}},
    )
    result = SimulationResult(
        actionId="policy-2", actionType="resource_policy", timeConsumed=0,
        resourceChanges={}, playerStatusChanges=[], locationChanges=[],
        encounterProbability=0.0, encounterOccurred=False, encounterDetails={},
        factionRelationshipChanges=[], informationDiscovered=[], worldEventsTriggered=[],
        updatedWorldState=room_state["world_state"],
    )
    from main import _build_ai_narration_prompt

    prompt = _build_ai_narration_prompt(1, room_state, action, result, "Set a scavenging policy")

    assert "strategic policy commitment covering 21 days" in prompt
    assert "Prior strategic-period result" in prompt


def test_narration_prompt_explains_plan_reasons_without_numbers():
    room_state = fresh_room_state(seed=404)
    from simulation_engine import SimulationResult, StructuredAction

    action = StructuredAction(
        "policy-plan-reasons",
        "resource_policy",
        "player_1",
        "settlement",
        parameters={"policy": {"focus": "scouting", "commitmentDays": 21}},
    )
    result = SimulationResult(
        actionId="policy-plan-reasons", actionType="resource_policy", timeConsumed=0,
        resourceChanges={}, playerStatusChanges=[], locationChanges=[],
        encounterProbability=0.0, encounterOccurred=False, encounterDetails={},
        factionRelationshipChanges=[], informationDiscovered=[], worldEventsTriggered=[],
        updatedWorldState=room_state["world_state"],
        planReasons=[{
            "factor": "known_target_locations",
            "effect": "helps",
            "explanation": "You planned around places you actually know.",
        }],
    )

    from main import _build_ai_narration_prompt

    prompt = _build_ai_narration_prompt(1, room_state, action, result, "Plan the scouting")

    assert "planFactors: helps: You planned around places you actually know." in prompt
    assert "reflect why the plan helped or hurt without quoting numbers" in prompt


def test_player_command_parse_log_is_one_json_line_with_complete_details(monkeypatch):
    from simulation_engine import StructuredAction

    log_lines = []
    monkeypatch.setattr(main, "append_debug_log", log_lines.append)
    action = StructuredAction(
        "fallback-1",
        "resource_policy",
        "player_1",
        "settlement",
        parameters={"policy": {"focus": "barricade the door"}},
    )

    _log_player_command_parse(
        "ABCD",
        1,
        "  barricade the door\nplease  ",
        '{"invalid":\n"json"}',
        action,
        True,
        "AI parse returned invalid JSON payload.",
    )

    assert len(log_lines) == 1
    record = json.loads(log_lines[0])
    assert record["player_text"] == "  barricade the door\nplease  "
    assert record["raw_llm_output"] == '{"invalid":\n"json"}'
    assert record["action_used"]["actionType"] == "resource_policy"
    assert record["action_used"]["parameters"]["policy"]["focus"] == "barricade the door"
    assert record["fallback_used"] is True
    assert record["fallback_reason"] == "AI parse returned invalid JSON payload."


def test_system_prompt_includes_compressed_story_summary():
    room_state = fresh_room_state(seed=404)
    room_state["players"]["1"]["storySummary"] = "The shelter formed an uneasy alliance with the school."

    prompt = build_system_prompt(1, room_state)

    assert "STORY SO FAR (compressed older history):" in prompt
    assert "The shelter formed an uneasy alliance with the school." in prompt


def test_action_prompt_includes_pending_events_for_current_player():
    room_state = fresh_room_state(seed=404)
    room_state["players"]["2"]["pendingEvents"] = [{
        "from": 1,
        "summary": "Player 1 approaches you and says: We need to talk.",
    }]

    prompt = _build_ai_action_parse_prompt(2, "player_2", room_state, "I listen")

    assert "Pending events for this player:" in prompt
    assert "Player 1 approaches you and says: We need to talk." in prompt


def test_system_prompt_requires_explicit_outcome_and_evaluative_fields():
    room_state = fresh_room_state(seed=404)

    prompt = build_system_prompt(1, room_state)

    assert "two explicit beats" in prompt
    assert "Outcome" in prompt
    assert '"strengths": ["..."]' in prompt
    assert '"weaknesses": ["..."]' in prompt
    assert "status: \"open\"" in prompt
    assert "delayed consequence" in prompt
    assert '"characters_delta":' in prompt
    assert "new named NPC" in prompt


def test_system_prompt_includes_recent_world_developments():
    room_state = fresh_room_state(seed=404)
    room_state["worldContext"] = [
        {"day": 4, "summary": "Scavengers seized the old rail yard."},
        {"day": 5, "summary": "The clinic is now ringed by patrols."},
    ]

    prompt = build_system_prompt(1, room_state)

    assert "RECENT WORLD DEVELOPMENTS:" in prompt
    assert "Scavengers seized the old rail yard." in prompt
    assert "The clinic is now ringed by patrols." in prompt


def test_system_prompt_biases_toward_synthesis_across_accumulated_context():
    room_state = fresh_room_state(seed=404)
    room_state["players"]["1"]["entities"]["knownCharacters"] = [{
        "id": "npc_arvind_rao",
        "name": "Dr. Arvind Rao",
        "traits": ["resourceful"],
        "status": "companion",
        "lastSeenDay": 1,
    }]
    room_state["worldContext"] = [{"day": 2, "summary": "The river bridge was sabotaged."}]
    room_state["worldEra"] = "The city is under curfew and rumors of a crackdown circulate."

    prompt = build_system_prompt(1, room_state)

    assert "synthesis" in prompt.lower()
    assert "intersection" in prompt.lower()
    assert "knownCharacters" in prompt
    assert "worldContext" in prompt
    assert "worldEra" in prompt
    assert "fallback" in prompt.lower()


def test_resolved_target_slot_uses_explicit_target_or_entity_owner():
    from simulation_engine import StructuredAction

    explicit_action = StructuredAction("a", "communicate", "player_1", "camp")
    entity_action = StructuredAction(
        "b", "attack", "player_1", "player_2",
        parameters={"targetEntity": {"owner": "player_2", "type": "companion", "name": "Buddy"}},
    )

    assert _resolved_target_slot(explicit_action, 3) == 3
    assert _resolved_target_slot(entity_action, None) == 2


def test_narrator_world_block_is_player_scoped_and_uses_discovery_projection():
    from main import _build_ai_narration_prompt
    from simulation_engine import SimulationResult, StructuredAction
    from world_state import WorldState

    room = fresh_room_state(seed=404)
    world = WorldState.from_dict(room["world_state"])
    faction_ids = {item["id"] for item in world.factions}
    settlement_ids = [item["id"] for item in world.settlements]
    npc_ids = sorted(world.npcs)
    assert {"faction_survivors", "faction_watch"}.issubset(faction_ids)
    assert len(settlement_ids) >= 2 and len(npc_ids) >= 2

    world.playerKnownWorld["players"]["player_1"]["factions"] = {
        "faction_survivors": "known",
    }
    world.playerKnownWorld["players"]["player_1"]["settlements"] = {
        settlement_ids[0]: "met",
    }
    world.playerKnownWorld["players"]["player_2"]["factions"] = {
        "faction_watch": "known",
    }
    world.playerKnownWorld["players"]["player_2"]["settlements"] = {
        settlement_ids[1]: "known",
    }
    world.players[0]["entities"]["knownCharacters"] = [{
        "npcId": npc_ids[0],
        "knownFacts": {},
        "relationship": {},
    }]
    world.players[1]["entities"]["knownCharacters"] = [{
        "npcId": npc_ids[1],
        "knownFacts": {},
        "relationship": {},
    }]
    room["world_state"] = world.to_dict()

    action = StructuredAction("world-knowledge", "rest", "player_1", "camp")
    result = SimulationResult(
        actionId=action.actionId,
        actionType=action.actionType,
        timeConsumed=0,
        resourceChanges={},
        playerStatusChanges=[],
        locationChanges=[],
        encounterProbability=0.0,
        encounterOccurred=False,
        encounterDetails={},
        factionRelationshipChanges=[],
        informationDiscovered=[],
        worldEventsTriggered=[],
        updatedWorldState=room["world_state"],
    )

    def world_block(player_num: int) -> str:
        prompt = _build_ai_narration_prompt(
            player_num,
            room,
            action,
            result,
            "Wait and observe.",
        )
        return prompt.split("WORLD STATE YOU KNOW\n", 1)[1].split(
            "\nPLAYER INPUT:", 1
        )[0]

    player_one = world_block(1)
    player_two = world_block(2)
    assert "Local Survivors" in player_one
    assert "Local Survivors" not in player_two
    assert "Northside Watch" in player_two
    assert "Northside Watch" not in player_one
    assert world.settlements[0]["name"] in player_one
    assert world.settlements[1]["name"] in player_two
    assert world.npcs[npc_ids[0]]["name"] in player_one
    assert world.npcs[npc_ids[1]]["name"] in player_two
    assert "goals" not in player_one.lower() + player_two.lower()
    assert "population" not in player_one.lower() + player_two.lower()
    assert "strength" not in player_one.lower() + player_two.lower()
    assert not any(character.isdigit() for character in player_one + player_two)


def test_merge_state_validates_thread_links_and_keeps_old_threads_unchanged():
    from main import merge_state

    old_thread = {"id": "legacy_thread", "status": "open", "summary": "Old thread"}
    room = {
        "players": {
            "1": {
                "facts": {},
                "threads": [copy.deepcopy(old_thread)],
                "entities": {"knownCharacters": []},
            },
        },
        "world_state": {
            "players": [{"id": "player_1", "entities": {"knownCharacters": []}}],
            "npcs": {"npc_known": {"id": "npc_known", "status": "alive"}},
            "factions": [{"id": "faction_known"}],
            "settlements": [{"id": "settlement_known"}],
        },
    }

    merge_state(room, 1, {
        "threads": [{
            "id": "validated_thread",
            "status": "open",
            "summary": "Known entities only",
            "npcIds": ["npc_known", "npc_missing"],
            "factionIds": ["faction_known", "faction_missing"],
            "settlementId": "settlement_missing",
        }],
    })

    assert room["players"]["1"]["threads"][0] == old_thread
    validated = room["players"]["1"]["threads"][1]
    assert validated["npcIds"] == ["npc_known"]
    assert validated["factionIds"] == ["faction_known"]
    assert "settlementId" not in validated


def test_characters_delta_updates_only_player_npc_reference_and_not_dead_npc():
    from main import merge_state
    from world_state import normalize_room_state

    dead_npc = {
        "id": "npc_dead",
        "name": "Mara",
        "role": "leader",
        "status": "dead",
        "loyalty": 10,
        "location": "school",
    }
    room = {
        "players": {
            "1": {
                "facts": {},
                "threads": [],
                "entities": {"knownCharacters": []},
            },
        },
        "world_state": {
            "players": [{"id": "player_1", "entities": {"knownCharacters": []}}],
            "npcs": {"npc_dead": copy.deepcopy(dead_npc)},
        },
    }

    merge_state(room, 1, {
        "characters_delta": [
            {
                "npcId": "npc_dead",
                "knownFacts": {"met_at": "the clinic"},
                "relationship": {"trust": 0.7},
                "status": "alive",
                "health": 100,
                "location": "safehouse",
            },
            {"npcId": "npc_unknown", "knownFacts": {"invented": True}},
            {"id": "legacy_survivor", "name": "Legacy Survivor", "status": "encountered"},
        ],
    })

    expected_reference = {
        "npcId": "npc_dead",
        "knownFacts": {"met_at": "the clinic"},
        "relationship": {"trust": 0.7},
    }
    assert room["players"]["1"]["entities"]["knownCharacters"][0] == expected_reference
    assert len(room["players"]["1"]["entities"]["knownCharacters"]) == 2
    assert room["world_state"]["players"][0]["entities"]["knownCharacters"] == [expected_reference]
    assert room["world_state"]["npcs"]["npc_dead"] == dead_npc
    assert room["players"]["1"]["entities"]["knownCharacters"][1]["id"] == "legacy_survivor"

    legacy_room = fresh_room_state(seed=405)
    legacy_thread = {"id": "legacy", "status": "dormant", "summary": "Loaded unchanged"}
    legacy_room["players"]["1"]["threads"] = [legacy_thread]
    normalized = normalize_room_state(legacy_room)
    assert normalized["players"]["1"]["threads"] == [legacy_thread]


def test_action_payload_marks_impossible_claim_as_failed_attempt():
    action, error = _build_action_from_payload({
        "actionId": "magic-1",
        "actionType": "communicate",
        "actor": "player_1",
        "target": "self",
        "feasibility": "impossible",
        "timeCost": 2,
        "resourceCost": {"food": 99},
    })

    assert error is None
    assert action.actionType == "failed_attempt"
    assert action.parameters["physicallyImpossible"] is True
    assert action.resourceCost == {}


def test_parse_reply_handles_fenced_json_without_language_tag():
    text = "Here is the action\n```\n{\"actionId\": \"test\", \"actionType\": \"travel\", \"actor\": \"player_1\", \"target\": \"safehouse\", \"parameters\": {}, \"timeCost\": 1, \"resourceCost\": {}, \"risks\": [], \"prerequisites\": []}\n```"

    narrative, payload = parse_reply(text)

    assert narrative == "Here is the action"
    assert payload is not None
    assert payload["actionId"] == "test"
    assert payload["actionType"] == "travel"


def test_parse_json_payload_handles_uppercase_json_fence_and_prose():
    text = "I will do that.\n```JSON\n{\"actionId\": \"test\", \"actionType\": \"search\", \"actor\": \"player_1\", \"target\": \"warehouse\", \"parameters\": {}, \"timeCost\": 1, \"resourceCost\": {}, \"risks\": [], \"prerequisites\": []}\n```\nThanks!"

    payload = _parse_json_payload(text)

    assert payload is not None
    assert payload["actionType"] == "search"
    assert payload["target"] == "warehouse"
