import copy

import pytest

from simulation_engine import SimulationEngine, StructuredAction
from game_data import TACTICAL_ACTION_TYPES
from probability_config import TIME_SLOTS
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
    ({"actionType": "infrastructure_investment", "focus": "fortify the shelter"}, "fortify"),
    ({"actionType": "infrastructure_investment", "focus": "defend the shelter"}, "reinforce"),
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
    primary_actions = [
        item["actionType"]
        for item in tick.last_period_outcome["dailyOutcomes"]
        if not item.get("isAmbient")
    ]
    assert all(action_type == "gather" for action_type in primary_actions)
    assert any(event["type"] == "strategic_period_outcome" for event in updated.activeEvents)


def test_world_tick_caps_retained_event_and_log_collections():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [],
        "activeEvents": [
            {"type": "old", "day": index}
            for index in range(400)
        ],
        "worldHistory": [
            {"eventId": f"old_{index}", "timestamp": index,
             "type": "old", "description": "old"}
            for index in range(250)
        ],
        "internalEvents": [
            {"type": "old", "day": index}
            for index in range(600)
        ],
        "relationships": [
            {"from": "a", "to": "b", "score": index}
            for index in range(600)
        ],
        "settlements": [],
        "factions": [],
        "locations": [],
    })

    updated = WorldTick(seed=13).advance(
        world_state,
        WorldEventQueue(),
        hours=24,
        run_autopilot=False,
    )

    assert len(updated.activeEvents) <= 25
    assert len(updated.worldHistory) <= 25
    assert len(updated.internalEvents) <= 50
    assert len(updated.relationships) <= 50


def test_world_tick_deduplicates_and_sorts_faction_territory():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [],
        "locations": [],
        "settlements": [],
        "factions": [{
            "id": "faction_b",
            "territory": ["zone", "alpha", "zone"],
            "population": 1,
            "militaryStrength": 1,
            "resources": {},
            "ideology": "survival",
            "relationships": {},
        }],
    })
    tick = WorldTick(seed=13)
    tick._normalize_faction(world_state.factions[0])

    assert world_state.factions[0]["territory"] == ["alpha", "zone"]


def test_splinter_is_unique_capped_and_splits_population_and_territory():
    world_state = WorldState.from_dict({
        "currentDay": 28,
        "currentTime": "06:00",
        "players": [],
        "locations": [
            {"id": "zone_a", "name": "A", "type": "site"},
            {"id": "zone_b", "name": "B", "type": "site"},
            {"id": "zone_c", "name": "C", "type": "site"},
        ],
        "settlements": [],
        "factions": [
            {
                "id": "faction_parent",
                "name": "Parent",
                "population": 20,
                "militaryStrength": 4,
                "resources": {"food": 20, "water": 20},
                "territory": ["zone_a", "zone_b", "zone_c"],
                "ideology": "survival",
                "relationships": {},
                "goals": [],
            },
            *[
                {"id": f"other_{index}", "population": 2,
                 "militaryStrength": 1, "resources": {}, "territory": [],
                 "ideology": "survival", "relationships": {}, "goals": []}
                for index in range(7)
            ],
        ],
    })
    tick = WorldTick(seed=13)
    faction = world_state.factions[0]
    tick._apply_faction_action(
        world_state,
        faction,
        "split_into_new_factions",
        24,
        event_queue=WorldEventQueue(),
    )

    assert len(world_state.factions) == 8
    assert faction["territory"] == ["zone_a", "zone_b", "zone_c"]
    assert faction["population"] == 20
    splinters = [
        item for item in world_state.factions
        if str(item.get("id", "")).startswith("faction_parent:splinter:")
    ]
    assert len(splinters) == 0

    uncapped = WorldState.from_dict({
        "currentDay": 28,
        "players": [],
        "locations": [],
        "settlements": [],
        "factions": [{
            "id": "faction_parent",
            "name": "Parent",
            "population": 20,
            "militaryStrength": 4,
            "resources": {"food": 20, "water": 20},
            "territory": ["zone_a", "zone_b", "zone_c"],
            "ideology": "survival",
            "relationships": {},
            "goals": [],
        }],
    })
    parent = uncapped.factions[0]
    tick._apply_faction_action(
        uncapped,
        parent,
        "split_into_new_factions",
        24,
        event_queue=WorldEventQueue(),
    )
    new_splinter = next(
        item for item in uncapped.factions
        if str(item.get("id", "")).startswith("faction_parent:splinter:")
    )
    assert new_splinter["id"] == "faction_parent:splinter:28:1"
    assert parent["population"] == 10
    assert parent["territory"] == ["zone_b", "zone_c"]
    assert new_splinter["population"] == 10
    assert new_splinter["territory"] == ["zone_a"]

    tick._apply_faction_action(
        uncapped,
        parent,
        "split_into_new_factions",
        24,
        event_queue=WorldEventQueue(),
    )
    splinters_after_cooldown = [
        item for item in uncapped.factions
        if str(item.get("id", "")).startswith("faction_parent:splinter:")
    ]
    assert len(splinters_after_cooldown) == 1


def test_weighted_faction_decisions_are_deterministic_and_use_scoring():
    world_state = WorldState.from_dict({
        "currentDay": 7,
        "currentTime": "06:00",
        "players": [],
        "locations": [
            {"id": "zone_a", "name": "A", "type": "site"},
            {"id": "zone_b", "name": "B", "type": "site"},
        ],
        "settlements": [{
            "id": "settlement_a",
            "location_id": "zone_a",
            "faction_id": "faction_test",
            "population": 40,
            "stores": {"food": 10, "water": 10},
        }],
        "factions": [{
            "id": "faction_test",
            "name": "Test",
            "population": 40,
            "militaryStrength": 4,
            "resources": {"food": 10, "water": 10},
            "territory": ["zone_a", "zone_b"],
            "ideology": "survival",
            "relationships": {
                "faction_hostile": -80,
                "faction_friendly": 80,
            },
            "goals": ["secure_supplies", "control_trade_routes"],
            "lastAction": "recruit_survivors",
            "recentActions": ["trade", "recruit_survivors"],
        }],
    })
    faction = world_state.factions[0]
    first_tick = WorldTick(seed=123)
    second_tick = WorldTick(seed=123)

    first_scores = first_tick._faction_action_scores(world_state, faction, 24)
    repeat_score = first_scores["recruit_survivors"]
    first_action = first_tick._choose_faction_action(world_state, faction, 24)
    second_action = second_tick._choose_faction_action(world_state, faction, 24)

    assert first_action == second_action
    faction_without_repeat = dict(faction)
    faction_without_repeat["lastAction"] = None
    faction_without_repeat["recentActions"] = []
    no_repeat_scores = first_tick._faction_action_scores(
        world_state,
        faction_without_repeat,
        24,
    )
    assert no_repeat_scores["recruit_survivors"] > repeat_score

    low_capacity_faction = dict(faction)
    low_capacity_faction["population"] = 40
    near_capacity_faction = dict(faction)
    near_capacity_faction["population"] = 400
    low_scores = first_tick._faction_action_scores(
        world_state,
        low_capacity_faction,
        24,
    )
    high_scores = first_tick._faction_action_scores(
        world_state,
        near_capacity_faction,
        24,
    )
    assert (
        high_scores["recruit_survivors"]
        < low_scores["recruit_survivors"]
    )
    assert first_scores["raid_settlements"] > 8
    assert first_scores["trade"] > 20


def test_player_disposition_and_attention_change_faction_scores_and_targets():
    world = WorldState.from_dict({
        "currentDay": 7,
        "players": [
            {"id": "hostile_player", "location": "hostile_site"},
            {"id": "friendly_player", "location": "friendly_site"},
            {"id": "signaller", "location": "signal_site"},
        ],
        "locations": [
            {"id": "hostile_site", "name": "Hostile Site"},
            {"id": "friendly_site", "name": "Friendly Site"},
            {"id": "signal_site", "name": "Signal Site"},
        ],
        "settlements": [
            {
                "id": "hostile_settlement",
                "location_id": "hostile_site",
                "founderId": "hostile_player",
                "faction_id": "faction_test",
                "population": 10,
                "stores": {"food": 10, "water": 10},
            },
            {
                "id": "friendly_settlement",
                "location_id": "friendly_site",
                "founderId": "friendly_player",
                "faction_id": "faction_test",
                "population": 10,
                "stores": {"food": 10, "water": 10},
            },
            {
                "id": "signalled_settlement",
                "location_id": "signal_site",
                "founderId": "signaller",
                "faction_id": "faction_test",
                "population": 10,
                "stores": {"food": 10, "water": 10},
                "attention_by_player": {"signaller": 100},
            },
        ],
        "factions": [{
            "id": "faction_test",
            "population": 30,
            "militaryStrength": 2,
            "resources": {"food": 10, "water": 10},
            "territory": ["hostile_site", "friendly_site", "signal_site"],
            "relationships": {},
            "player_dispositions": {
                "hostile_player": -90,
                "friendly_player": 90,
                "signaller": 0,
            },
            "goals": [],
        }],
    })
    tick = WorldTick(seed=17)
    faction = world.factions[0]
    with_influence = tick._faction_action_scores(world, faction, 24)
    baseline_world = copy.deepcopy(world)
    baseline_faction = baseline_world.factions[0]
    baseline_faction["player_dispositions"] = {}
    baseline_world.settlements[2].pop("attention_by_player")
    without_influence = tick._faction_action_scores(
        baseline_world,
        baseline_faction,
        24,
    )

    assert with_influence["attack"] > without_influence["attack"]
    assert with_influence["raid_settlements"] > without_influence["raid_settlements"]
    assert with_influence["trade"] > without_influence["trade"]
    assert with_influence["negotiate"] > without_influence["negotiate"]
    assert tick._select_player_decision_target(
        world,
        faction,
        "attack",
    ) == {
        "playerId": "hostile_player",
        "settlementId": "hostile_settlement",
    }
    assert tick._select_player_decision_target(
        world,
        faction,
        "trade",
    ) == {
        "playerId": "friendly_player",
        "settlementId": "friendly_settlement",
    }
    faction_without_hostility = copy.deepcopy(faction)
    faction_without_hostility["player_dispositions"].pop("hostile_player")
    assert tick._select_player_decision_target(
        world,
        faction_without_hostility,
        "raid_settlements",
    ) == {
        "playerId": "signaller",
        "settlementId": "signalled_settlement",
    }


def test_settlement_attention_is_capped_and_decays_daily():
    world = WorldState.from_dict({
        "currentDay": 1,
        "players": [],
        "locations": [{"id": "camp", "type": "school"}],
        "settlements": [{
            "id": "camp_settlement",
            "location_id": "camp",
            "population": 10,
            "status": "stable",
            "stores": {"food": 20, "water": 20},
            "attention_by_player": {
                "player_1": 5,
                "player_2": 1,
                "player_3": 200,
            },
        }],
    })

    WorldTick(seed=5).settlement_tick.advance_days(
        world,
        1,
        first_day=2,
    )

    assert world.settlements[0]["attention_by_player"] == {
        "player_1": 4,
        "player_3": 99,
    }


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
    assert [
        item["actionType"]
        for item in tick.last_period_outcome["dailyOutcomes"]
        if not item.get("isAmbient")
    ] == [
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


def test_settlements_grow_weekly_and_emit_a_deterministic_internal_record():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{
            "id": "school",
            "name": "School",
            "type": "school",
            "discovered": True,
        }],
        "settlements": [{
            "id": "settlement-1",
            "name": "School Settlement",
            "location_id": "school",
            "location": "school",
            "coordinates": {"x": 1, "y": 1},
            "population": 30,
            "stores": {"food": 20, "water": 20},
            "faction_id": "faction_survivors",
            "foundedDay": 1,
            "founderId": "player_1",
        }],
    })

    first = WorldTick(seed=13).advance(
        world_state, WorldEventQueue(), hours=7 * 24, run_autopilot=False
    )
    second = WorldTick(seed=13).advance(
        world_state, WorldEventQueue(), hours=7 * 24, run_autopilot=False
    )

    first_settlement = first.settlements[0]
    assert first_settlement["population"] == 31
    assert first_settlement["population"] == second.settlements[0]["population"]
    assert first_settlement["tier"] == 2
    weekly_records = [
        event for event in first.internalEvents
        if event.get("type") == "settlement_tick"
        and event.get("settlementId") == "settlement-1"
        and event.get("reasons", {}).get("weekly")
    ]
    assert weekly_records
    assert weekly_records[0]["reasons"]["weekly"]["population"] == {
        "before": 30,
        "after": 31,
    }


def test_legacy_settlement_grace_and_status_hysteresis():
    world_state = WorldState.from_dict({
        "currentDay": 10,
        "currentTime": "06:00",
        "players": [],
        "locations": [{"id": "loc", "name": "Location", "type": "building"}],
        "settlements": [{
            "id": "legacy",
            "location": "loc",
            "population": 10,
            "status": "stable",
        }],
    })
    settlement = world_state.settlements[0]
    assert settlement["stores"]["food"] == 0
    assert settlement["legacy_grace_until_day"] == 17

    settlement["legacy_grace_until_day"] = 0
    first = WorldTick(seed=19).advance(
        world_state, WorldEventQueue(), hours=24, run_autopilot=False
    )
    first_settlement = first.settlements[0]
    assert first_settlement["status"] == "stable"
    assert first_settlement["status_pressure_ticks"] == 1

    second = WorldTick(seed=19).advance(
        first, WorldEventQueue(), hours=24, run_autopilot=False
    )
    assert second.settlements[0]["status"] == "strained"


def test_foundation_grant_transfers_starting_stores_after_settlement_action():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "currentPolicy": {
                "actionType": "infrastructure_investment",
                "focus": "found a settlement",
                "commitmentDays": 1,
            },
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "type": "school",
            "discovered": True,
            "coordinates": {"x": 0, "y": 0},
        }],
        "resources": {
            "food": 12,
            "water": 12,
            "building_materials": 2,
        },
    })

    tick = WorldTick(seed=23)
    updated = tick.advance_policy_day(world_state, WorldEventQueue())

    settlement = next(
        item for item in updated.settlements
        if item.get("founderId") == "player_1"
    )
    assert settlement["foundation_grant_applied"] is True
    assert settlement["stores"]["food"] == 6
    assert settlement["stores"]["water"] == 6
    assert settlement["stores"]["building_materials"] == 1
    assert updated.resources["food"] == 3
    assert updated.resources["water"] == 4
    assert any(
        item.get("type") == "settlement_foundation_grant"
        for item in updated.internalEvents
    )


def test_direct_apply_establish_settlement_also_receives_foundation_grant():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "health": 100,
            "stamina": 100,
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "type": "school",
            "discovered": True,
            "coordinates": {"x": 0, "y": 0},
        }],
        "resources": {
            "food": 12,
            "water": 12,
            "building_materials": 2,
        },
    })
    action = StructuredAction(
        actionId="direct-foundation",
        actionType="establish_settlement",
        actor="player_1",
        target="school",
        parameters={},
        timeCost=1,
    )

    result = SimulationEngine(seed=23).apply(world_state, action)
    settlement = next(
        item for item in result.updatedWorldState["settlements"]
        if item.get("founderId") == "player_1"
    )

    assert settlement["foundation_grant_applied"] is True
    assert settlement["stores"]["food"] == 6
    assert settlement["stores"]["water"] == 6
    assert settlement["stores"]["building_materials"] == 1
    assert result.updatedWorldState["resources"]["food"] == 4
    assert result.updatedWorldState["resources"]["water"] == 5


def test_ninety_day_settlement_fast_forward_is_deterministic():
    initial = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [],
        "locations": [{
            "id": "farm",
            "name": "Farm",
            "type": "farm",
        }],
        "factions": [{
            "id": "faction_test",
            "population": 10,
            "militaryStrength": 1,
            "resources": {"food": 20, "water": 20},
            "territory": ["farm"],
            "ideology": "survival",
            "relationships": {},
            "goals": [],
        }],
        "settlements": [{
            "id": "farm_settlement",
            "location_id": "farm",
            "location": "farm",
            "population": 20,
            "stores": {"food": 30, "water": 30},
            "faction_id": "faction_test",
            "morale": 60,
            "unrest": 10,
            "defense": 20,
        }],
    })

    first = WorldTick(seed=31).advance(
        initial, WorldEventQueue(), hours=90 * 24, run_autopilot=False
    )
    second = WorldTick(seed=31).advance(
        initial, WorldEventQueue(), hours=90 * 24, run_autopilot=False
    )

    assert first.to_dict() == second.to_dict()
    assert first.currentDay == 91
    assert first.settlements[0]["population"] >= 20
    assert first.factions[0]["population"] == (
        first.settlements[0]["population"]
        + first.factions[0]["roaming_population"]
    )


def test_time_slots_follow_expected_order_and_rollover():
    assert [name for name, _ in TIME_SLOTS] == ["dawn", "day", "dusk", "night"]
    assert WorldTick.slot_name_for_time("00:00") == "night"
    assert WorldTick.slot_name_for_time("06:00") == "dawn"
    assert WorldTick.slot_name_for_time("12:00") == "day"
    assert WorldTick.slot_name_for_time("18:00") == "dusk"
    assert WorldTick.slot_name_for_time("22:00") == "night"

    slot_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [],
    })
    slot_tick = WorldTick(seed=13)
    observed_times = []
    for _ in range(4):
        slot_state = slot_tick.advance_slot(
            slot_state, WorldEventQueue(), run_autopilot=False
        )
        observed_times.append(slot_state.currentTime)
    assert observed_times == ["12:00", "18:00", "22:00", "06:00"]
    assert slot_state.currentDay == 2

    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "22:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "hunger": 10,
            "stress": 30,
            "statusEffects": [{"id": "e1", "mechanicalEffect": {"health": -10}}],
        }],
        "locations": [{"id": "school", "name": "School", "discovered": True, "zombiePopulation": 1, "dangerLevel": 1}],
    })

    updated = WorldTick(seed=13).advance_slot(world_state, WorldEventQueue(), run_autopilot=False)

    assert updated.currentDay == 2
    assert updated.currentTime == "06:00"
    assert updated.players[0]["stress"] <= 100
    assert updated.players[0]["hunger"] <= 100


def test_policy_day_runs_night_action_at_2200_and_records_slot():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "shelter",
            "currentPolicy": {
                "actionType": "personal_development",
                "focus": "rest and recover",
                "planFeatures": {"timing": "night"},
            },
        }],
        "locations": [{"id": "shelter", "name": "Shelter", "type": "shelter"}],
        "resources": {"food": 10, "water": 10},
    })

    tick = WorldTick(seed=13)
    updated = tick.advance_policy_day(world_state, WorldEventQueue())

    action_record = next(
        record for record in tick.last_policy_day_outcomes
        if record["playerId"] == "player_1" and not record.get("isAmbient")
    )
    assert action_record["slot"] == "night"
    assert action_record["slotTime"] == "22:00"
    assert updated.currentTime == "06:00"
    assert updated.currentDay == 2


def test_ambient_night_chain_runs_only_for_outdoors_players():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [
            {"id": "outdoors", "location": "street"},
            {"id": "sheltered", "location": "shelter"},
            {"id": "fortified", "location": "fort"},
        ],
        "locations": [
            {"id": "street", "name": "Street", "type": "street"},
            {"id": "shelter", "name": "Shelter", "type": "shelter"},
            {
                "id": "fort",
                "name": "Fort",
                "flags": {"fortified": True},
                "flagExpiries": {"fortified": 4},
            },
        ],
        "resources": {"food": 10, "water": 10},
    })
    tick = WorldTick(seed=13)

    tick.advance_policy_day(world_state, WorldEventQueue())

    ambient_records = [
        record for record in tick.last_policy_day_outcomes
        if record.get("isAmbient")
    ]
    assert len(ambient_records) == 1
    assert ambient_records[0]["playerId"] == "outdoors"
    assert ambient_records[0]["slot"] == "night"
    assert ambient_records[0]["slotTime"] == "22:00"


def test_policy_day_runs_daily_upkeep_once_at_rollover(monkeypatch):
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "shelter",
            "hunger": 25,
            "thirst": 25,
            "stress": 20,
            "currentPolicy": {
                "actionType": "personal_development",
                "focus": "rest and recover",
            },
        }],
        "locations": [{"id": "shelter", "name": "Shelter", "type": "shelter"}],
        "resources": {"food": 10, "water": 10},
    })
    tick = WorldTick(seed=13)
    advance = tick.advance
    upkeep_calls = []

    def track_advance(state, event_queue, **kwargs):
        upkeep_calls.append(kwargs.get("hours"))
        return advance(state, event_queue, **kwargs)

    monkeypatch.setattr(tick, "advance", track_advance)
    updated = tick.advance_policy_day(world_state, WorldEventQueue())

    assert upkeep_calls == [8]
    assert updated.currentDay == 2
    assert updated.currentTime == "06:00"


def test_policy_day_roll_logs_are_deterministic_for_same_seed():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "currentPolicy": {
                "actionType": "resource_policy",
                "focus": "search for supplies",
                "planFeatures": {"timing": "day"},
            },
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "discovered": True,
            "dangerLevel": 3,
            "zombiePopulation": 2,
        }],
        "resources": {"food": 10, "water": 10},
    })

    first_records = WorldTick(seed=271)
    first_records.advance_policy_day(world_state, WorldEventQueue())
    second_records = WorldTick(seed=271)
    second_records.advance_policy_day(world_state, WorldEventQueue())

    assert first_records.last_policy_day_outcomes == second_records.last_policy_day_outcomes


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


def test_world_tick_defers_faction_decisions_to_weekly_ticks():
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
    assert "lastAction" not in faction
    assert faction["relationships"] == {}
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
    tick._apply_faction_action(world_state, world_state.factions[0], "expand_territory", 24, event_queue=queue)

    summaries = [item.summary for item in queue._events]
    assert any("Survivors expanded into" in summary or "expanded into" in summary.lower() for summary in summaries)
