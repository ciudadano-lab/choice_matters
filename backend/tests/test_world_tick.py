import pytest

from simulation_engine import SimulationEngine
from game_data import TACTICAL_ACTION_TYPES
from world_state import WorldState
from world_tick import WorldTick, WorldEventQueue


TACTICAL_POLICY_CASES = [
    ({"actionType": "resource_policy", "focus": "travel to the clinic", "pivotalTiming": "immediate"}, "travel"),
    ({"actionType": "resource_policy", "focus": "search for supplies"}, "search"),
    ({"actionType": "resource_policy", "focus": "scavenge and salvage the warehouse"}, "scavenge"),
    ({"actionType": "resource_policy", "focus": "gather food"}, "gather"),
    ({"actionType": "diplomatic_stance", "stance": "hostile attack", "pivotalTiming": "immediate"}, "attack"),
    ({"actionType": "diplomatic_stance", "stance": "defend the shelter", "pivotalTiming": "immediate"}, "defend"),
    ({"actionType": "diplomatic_stance", "stance": "send a mission to player 2", "pivotalTiming": "immediate"}, "send_mission"),
    ({"actionType": "diplomatic_stance", "stance": "communicate with neighbors"}, "communicate"),
    ({"actionType": "diplomatic_stance", "stance": "alliance"}, "negotiate"),
    ({"actionType": "diplomatic_stance", "stance": "trade resources"}, "trade"),
    ({"actionType": "infrastructure_investment", "focus": "found a settlement", "pivotalTiming": "immediate"}, "establish_settlement"),
    ({"actionType": "infrastructure_investment", "focus": "fortify the shelter"}, "reinforce"),
    ({"actionType": "infrastructure_investment", "focus": "build a workshop"}, "build"),
    ({"actionType": "population_policy", "focus": "recruit survivors"}, "recruit"),
    ({"actionType": "personal_development", "focus": "rest and recover"}, "rest"),
]


@pytest.mark.parametrize("policy, expected_action_type", TACTICAL_POLICY_CASES)
def test_policy_dispatch_reaches_every_tactical_action(policy, expected_action_type):
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [
            {"id": "school", "name": "School", "discovered": True},
            {"id": "clinic", "name": "Clinic", "discovered": False, "coordinates": {"x": 20, "y": 0}},
        ],
        "resources": {"food": 10, "water": 10},
    })

    result = SimulationEngine(seed=13).auto_resolve_policy_day(
        world_state,
        "player_1",
        policy,
        day_index=1,
    )

    assert result.actionType == expected_action_type


def test_policy_dispatch_covers_canonical_tactical_taxonomy():
    reachable_types = {expected for _, expected in TACTICAL_POLICY_CASES}

    assert reachable_types == set(TACTICAL_ACTION_TYPES)


def test_resource_policy_focus_reaches_scavenge():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 10, "water": 10},
    })

    result = SimulationEngine(seed=13).auto_resolve_policy_day(
        world_state,
        "player_1",
        {"actionType": "resource_policy", "focus": "salvage the school"},
        day_index=1,
    )

    assert result.actionType == "scavenge"


def test_world_tick_autopilot_resolves_policy_days_and_rolls_up_period():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 90,
            "hunger": 40,
            "stress": 20,
            "location": "school",
            "currentPolicy": {
                "actionType": "resource_policy",
                "focus": "scavenging",
                "commitmentDays": 3,
            },
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "discovered": True,
            "zombiePopulation": 2,
            "dangerLevel": 1,
        }],
        "resources": {"food": 3, "water": 3},
    })

    tick = WorldTick(seed=13)
    updated = tick.advance(world_state, WorldEventQueue(), hours=72)

    assert updated.currentDay == 4
    assert tick.last_period_outcome["periodDays"] == 3
    assert tick.last_period_outcome["daysResolved"] == 3
    assert all(item["actionType"] == "gather" for item in tick.last_period_outcome["dailyOutcomes"])
    assert any(event["type"] == "strategic_period_outcome" for event in updated.activeEvents)


def test_world_tick_autopilot_biases_alliance_toward_diplomacy():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 90,
            "location": "school",
            "currentPolicy": {"actionType": "diplomatic_stance", "stance": "alliance", "commitmentDays": 1},
        }],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 3, "water": 3},
    })

    tick = WorldTick(seed=13)
    tick.advance(world_state, WorldEventQueue())

    assert tick.last_period_outcome["dailyOutcomes"][0]["actionType"] == "negotiate"


def test_strategic_period_applies_information_and_map_status_to_room_state():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school", "currentPolicy": {
            "actionType": "resource_policy",
            "focus": "search for supplies",
            "commitmentDays": 1,
        }}],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 3, "water": 3},
    })
    room_state = {"players": {"1": {"facts": {"location": "school"}}}}

    updated = WorldTick(seed=13).advance(
        world_state,
        WorldEventQueue(),
        hours=24,
        room_state=room_state,
    )

    location = updated.locations[0]
    assert location.get("knownInformation")
    assert room_state["players"]["1"]["facts"]["mapStatus"] == "You know where the hospital is approximately."
    assert room_state["players"]["1"]["facts"]["mapStatus"] != "Your neighborhood."


def test_settlement_pivot_happens_once_then_infrastructure_follows_through():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school", "currentPolicy": {
            "actionType": "infrastructure_investment",
            "focus": "found a settlement",
            "commitmentDays": 3,
            "startDay": 1,
        }}],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 10, "water": 10},
    })

    tick = WorldTick(seed=13)
    updated = tick.advance(world_state, WorldEventQueue(), hours=72)

    settlement_events = [event for event in updated.activeEvents if event.get("type") == "settlement"]
    assert len(settlement_events) == 1
    assert len(updated.settlements) == 1
    assert tick.last_period_outcome["daysResolved"] == 3
    assert [item["actionType"] for item in tick.last_period_outcome["dailyOutcomes"]] == [
        "build",
        "build",
        "establish_settlement",
    ]


def test_immediate_attack_pivots_on_day_one_then_recovers():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 10, "water": 10},
    })
    policy = {
        "actionType": "personal_development",
        "focus": "attack now",
        "commitmentDays": 3,
        "startDay": 1,
        "pivotalTiming": "immediate",
    }

    engine = SimulationEngine(seed=13)
    first = engine.auto_resolve_policy_day(world_state, "player_1", policy, day_index=1)
    later = engine.auto_resolve_policy_day(world_state, "player_1", policy, day_index=2)

    assert first.actionType == "attack"
    assert later.actionType == "rest"


def test_strategic_period_syncs_travel_location_to_room_facts():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "school", "currentPolicy": {
            "actionType": "resource_policy",
            "focus": "travel to the clinic",
            "commitmentDays": 1,
        }}],
        "locations": [
            {"id": "school", "name": "School", "discovered": True, "coordinates": {"x": 0, "y": 0}},
            {"id": "clinic", "name": "Clinic", "discovered": False, "coordinates": {"x": 20, "y": 0}},
        ],
        "resources": {"food": 3, "water": 3},
    })
    room_state = {"players": {"1": {"facts": {"location": "old_shelter"}}}}

    WorldTick(seed=13).advance(
        world_state,
        WorldEventQueue(),
        hours=24,
        room_state=room_state,
    )

    assert room_state["players"]["1"]["facts"]["location"] == "clinic"


def test_location_resources_regenerate_slowly_from_maximum_snapshot():
    world_state = WorldState.from_dict({
        "currentDay": 2,
        "players": [{"id": "player_1", "location": "farm"}],
        "locations": [{
            "id": "farm",
            "name": "Farm",
            "resources": {"food": 0, "water": 1},
            "resourcesMax": {"food": 3, "water": 1},
        }],
    })

    updated = WorldTick(seed=13).advance(world_state, WorldEventQueue(), hours=24, run_autopilot=False)
    resources = updated.locations[0]["resources"]

    assert resources["food"] in {0, 1}
    assert resources["water"] == 1
    assert resources["food"] <= updated.locations[0]["resourcesMax"]["food"]


def test_settlements_grow_deterministically_and_emit_tier_transition_event():
    seed = next(
        candidate
        for candidate in range(100)
        if WorldTick(seed=candidate)._stable_seed("3:settlement-1", candidate + 23) % 5 > 0
    )
    world_state = WorldState.from_dict({
        "currentDay": 2,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "settlements": [{
            "id": "settlement-1",
            "name": "School Settlement",
            "location": "school",
            "coordinates": {"x": 1, "y": 1},
            "population": 30,
            "foundedDay": 1,
            "founderId": "player_1",
        }],
    })

    first = WorldTick(seed=seed).advance(world_state, WorldEventQueue(), hours=24, run_autopilot=False)
    second = WorldTick(seed=seed).advance(world_state, WorldEventQueue(), hours=24, run_autopilot=False)

    first_settlement = first.settlements[0]
    assert first_settlement["population"] > 30
    assert first_settlement["population"] == second.settlements[0]["population"]
    transitions = [event for event in first.activeEvents if event.get("type") == "settlement_growth"]
    assert transitions
    assert transitions[0]["fromTier"] == 1
    assert transitions[0]["toTier"] == 2


def test_world_tick_advances_world_state_and_schedules_events():
    world_state = WorldState.from_dict(
        {
            "currentDay": 1,
            "currentTime": "08:00",
            "players": [
                {
                    "id": "player_1",
                    "name": "Player 1",
                    "health": 100,
                    "stamina": 90,
                    "hunger": 40,
                    "stress": 20,
                    "location": "school",
                    "skills": ["survival"],
                    "inventory": [],
                    "alive": True,
                    "trustRelationships": {},
                    "loyalty": 0.5,
                    "fear": 0.2,
                    "resentment": 0.0,
                    "currentActivity": "observing",
                }
            ],
            "locations": [
                {
                    "id": "school",
                    "name": "School",
                    "type": "building",
                    "coordinates": {"x": 0, "y": 0},
                    "discovered": True,
                    "actualState": {"status": "secure"},
                    "visibleState": {"status": "secure"},
                    "zombiePopulation": 2,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 1,
                    "dangerLevel": 1,
                }
            ],
            "factions": [
                {
                    "id": "faction_survivors",
                    "name": "Survivors",
                    "population": 1,
                    "militaryStrength": 1,
                    "resources": {"water": 0, "food": 0},
                    "territory": ["school"],
                    "ideology": "survival",
                    "relationships": {},
                    "goals": ["stay alive"],
                }
            ],
            "resources": {"water": 5, "food": 3, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )

    queue = WorldEventQueue()
    tick = WorldTick(seed=13)
    updated = tick.advance(world_state, queue, hours=24)

    assert updated.currentDay == 2
    assert updated.currentTime == "08:00"
    assert updated.weather in {"clear", "storm", "rain"}
    assert updated.activeEvents
    assert queue.has_pending_events()
    assert queue.next_event()["day"] >= updated.currentDay


def test_world_tick_simulates_faction_actions_and_relationships():
    world_state = WorldState.from_dict(
        {
            "currentDay": 1,
            "currentTime": "08:00",
            "players": [
                {
                    "id": "player_1",
                    "name": "Player 1",
                    "health": 100,
                    "stamina": 90,
                    "hunger": 40,
                    "stress": 20,
                    "location": "school",
                    "skills": ["survival"],
                    "inventory": [],
                    "alive": True,
                    "trustRelationships": {},
                    "loyalty": 0.5,
                    "fear": 0.2,
                    "resentment": 0.0,
                    "currentActivity": "observing",
                }
            ],
            "locations": [
                {
                    "id": "school",
                    "name": "School",
                    "type": "building",
                    "coordinates": {"x": 0, "y": 0},
                    "discovered": True,
                    "actualState": {"status": "secure"},
                    "visibleState": {"status": "secure"},
                    "zombiePopulation": 2,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 1,
                    "dangerLevel": 1,
                },
                {
                    "id": "garage",
                    "name": "Garage",
                    "type": "building",
                    "coordinates": {"x": 1, "y": 0},
                    "discovered": True,
                    "actualState": {"status": "secure"},
                    "visibleState": {"status": "secure"},
                    "zombiePopulation": 1,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 1,
                    "dangerLevel": 1,
                },
            ],
            "factions": [
                {
                    "id": "faction_survivors",
                    "name": "Survivors",
                    "population": 6,
                    "militaryStrength": 4,
                    "resources": {"water": 4, "food": 4, "medical_supplies": 2},
                    "territory": ["school"],
                    "ideology": "survival",
                    "relationships": {},
                    "goals": ["stay alive"],
                },
                {
                    "id": "faction_scavengers",
                    "name": "Scavengers",
                    "population": 4,
                    "militaryStrength": 3,
                    "resources": {"water": 2, "food": 2, "medical_supplies": 1},
                    "territory": ["garage"],
                    "ideology": "resource_acquisition",
                    "relationships": {},
                    "goals": ["secure supplies"],
                },
            ],
            "resources": {"water": 5, "food": 3, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )

    queue = WorldEventQueue()
    tick = WorldTick(seed=29)
    updated = tick.advance(world_state, queue, hours=24)

    faction = next(faction for faction in updated.factions if faction["id"] == "faction_survivors")
    assert "lastAction" in faction
    assert faction["lastAction"] in {
        "expand_territory",
        "recruit_survivors",
        "attack",
        "trade",
        "establish_checkpoints",
        "raid_settlements",
        "negotiate",
        "collapse",
        "split_into_new_factions",
    }
    assert faction["relationships"]
    for score in faction["relationships"].values():
        assert -100 <= score <= 100


def test_world_tick_generates_faction_summary_events_for_world_context():
    world_state = WorldState.from_dict(
        {
            "currentDay": 1,
            "currentTime": "08:00",
            "players": [{"id": "player_1", "name": "Player 1", "health": 100, "stamina": 90, "hunger": 40, "stress": 20, "location": "school", "skills": ["survival"], "inventory": [], "alive": True, "trustRelationships": {}, "loyalty": 0.5, "fear": 0.2, "resentment": 0.0, "currentActivity": "observing"}],
            "locations": [
                {"id": "school", "name": "School", "type": "building", "coordinates": {"x": 0, "y": 0}, "discovered": True, "actualState": {"status": "secure"}, "visibleState": {"status": "secure"}, "zombiePopulation": 2, "resources": {}, "controllingFaction": None, "securityLevel": 1, "dangerLevel": 1},
                {"id": "rail_yard", "name": "Rail Yard", "type": "site", "coordinates": {"x": 1, "y": 0}, "discovered": True, "actualState": {"status": "abandoned"}, "visibleState": {"status": "abandoned"}, "zombiePopulation": 0, "resources": {}, "controllingFaction": None, "securityLevel": 1, "dangerLevel": 1},
            ],
            "factions": [
                {"id": "faction_survivors", "name": "Survivors", "population": 5, "militaryStrength": 3, "resources": {"water": 2, "food": 3}, "territory": ["school"], "ideology": "survival", "relationships": {}, "goals": ["stay alive"]},
                {"id": "faction_scavengers", "name": "Scavengers", "population": 3, "militaryStrength": 2, "resources": {"water": 1, "food": 1}, "territory": ["rail_yard"], "ideology": "resource_acquisition", "relationships": {}, "goals": ["loot"]},
            ],
            "resources": {"water": 5, "food": 3, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )

    queue = WorldEventQueue()
    tick = WorldTick(seed=45)
    tick._apply_faction_action(world_state, world_state.factions[0], "expand_territory", 24, 0, event_queue=queue)

    summaries = [item.summary for item in queue._events]
    assert any("Survivors expanded into" in summary or "expanded into" in summary.lower() for summary in summaries)
