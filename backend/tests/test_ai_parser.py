from game_data import build_system_prompt, fresh_room_state
from main import _build_action_from_payload, _build_ai_action_parse_prompt, _parse_json_payload, _resolved_target_slot, parse_reply


def test_phase_a_prompt_exposes_policy_taxonomy_and_strategic_cadence():
    room_state = fresh_room_state(seed=404)

    prompt = _build_ai_action_parse_prompt(1, "player_1", room_state, "Set priorities for the settlement")

    assert "resource_policy|diplomatic_stance|infrastructure_investment|population_policy|personal_development" in prompt
    assert "21 days (three weeks)" in prompt
    assert '"policy"' in prompt
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
