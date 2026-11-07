import copy

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
