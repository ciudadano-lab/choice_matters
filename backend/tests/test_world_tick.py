from world_state import WorldState
from world_tick import WorldTick, WorldEventQueue


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
