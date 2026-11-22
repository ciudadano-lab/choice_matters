import copy

import pytest

from location_lookup import resolve_location_id
from probability_engine import run_chain
from simulation_engine import SimulationEngine, StructuredAction
from world_state import generate_initial_world_state


@pytest.fixture
def locations_state():
    return {
        "locations": [
            {"id": "market_1", "name": "East Market"},
            {"id": "market_2", "name": "East Market"},
            {"id": "school", "name": "School"},
        ]
    }


def test_resolve_location_id_accepts_ids_and_case_insensitive_names(locations_state):
    assert resolve_location_id(locations_state, "school") == "school"
    assert resolve_location_id(locations_state, "eAsT mArKeT") == "market_1"


@pytest.mark.parametrize("value", [None, 42, [], {}, "unknown"])
def test_resolve_location_id_returns_none_for_invalid_values(locations_state, value):
    assert resolve_location_id(locations_state, value) is None


def test_resolve_location_id_uses_first_duplicate_name_in_order(locations_state):
    assert resolve_location_id(locations_state, "East Market") == "market_1"


def test_resolve_location_id_supports_dict_locations():
    state = {
        "locations": {
            "market_1": {"name": "East Market"},
            "school": {"id": "school", "name": "School"},
        }
    }

    assert resolve_location_id(state, "market_1") == "market_1"
    assert resolve_location_id(state, "EAST MARKET") == "market_1"


@pytest.mark.parametrize("action_type", ["search", "scavenge"])
def test_fresh_spawn_location_name_and_id_are_equivalent(action_type):
    world = generate_initial_world_state([], 7).to_dict()
    player = world["players"][0]
    location_id = player["spawnLocationId"]
    location = next(item for item in world["locations"] if item["id"] == location_id)
    location_name = location["name"]
    location["resources"] = {"food": 5, "water": 3}
    engine = SimulationEngine(seed=7)
    expected_roll_log = None
    expected_result = None
    for player_location in (location_id, location_name):
        for action_target in (location_id, location_name):
            case_state = copy.deepcopy(world)
            case_state["players"][0]["location"] = player_location
            action = StructuredAction(
                actionId=f"{action_type}-location-identity",
                actionType=action_type,
                actor=player["id"],
                target=action_target,
            )
            chain = run_chain(copy.deepcopy(case_state), action, seed=7)
            roll_log = [step.to_dict() for step in chain.steps]
            assert any(
                modifier["source"] == "location_danger"
                for step in chain.steps
                for modifier in step.modifiers
            )

            result = engine.apply(case_state, action)
            normalized_result = copy.deepcopy(result)
            normalized_result.updatedWorldState["players"][0]["location"] = location_id
            if expected_roll_log is None:
                expected_roll_log = roll_log
                expected_result = normalized_result
            else:
                assert roll_log == expected_roll_log
                assert normalized_result == expected_result

    if action_type == "scavenge":
        assert expected_result.resourceChanges
        assert all(amount > 0 for amount in expected_result.resourceChanges.values())


def test_travel_by_location_name_stores_the_resolved_id():
    world = generate_initial_world_state([], 7).to_dict()
    player = world["players"][0]
    target = next(
        location
        for location in world["locations"]
        if location["id"] != player["spawnLocationId"]
    )
    result = SimulationEngine(seed=7).apply(
        world,
        StructuredAction(
            actionId="travel-to-named-location",
            actionType="travel",
            actor=player["id"],
            target=target["name"],
        ),
    )

    updated_player = next(
        item for item in result.updatedWorldState["players"]
        if item["id"] == player["id"]
    )
    assert updated_player["location"] == target["id"]
    assert result.locationChanges[0]["locationId"] == target["id"]


def test_auto_resolve_policy_targets_spawn_location_id_for_named_player_location(monkeypatch):
    world = generate_initial_world_state([], 7).to_dict()
    player = world["players"][0]
    spawn_location_id = player["spawnLocationId"]
    engine = SimulationEngine(seed=7)
    original_apply = engine.apply
    observed_targets = []

    def observe_apply(world_state, action):
        observed_targets.append(action.target)
        return original_apply(world_state, action)

    monkeypatch.setattr(engine, "apply", observe_apply)
    result = engine.auto_resolve_policy_day(
        world,
        player["id"],
        {"actionType": "resource_policy", "focus": "search supplies"},
        day_index=1,
    )

    assert result.actionType == "search"
    assert any(
        modifier["source"] == "location_danger"
        for step in result.rollLog
        for modifier in step["modifiers"]
    )
    assert observed_targets == [spawn_location_id]
