import copy

from simulation_engine import SimulationEngine, StructuredAction
from world_state import WorldState


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
    assert first.updatedWorldState["players"][0]["location"] == "location_outpost"
    assert first.resourceChanges["water"] == -1
    assert first.locationChanges[0]["locationId"] == "location_outpost"
    assert first.worldEventsTriggered[0]["type"] in {"encounter", "travel"}
