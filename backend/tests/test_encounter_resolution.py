from types import SimpleNamespace

from encounter_resolution import apply_encounter_outcome, resolve_encounter
from narrator import narrate_simulation_result
from probability_engine import roll_value
from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState
from world_tick import WorldTick


def _state():
    return {
        "currentDay": 4,
        "currentTime": "22:00",
        "weather": "clear",
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 100,
            "stress": 10,
            "location": "school",
            "inventory": ["radio"],
            "entities": {"companions": []},
            "statusEffects": [],
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "dangerLevel": 0,
            "discovered": True,
        }],
        "resources": {"food": 3, "water": 2},
    }


def _action():
    return StructuredAction(
        actionId="encounter-resolution-test",
        actionType="search",
        actor="player_1",
        target="school",
    )


def _chain_result(plan_quality=0.0):
    return SimpleNamespace(
        encounterOccurred=True,
        planQuality=plan_quality,
    )


def test_encounter_outcome_is_deterministic_and_does_not_mutate_input():
    state = _state()
    before = {
        "health": state["players"][0]["health"],
        "resources": dict(state["resources"]),
        "inventory": list(state["players"][0]["inventory"]),
    }

    first = resolve_encounter(state, state["players"][0], _action(), _chain_result(), 42)
    second = resolve_encounter(state, state["players"][0], _action(), _chain_result(), 42)

    assert first == second
    assert first.roll == roll_value(
        42,
        "encounter-resolution-test",
        4,
        "22:00",
        "encounter_outcome",
        0,
    )
    assert first.probabilities == second.probabilities
    assert state["players"][0]["health"] == before["health"]
    assert state["resources"] == before["resources"]
    assert state["players"][0]["inventory"] == before["inventory"]


def test_plan_quality_shifts_a_fixed_encounter_toward_a_safer_outcome():
    state = _state()
    action = _action()
    seed = next(
        candidate
        for candidate in range(1000)
        if 0.66 <= roll_value(
            candidate,
            action.actionId,
            4,
            "22:00",
            "encounter_outcome",
            0,
        ) < 0.78
    )

    planned = resolve_encounter(
        state,
        state["players"][0],
        action,
        _chain_result(1.0),
        seed,
    )
    unprepared = resolve_encounter(
        state,
        state["players"][0],
        action,
        _chain_result(-1.0),
        seed,
    )

    assert planned.outcome == "close_call"
    assert unprepared.outcome == "hurt"
    assert planned.probabilities["hurt"] + planned.probabilities["serious"] < (
        unprepared.probabilities["hurt"] + unprepared.probabilities["serious"]
    )


def test_modifier_total_is_capped_and_reported():
    state = _state()
    player = state["players"][0]
    player.update({"health": 1, "stamina": 1, "stress": 100})
    state["locations"][0]["dangerLevel"] = 5

    outcome = resolve_encounter(
        state,
        player,
        _action(),
        _chain_result(-1.0),
        9,
    )

    assert any(
        modifier["source"] == "encounter_modifier_cap"
        for modifier in outcome.modifiers
    )
    assert sum(
        modifier["delta"]
        for modifier in outcome.modifiers
        if modifier["source"] != "encounter_modifier_cap"
    ) > 0.12


def test_effect_application_uses_clamps_and_returns_a_copy():
    state = _state()
    player = state["players"][0]
    player["health"] = 5
    action = _action()
    seed = next(
        candidate
        for candidate in range(1000)
        if 0.72 <= roll_value(
            candidate,
            action.actionId,
            4,
            "22:00",
            "encounter_outcome",
            0,
        ) < 0.95
    )
    outcome = resolve_encounter(
        state,
        player,
        action,
        _chain_result(),
        seed,
    )

    updated = apply_encounter_outcome(state, "player_1", outcome)

    assert updated is not state
    assert outcome.outcome == "hurt"
    assert updated["players"][0]["health"] == 0
    assert len(updated["players"][0]["statusEffects"]) == 1
    assert state["players"][0]["health"] == 5


def test_simulation_apply_does_not_resolve_encounters_but_world_tick_does():
    state = _state()
    state["players"][0]["stress"] = 100
    action = _action()
    seed, result = next(
        (candidate, candidate_result)
        for candidate in range(1000)
        for candidate_result in [
            SimulationEngine(seed=candidate).apply(state, action)
        ]
        if candidate_result.encounterOccurred
    )

    assert result.encounterOutcome is None
    assert result.updatedWorldState["players"][0]["health"] == 100

    resolved = WorldTick(seed=seed).resolve_encounter_after_action(
        WorldState.from_dict(state),
        result,
        action,
        seed=seed,
    )

    assert resolved.encounterOutcome is not None
    assert resolved.encounterOutcome["outcome"] in {
        "evaded",
        "close_call",
        "hurt",
        "serious",
    }
    assert any(
        event["type"] == "encounter_resolution"
        for event in resolved.updatedWorldState["activeEvents"]
    )
    narration = narrate_simulation_result(
        state,
        action,
        resolved,
    )
    assert "Encounter factors:" in narration
