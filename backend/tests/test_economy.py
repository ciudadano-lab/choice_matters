import pytest

from economy import (
    apply_action_yield,
    cap_resource_stores,
    get_starting_resources,
    get_yield_for_location,
)
from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState
from world_tick import WorldEventQueue, WorldTick


def test_starting_stores_and_caps_scale_per_player():
    assert get_starting_resources(3)["food"] == 45
    assert get_starting_resources(3)["water"] == 45
    assert get_starting_resources(3)["medical_supplies"] == 6
    assert cap_resource_stores({"food": 200, "water": 200}, 3) == {
        "food": 120,
        "water": 120,
    }


def test_action_yields_use_location_content_and_respect_resource_caps():
    state = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "farm"}],
        "locations": [{
            "id": "farm",
            "name": "Farm",
            "type": "farm",
            "discovered": True,
            "resources": {},
        }],
        "resources": {"food": 39, "water": 39},
    }
    action = StructuredAction(
        actionId="gather-farm",
        actionType="gather",
        actor="player_1",
        target="farm",
    )
    result = SimulationEngine(seed=1).apply(state, action)

    apply_action_yield(state, result, action)

    assert get_yield_for_location("gather", "farm") == {"food": 2, "water": 1}
    assert result.updatedWorldState["resources"]["food"] == 40
    assert result.updatedWorldState["resources"]["water"] == 40
    assert result.resourceChanges == {"food": 1, "water": 1}


@pytest.mark.parametrize(
    ("action_type", "expected"),
    [
        ("gather", {"food": 2, "water": 1}),
        ("search", {"food": 2, "water": 2}),
        ("scavenge", {"food": 1, "water": 1}),
        ("forage", {"food": 2, "water": 0}),
    ],
)
def test_resource_actions_apply_location_yields(action_type, expected):
    state = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "farm"}],
        "locations": [{
            "id": "farm",
            "name": "Farm",
            "type": "farm",
            "discovered": True,
            "resources": {},
        }],
        "resources": {"food": 10, "water": 10},
    }
    action = StructuredAction(
        actionId=f"{action_type}-farm",
        actionType=action_type,
        actor="player_1",
        target="farm",
    )
    result = SimulationEngine(seed=1).apply(state, action)

    apply_action_yield(state, result, action)

    assert {
        resource: result.updatedWorldState["resources"].get(resource, 0) - 10
        for resource in expected
    } == expected


def test_empty_food_and_water_reduce_health_and_record_death_cause():
    state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{
            "id": "player_1",
            "health": 25,
            "hunger": 0,
            "thirst": 0,
            "location": "shelter",
        }],
        "locations": [{"id": "shelter", "discovered": True}],
        "resources": {"food": 0, "water": 0},
    })

    updated = WorldTick(seed=1).advance(
        state,
        WorldEventQueue(),
        run_autopilot=False,
    )

    assert updated.players[0]["health"] == 7

    state.players[0]["health"] = 15
    dead = WorldTick(seed=1).advance(
        state,
        WorldEventQueue(),
        run_autopilot=False,
    )
    assert dead.players[0]["health"] == 0
    assert dead.players[0]["deathCause"] == "dehydration"


def test_starvation_and_dehydration_damage_begin_within_thirteen_empty_days():
    state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{
            "id": "player_1",
            "health": 100,
            "hunger": 50,
            "thirst": 50,
            "location": "shelter",
        }],
        "locations": [{"id": "shelter", "discovered": True}],
        "resources": {"food": 0, "water": 0},
    })
    tick = WorldTick(seed=1)

    for _ in range(12):
        state = tick.advance(state, WorldEventQueue(), run_autopilot=False)
    assert state.players[0]["health"] == 100

    state = tick.advance(state, WorldEventQueue(), run_autopilot=False)
    assert state.players[0]["health"] == 82


def test_sustenance_and_rest_recover_health():
    state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{
            "id": "player_1",
            "health": 40,
            "hunger": 50,
            "thirst": 50,
            "location": "shelter",
        }],
        "locations": [{"id": "shelter", "discovered": True}],
        "resources": {"food": 3, "water": 3},
    })

    recovered = WorldTick(seed=1).advance(
        state,
        WorldEventQueue(),
        run_autopilot=False,
    )
    assert recovered.players[0]["health"] == 41

    rest_result = SimulationEngine(seed=1).auto_resolve_policy_day(
        state,
        "player_1",
        {"actionType": "personal_development", "focus": "rest"},
        day_index=1,
    )
    assert rest_result.updatedWorldState["players"][0]["health"] == 48
