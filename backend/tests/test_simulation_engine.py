import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from action_registry import ActionDefinition
from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState
from world_tick import WorldTick, WorldEventQueue


def test_simulation_engine_applies_travel_deterministically():
    world_state = WorldState.from_dict(
        {
            "currentDay": 1,
            "currentTime": "06:00",
            "players": [
                {
                    "id": "player_1",
                    "name": "Player 1",
                    "health": 100,
                    "stamina": 90,
                    "hunger": 40,
                    "stress": 20,
                    "location": "Unknown Shelter",
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
                    "id": "location_shelter",
                    "name": "Unknown Shelter",
                    "type": "shelter",
                    "coordinates": {"x": 0, "y": 0},
                    "discovered": True,
                    "actualState": {"status": "secure"},
                    "visibleState": {"status": "secure"},
                    "zombiePopulation": 0,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 1,
                    "dangerLevel": 1,
                },
                {
                    "id": "location_outpost",
                    "name": "Abandoned Outpost",
                    "type": "outpost",
                    "coordinates": {"x": 3, "y": 2},
                    "discovered": False,
                    "actualState": {"status": "uncertain"},
                    "visibleState": {"status": "uncertain"},
                    "zombiePopulation": 4,
                    "resources": {"food": 3},
                    "controllingFaction": None,
                    "securityLevel": 2,
                    "dangerLevel": 3,
                },
            ],
            "factions": [
                {
                    "id": "faction_survivors",
                    "name": "Survivors",
                    "population": 1,
                    "militaryStrength": 1,
                    "resources": {"water": 0, "food": 0},
                    "territory": ["Unknown Shelter"],
                    "ideology": "survival",
                    "relationships": {},
                    "goals": ["stay alive"],
                }
            ],
            "resources": {"water": 5, "food": 2, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [{"day": 1, "summary": "The outbreak begins."}],
            "relationships": [],
        }
    )

    action = StructuredAction(
        actionId="travel-001",
        actionType="travel",
        actor="player_1",
        target="location_outpost",
        parameters={"destination": "location_outpost", "seed": 7},
        timeCost=2,
        resourceCost={"water": 1},
        risks=["zombie_encounter"],
        prerequisites=["can_move"],
    )

    engine = SimulationEngine()
    first = engine.apply(world_state, action)
    second = engine.apply(copy.deepcopy(world_state), action)

    assert first.timeConsumed == second.timeConsumed
    assert first.encounterProbability == second.encounterProbability
    assert first.encounterOccurred == second.encounterOccurred
    assert first.encounterDetails == second.encounterDetails
    assert first.updatedWorldState["players"][0]["location"] == "location_outpost"
    assert first.resourceChanges["water"] == -1
    assert first.locationChanges[0]["locationId"] == "location_outpost"
    assert first.worldEventsTriggered[0]["type"] == "travel"
    assert all(
        event["type"] != "encounter" or first.encounterOccurred
        for event in first.worldEventsTriggered
    )


@pytest.mark.parametrize(
    ("action_type", "expected_flag", "expected_item"),
    [
        ("fortify", "fortified", None),
        ("craft_repair", None, "repair_kit"),
    ],
)
def test_registry_only_actions_apply_effects_and_registry_time_cost(
    action_type,
    expected_flag,
    expected_item,
):
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{
            "id": "player_1",
            "location": "shelter",
            "health": 100,
            "stamina": 100,
            "stress": 0,
            "inventory": [],
            "statusEffects": [],
        }],
        "locations": [{"id": "shelter", "name": "Shelter", "discovered": True}],
        "resources": {
            "food": 5,
            "water": 5,
            "medical_supplies": 3,
            "building_materials": 3,
        },
    }
    action = StructuredAction(
        actionId=f"registry-{action_type}",
        actionType=action_type,
        actor="player_1",
        target="shelter",
    )

    result = SimulationEngine(seed=1).apply(world, action)

    assert result.timeConsumed == 1
    assert result.updatedWorldState["currentTime"] == "09:00"
    if expected_flag:
        assert result.updatedWorldState["locations"][0]["flags"][expected_flag] == 1
        assert result.updatedWorldState["locations"][0]["flagExpiries"][expected_flag] == 6
    if expected_item:
        assert result.updatedWorldState["players"][0]["inventory"] == [expected_item]
    expected_resources = (
        {"building_materials": -1}
        if action_type == "fortify"
        else {"medical_supplies": -1}
        if action_type == "craft_repair"
        else {}
    )
    assert result.resourceChanges == expected_resources


def test_current_rules_reinforce_uses_defense_without_medical_supplies():
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "outpost", "stress": 20}],
        "locations": [{"id": "outpost", "name": "Outpost"}],
        "resources": {"medical_supplies": 3},
    }
    action = StructuredAction(
        actionId="reinforce-current",
        actionType="reinforce",
        actor="player_1",
        target="outpost",
    )

    legacy = SimulationEngine(seed=1).apply(world, action)
    current = SimulationEngine(seed=1).apply(world, action, rules="current")

    assert legacy.resourceChanges == {"medical_supplies": -1}
    assert legacy.updatedWorldState["resources"]["medical_supplies"] == 2
    assert current.resourceChanges == {}
    assert current.updatedWorldState["resources"]["medical_supplies"] == 3
    assert current.updatedWorldState["locations"][0]["flags"]["fortified"] == 1
    assert current.updatedWorldState["locations"][0]["flagExpiries"]["fortified"] == 6

    legacy_build = SimulationEngine(seed=1).apply(world, StructuredAction(
        actionId="build-legacy",
        actionType="build",
        actor="player_1",
        target="outpost",
    ))
    current_build = SimulationEngine(seed=1).apply(
        world,
        StructuredAction(
            actionId="build-current",
            actionType="build",
            actor="player_1",
            target="outpost",
        ),
        rules="current",
    )
    assert "fortified" not in legacy_build.updatedWorldState["locations"][0].get("flags", {})
    assert current_build.updatedWorldState["locations"][0]["flags"]["fortified"] == 1


def test_current_rules_signal_is_live_and_increases_settlement_attention():
    from action_registry import load_action_registry

    assert load_action_registry()["signal"].status == "live"
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "outpost"}],
        "locations": [{"id": "outpost", "name": "Outpost"}],
        "settlements": [{
            "id": "outpost-settlement",
            "location_id": "outpost",
            "population": 10,
            "stores": {},
        }],
        "resources": {},
    }

    result = SimulationEngine(seed=1).apply(
        world,
        StructuredAction(
            actionId="live-signal",
            actionType="signal",
            actor="player_1",
            target="outpost",
        ),
        rules="current",
    )

    assert result.updatedWorldState["locations"][0]["flags"]["signal_sent"] == 1
    assert result.updatedWorldState["settlements"][0]["attention_by_player"] == {
        "player_1": 2
    }


def test_default_apply_matches_legacy_golden_for_all_fifteen_actions(monkeypatch):
    tests_dir = Path(__file__).resolve().parent
    with (tests_dir / "golden" / "probability_rolllog.json").open(
        "r", encoding="utf-8"
    ) as handle:
        golden = json.load(handle)
    from golden_cases import CHAIN_ACTIONS, build_apply_cases
    import probability_engine
    from make_golden import FROZEN_LIBRARY, _apply_record

    monkeypatch.setattr(probability_engine, "_DEFAULT_LIBRARY", FROZEN_LIBRARY)
    cases = build_apply_cases()
    assert [case["action_fields"]["actionType"] for case in cases[:15]] == CHAIN_ACTIONS
    assert [
        _apply_record(index, case) for index, case in enumerate(cases[:15])
    ] == golden["apply_cases"][:15]


def test_main_preview_and_real_apply_use_identical_current_rules():
    from main import _apply_live_action

    world = {
        "currentDay": 2,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "town"}],
        "locations": [{"id": "town", "name": "Town"}],
        "factions": [{
            "id": "faction_survivors",
            "player_dispositions": {"player_1": 0},
            "leader_npc_id": "npc_leader",
        }],
        "settlements": [{
            "id": "town-settlement",
            "location_id": "town",
            "morale": 50,
            "stores": {"food": 3},
        }],
        "resources": {},
    }
    action = StructuredAction(
        actionId="trade-parity",
        actionType="trade",
        actor="player_1",
        target="town",
    )

    preview = _apply_live_action(SimulationEngine(seed=9), world, action)
    real = _apply_live_action(SimulationEngine(seed=9), world, action)

    assert asdict(preview) == asdict(real)
    assert real.updatedWorldState["settlements"][0]["stores"]["food"] == 4
    assert real.updatedWorldState["factions"][0]["player_dispositions"]["player_1"] == 5


def test_world_tick_scales_apply_relationship_changes_after_apply():
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "town"}],
        "locations": [{"id": "town", "name": "Town"}],
        "factions": [{
            "id": "faction_survivors",
            "player_dispositions": {"player_1": 0},
        }],
        "resources": {},
        "relationships": [],
    }
    action = StructuredAction(
        actionId="negotiate-scale",
        actionType="negotiate",
        actor="player_1",
        target="town",
    )
    result = SimulationEngine(seed=1).apply(world, action, rules="current")
    relationship_log = copy.deepcopy(result.updatedWorldState["relationships"])

    WorldTick.apply_faction_relationship_changes(result, action)

    assert result.factionRelationshipChanges == [{"factionId": "faction_survivors", "delta": 2}]
    assert result.updatedWorldState["factions"][0]["player_dispositions"]["player_1"] == 10
    assert result.updatedWorldState["relationships"] == relationship_log


def test_current_social_and_settlement_actions_change_registry_state():
    initial = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{"id": "player_1", "location": "town"}],
        "locations": [
            {"id": "town", "name": "Town"},
            {"id": "camp", "name": "Camp"},
        ],
        "factions": [{
            "id": "faction_survivors",
            "player_dispositions": {"player_1": 0},
            "leader_npc_id": "npc_leader",
        }],
        "settlements": [{
            "id": "town_settlement",
            "location_id": "town",
            "population": 20,
            "morale": 50,
            "stores": {"food": 4},
            "faction_id": "faction_survivors",
            "leader_npc_id": "npc_leader",
        }],
        "npcs": {"npc_leader": {"id": "npc_leader", "loyalty": 50}},
        "resources": {"food": 10, "water": 10},
    }

    def resolve(action_type, target="town", action_id=None):
        return SimulationEngine(seed=1).apply(
            copy.deepcopy(initial),
            StructuredAction(
                actionId=action_id or f"current-{action_type}",
                actionType=action_type,
                actor="player_1",
                target=target,
            ),
            rules="current",
        ).updatedWorldState

    negotiated = resolve("negotiate")
    assert negotiated["settlements"][0]["morale"] == 51
    assert negotiated["npcs"]["npc_leader"]["loyalty"] == 51

    traded = resolve("trade")
    assert traded["settlements"][0]["stores"]["food"] == 5

    recruited = resolve("recruit")
    assert recruited["settlements"][0]["population"] == 21
    assert recruited["npcs"]["npc_leader"]["loyalty"] == 51

    mission = resolve("send_mission")
    assert mission["factions"][0]["player_dispositions"]["player_1"] == 1
    assert mission["settlements"][0]["attention_by_player"]["player_1"] == 1

    founded = resolve("establish_settlement", target="camp", action_id="new-camp")
    created = next(item for item in founded["settlements"] if item["id"] == "new-camp")
    assert created["morale"] == 51
    assert founded["npcs"]["npc_leader"]["loyalty"] == 51


def test_send_mission_changes_the_target_players_faction_disposition():
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [
            {"id": "player_1", "location": "school"},
            {"id": "player_2", "location": "school", "factionId": "faction_target"},
        ],
        "locations": [{"id": "school", "name": "School"}],
        "factions": [
            {"id": "faction_survivors", "player_dispositions": {"player_1": 0}},
            {"id": "faction_target", "player_dispositions": {"player_1": 0}},
        ],
        "resources": {"water": 5},
    }

    result = SimulationEngine(seed=1).apply(
        world,
        StructuredAction(
            actionId="mission-target-faction",
            actionType="send_mission",
            actor="player_1",
            target="player_2",
        ),
        rules="current",
    )

    assert result.updatedWorldState["factions"][0]["player_dispositions"]["player_1"] == 0
    assert result.updatedWorldState["factions"][1]["player_dispositions"]["player_1"] == 1


def test_registry_effect_vocabulary_uses_clamped_player_updates(monkeypatch):
    from simulation_engine import _ACTION_REGISTRY

    definition = ActionDefinition.model_validate({
        "id": "fortify",
        "action_type": "fortify",
        "label": "Fortify",
        "category": "utility",
        "chain_template": "noticed_chain",
        "time_cost": 2,
        "effects": [
            {"type": "change_resource", "resource": "food", "amount": 1},
            {"type": "change_stat", "stat": "health", "amount": -200},
            {"type": "move", "target": "actor", "location": "target"},
            {"type": "remove_status", "target": "actor", "status": "old_status"},
            {"type": "add_status", "target": "actor", "status": "new_status"},
            {"type": "remove_item", "target": "actor", "item": "old_tool"},
            {"type": "add_item", "target": "actor", "item": "new_tool"},
            {"type": "set_flag", "target": "location", "flag": "signaled", "value": 1},
            {
                "type": "change_faction_disposition",
                "faction_id": "faction_test",
                "player_id": "actor",
                "amount": 3,
            },
            {
                "type": "change_settlement_stat",
                "settlement_id": "target",
                "stat": "morale",
                "amount": 4,
            },
            {
                "type": "change_npc_loyalty",
                "npc_id": "npc_test",
                "amount": 5,
            },
        ],
        "status": "live",
    })
    monkeypatch.setitem(_ACTION_REGISTRY, "fortify", definition)
    world = {
        "currentDay": 1,
        "currentTime": "08:00",
        "players": [{
            "id": "player_1",
            "location": "origin",
            "health": 10,
            "inventory": ["old_tool"],
            "statusEffects": [{"id": "old", "name": "old_status"}],
        }],
        "locations": [
            {"id": "origin", "name": "Origin"},
            {"id": "target", "name": "Target"},
        ],
        "factions": [{
            "id": "faction_test",
            "player_dispositions": {"player_1": 0},
        }],
        "settlements": [{
            "id": "settlement_test",
            "location_id": "target",
            "morale": 40,
            "stores": {},
        }],
        "npcs": {"npc_test": {"id": "npc_test", "loyalty": 50}},
        "resources": {"food": 0},
    }

    result = SimulationEngine(seed=1).apply(
        world,
        StructuredAction(
            actionId="fortify-effects",
            actionType="fortify",
            actor="player_1",
            target="target",
        ),
    )
    player = result.updatedWorldState["players"][0]

    assert result.timeConsumed == 2
    assert result.resourceChanges == {"food": 1}
    assert player["health"] == 0
    assert player["location"] == "target"
    assert player["inventory"] == ["new_tool"]
    assert [effect["name"] for effect in player["statusEffects"]] == ["new_status"]
    assert result.updatedWorldState["locations"][1]["flags"]["signaled"] == 1
    assert result.updatedWorldState["factions"][0]["player_dispositions"]["player_1"] == 3
    assert result.updatedWorldState["settlements"][0]["morale"] == 44
    assert result.updatedWorldState["npcs"]["npc_test"]["loyalty"] == 55


def test_policy_action_commits_without_advancing_calendar():
    world = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "shelter"}],
        "locations": [{"id": "shelter", "discovered": True}],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="policy-001",
        actionType="resource_policy",
        actor="player_1",
        target="settlement",
        parameters={"policy": {"focus": "scavenging", "commitmentDays": 3}},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    player = result.updatedWorldState["players"][0]
    assert result.timeConsumed == 0
    assert result.updatedWorldState["currentDay"] == 1
    assert player["currentPolicy"]["actionType"] == "resource_policy"
    assert player["currentPolicy"]["commitmentDays"] == 3
    assert player["currentPolicy"]["startDay"] == 1


def test_scavenge_harvests_and_depletes_location_resources():
    world = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "warehouse", "stress": 0, "stamina": 10}],
        "locations": [{
            "id": "warehouse",
            "name": "Warehouse",
            "resources": {"building_materials": 5, "tools": 1},
            "visibleState": {"status": "secure"},
        }],
        "resources": {},
    })
    action = StructuredAction(
        actionId="scavenge-001",
        actionType="scavenge",
        actor="player_1",
        target="warehouse",
        timeCost=1,
    )

    first = SimulationEngine(seed=7).apply(world, action)
    first_stock = first.updatedWorldState["locations"][0]["resources"]

    assert first.resourceChanges
    assert all(value > 0 for value in first.resourceChanges.values())
    assert first_stock["building_materials"] < 5
    assert first_stock["tools"] == 0
    assert first.updatedWorldState["locations"][0]["visibleState"] == {"status": "scavenged"}
    assert first.worldEventsTriggered[0]["type"] == "scavenge"

    second = SimulationEngine(seed=7).apply(first.updatedWorldState, action)
    assert all(
        second.updatedWorldState["locations"][0]["resources"].get(resource, 0) <= first_stock.get(resource, 0)
        for resource in first_stock
    )


def test_scavenge_empty_location_returns_valid_picked_clean_result():
    world = WorldState.from_dict({
        "currentDay": 1,
        "players": [{"id": "player_1", "location": "warehouse"}],
        "locations": [{"id": "warehouse", "name": "Warehouse", "resources": {}}],
    })
    action = StructuredAction(
        actionId="scavenge-empty",
        actionType="scavenge",
        actor="player_1",
        target="warehouse",
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.resourceChanges == {}
    assert "picked clean" in result.informationDiscovered[0]["content"]
    assert result.updatedWorldState["locations"][0]["visibleState"] == {"status": "scavenged"}


def test_establish_settlement_creates_filled_record_and_keeps_coordinates():
    world = WorldState.from_dict({
        "currentDay": 4,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{
            "id": "school",
            "name": "Westwood School",
            "coordinates": {"x": 5, "y": 6},
            "discovered": True,
        }],
        "settlements": [],
        "resources": {"food": 6, "water": 6},
    })
    action = StructuredAction(
        actionId="settlement-1",
        actionType="establish_settlement",
        actor="player_1",
        target="school",
        parameters={},
        timeCost=3,
        resourceCost={"food": 2, "water": 1},
    )

    result = SimulationEngine(seed=11).apply(world, action)
    settlements = result.updatedWorldState["settlements"]

    assert len(settlements) == 1
    settlement = settlements[0]
    assert settlement["location"] == "school"
    assert settlement["name"] == "Westwood School Settlement"
    assert settlement["coordinates"] == {"x": 5, "y": 6}
    assert settlement["population"] >= 3
    assert settlement["population"] <= 10
    assert settlement["tier"] == 1
    assert settlement["foundedDay"] == 4
    assert settlement["founderId"] == "player_1"


def test_establish_settlement_dedupes_same_location_and_grows_population():
    world = WorldState.from_dict({
        "currentDay": 7,
        "players": [{"id": "player_1", "location": "school"}],
        "locations": [{
            "id": "school",
            "name": "Westwood School",
            "coordinates": {"x": 5, "y": 6},
            "discovered": True,
        }],
        "settlements": [{
            "id": "existing-settlement",
            "name": "Westwood School Settlement",
            "location": "school",
            "coordinates": {"x": 5, "y": 6},
            "population": 6,
            "foundedDay": 1,
            "founderId": "player_1",
        }],
        "resources": {"food": 6, "water": 6},
    })
    action = StructuredAction(
        actionId="settlement-2",
        actionType="establish_settlement",
        actor="player_2",
        target="school",
        parameters={},
        timeCost=3,
        resourceCost={"food": 2, "water": 1},
    )

    result = SimulationEngine(seed=13).apply(world, action)
    settlements = result.updatedWorldState["settlements"]

    assert len(settlements) == 1
    assert settlements[0]["location"] == "school"
    assert settlements[0]["population"] >= 7
    assert settlements[0]["population"] <= 14
    assert settlements[0]["tier"] == 1
    assert settlements[0]["founderId"] == "player_1"


def test_simulation_sets_deterministic_outcome_tier_for_travel():
    world_state = WorldState.from_dict(
        {
            "currentDay": 1,
            "currentTime": "06:00",
            "players": [{
                "id": "player_1",
                "name": "Player 1",
                "health": 100,
                "stamina": 90,
                "hunger": 40,
                "stress": 20,
                "location": "Unknown Shelter",
                "skills": ["survival"],
                "inventory": [],
                "alive": True,
                "trustRelationships": {},
                "loyalty": 0.5,
                "fear": 0.2,
                "resentment": 0.0,
                "currentActivity": "observing",
            }],
            "locations": [{
                "id": "location_shelter",
                "name": "Unknown Shelter",
                "type": "shelter",
                "coordinates": {"x": 0, "y": 0},
                "discovered": True,
                "actualState": {"status": "secure"},
                "visibleState": {"status": "secure"},
                "zombiePopulation": 0,
                "resources": {},
                "controllingFaction": None,
                "securityLevel": 1,
                "dangerLevel": 1,
            }, {
                "id": "location_outpost",
                "name": "Abandoned Outpost",
                "type": "outpost",
                "coordinates": {"x": 3, "y": 2},
                "discovered": False,
                "actualState": {"status": "uncertain"},
                "visibleState": {"status": "uncertain"},
                "zombiePopulation": 4,
                "resources": {"food": 3},
                "controllingFaction": None,
                "securityLevel": 2,
                "dangerLevel": 3,
            }],
            "resources": {"water": 5, "food": 2, "medical_supplies": 1},
        }
    )

    action = StructuredAction(
        actionId="travel-002",
        actionType="travel",
        actor="player_1",
        target="location_outpost",
        parameters={"destination": "location_outpost", "seed": 7},
        timeCost=2,
        resourceCost={"water": 1},
        risks=["zombie_encounter"],
    )

    first = SimulationEngine(seed=7).apply(world_state, action)
    second = SimulationEngine(seed=7).apply(copy.deepcopy(world_state), action)

    assert first.outcomeTier == second.outcomeTier
    assert first.outcomeTier in {"critical_success", "success", "partial_success", "failure", "critical_failure"}


@pytest.mark.parametrize("action_type", ["search", "gather", "build", "rest", "reinforce"])
def test_nontravel_action_at_actor_location_uses_score(action_type):
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "health": 100,
            "stamina": 100,
            "stress": 0,
            "hunger": 0,
            "statusEffects": [],
        }],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "resources": {"food": 10, "water": 10},
    })
    action = StructuredAction(
        actionId=f"same-location-{action_type}",
        actionType=action_type,
        actor="player_1",
        target="school",
        parameters={"seed": 17},
    )

    result = SimulationEngine(seed=17).apply(world_state, action)

    assert result.outcomeTier in {"success", "critical_success", "partial_success"}


def test_travel_to_actor_location_still_fails():
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "players": [{
            "id": "player_1",
            "location": "school",
            "health": 100,
            "stamina": 100,
            "stress": 0,
            "statusEffects": [],
        }],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
    })
    action = StructuredAction(
        actionId="same-location-travel",
        actionType="travel",
        actor="player_1",
        target="school",
        parameters={"seed": 17},
    )

    result = SimulationEngine(seed=17).apply(world_state, action)

    assert result.outcomeTier == "failure"


@pytest.mark.parametrize(
    ("policy", "initial_stats"),
    [
        (
            {"actionType": "personal_development", "focus": "rest and recover"},
            {"health": 100, "stamina": 100, "stress": 100, "hunger": 100},
        ),
        (
            {"actionType": "resource_policy", "focus": "search supplies"},
            {"health": 3, "stamina": 3, "stress": 100, "hunger": 0},
        ),
    ],
    ids=["rest", "injury-heavy"],
)
def test_player_stats_stay_in_range_over_60_policy_days(policy, initial_stats):
    world_state = WorldState.from_dict({
        "currentDay": 1,
        "currentTime": "06:00",
        "players": [{
            "id": "player_1",
            "location": "school",
            "statusEffects": [],
            **initial_stats,
        }],
        "locations": [{
            "id": "school",
            "name": "School",
            "discovered": True,
            "zombiePopulation": 30,
            "dangerLevel": 5,
        }],
        "resources": {"food": 10, "water": 10},
    })
    engine = SimulationEngine(seed=31)
    tick = WorldTick(seed=31)
    injury_observed = False

    for day in range(1, 61):
        result = engine.auto_resolve_policy_day(
            world_state,
            "player_1",
            policy,
            day_index=day,
        )
        player_after_action = result.updatedWorldState["players"][0]
        injury_observed = injury_observed or bool(player_after_action.get("statusEffects"))
        world_state = WorldState.from_dict(result.updatedWorldState)
        world_state = tick.advance(
            world_state,
            WorldEventQueue(),
            hours=24,
            run_autopilot=False,
        )

        for player in world_state.to_dict()["players"]:
            assert all(0 <= player.get(stat, 0) <= 100 for stat in ("health", "stamina", "stress", "hunger"))

    if policy["actionType"] == "resource_policy":
        assert injury_observed


def test_simulation_applies_status_effect_to_failed_physical_action():
    world = WorldState.from_dict({
        "currentDay": 2,
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 90,
            "stress": 20,
            "statusEffects": [],
            "entities": {"companions": [], "possessions": []},
        }],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="climb-001",
        actionType="travel",
        actor="player_1",
        target="location_shelter",
        parameters={"seed": 7},
        timeCost=1,
        resourceCost={},
    )

    result = SimulationEngine(seed=7).apply(world, action)
    player = next(item for item in result.updatedWorldState["players"] if item["id"] == "player_1")

    assert any(effect.get("name") in {"sprained ankle", "laceration", "bite scare"} for effect in player.get("statusEffects", []))


def test_world_tick_applies_and_expires_status_effects():
    world = WorldState.from_dict({
        "currentDay": 3,
        "currentTime": "08:00",
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 60,
            "statusEffects": [{
                "id": "effect_1",
                "name": "sprained ankle",
                "appliedDay": 3,
                "expiresDay": 3,
                "mechanicalEffect": {"stamina": -20},
            }],
        }],
        "locations": [],
        "factions": [],
        "resources": {"food": 5, "water": 5},
        "worldHistory": [],
    })

    ticked = WorldTick(seed=7).advance(world, WorldEventQueue())
    player = next(item for item in ticked.players if item["id"] == "player_1")

    assert player["stamina"] <= 40
    assert player["statusEffects"] == []


def test_social_actions_resolve_npc_reaction_from_relationship_and_traits():
    world = WorldState.from_dict({
        "currentDay": 3,
        "players": [{
            "id": "player_1",
            "health": 100,
            "stamina": 90,
            "stress": 10,
            "entities": {"companions": [], "possessions": [], "knownCharacters": [{
                "id": "npc_lena",
                "name": "Lena",
                "traits": ["cautious", "resourceful"],
                "status": "encountered",
                "lastSeenDay": 2,
            }]},
            "trustRelationships": {"npc_lena": {"trust": 0.78, "respect": 0.7, "loyalty": 0.6, "fear": 0.1, "resentment": 0.1, "affection": 0.6}},
        }],
        "resources": {"food": 3, "water": 5},
    })
    action = StructuredAction(
        actionId="trade-004",
        actionType="trade",
        actor="player_1",
        target="npc_lena",
        parameters={"seed": 7},
        timeCost=1,
        resourceCost={},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.socialOutcome["reaction"] in {"accepted", "countered", "declined"}
    assert result.socialOutcome["targetId"] == "npc_lena"
    assert result.socialOutcome["threshold"] >= 0
    assert any("Lena" in info.get("content", "") for info in result.informationDiscovered)


def test_simulation_rejects_unknown_entity_claim_about_another_player():
    world = WorldState.from_dict({
        "players": [
            {"id": "player_1", "entities": {"companions": [], "possessions": []}},
            {"id": "player_2", "entities": {"companions": [], "possessions": []}},
        ],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="attack-entity-001",
        actionType="attack",
        actor="player_1",
        target="player_2",
        parameters={"targetEntity": {"owner": "player_2", "type": "companion", "name": "Buddy"}},
        timeCost=1,
        resourceCost={"food": 1},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.rejected is True
    assert result.rejectionReason == "Player player_2 has no known companion named Buddy."
    assert result.timeConsumed == 0
    assert result.resourceChanges == {}


def test_simulation_ignores_empty_target_entity_placeholder():
    world = WorldState.from_dict({
        "players": [{"id": "player_1", "location": "shelter", "health": 100}],
        "locations": [{"id": "shelter", "discovered": True}],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="policy-placeholder-001",
        actionType="resource_policy",
        actor="player_1",
        target="settlement",
        parameters={"targetEntity": {}},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.rejected is False
    assert result.rejectionReason is None


def test_simulation_resolves_known_entity_claim_about_another_player():
    world = WorldState.from_dict({
        "players": [
            {"id": "player_1", "entities": {"companions": [], "possessions": []}},
            {"id": "player_2", "entities": {"companions": [{"name": "Buddy"}], "possessions": []}},
        ],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="attack-entity-002",
        actionType="attack",
        actor="player_1",
        target="player_2",
        parameters={"targetEntity": {"owner": "player_2", "type": "companion", "name": "Buddy"}},
        timeCost=1,
        resourceCost={"food": 1},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.rejected is False
    assert result.timeConsumed == 1


def test_simulation_makes_impossible_claim_a_no_effect_attempt():
    world = WorldState.from_dict({
        "players": [{"id": "player_1", "health": 100, "stress": 20}],
        "resources": {"food": 3},
    })
    action = StructuredAction(
        actionId="superpower-001",
        actionType="failed_attempt",
        actor="player_1",
        target="self",
        parameters={"physicallyImpossible": True},
        timeCost=2,
        resourceCost={"food": 99},
    )

    result = SimulationEngine(seed=7).apply(world, action)

    assert result.physicallyImpossible is True
    assert result.timeConsumed == 2
    assert result.resourceChanges == {}
    assert result.playerStatusChanges == []
    assert result.updatedWorldState["resources"]["food"] == 3
