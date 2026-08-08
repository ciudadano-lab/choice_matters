from world_state import WorldState
from relationships import RelationshipSystem, PersonalityProfile


def build_world_state():
    return WorldState.from_dict(
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
                    "affection": 0.5,
                    "respect": 0.5,
                    "currentActivity": "observing",
                    "personality": {
                        "courage": 0.7,
                        "paranoia": 0.3,
                        "aggression": 0.2,
                        "altruism": 0.8,
                        "ambition": 0.5,
                    },
                    "goals": ["protect others"],
                },
                {
                    "id": "player_2",
                    "name": "Player 2",
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
                    "affection": 0.5,
                    "respect": 0.5,
                    "currentActivity": "observing",
                    "personality": {
                        "courage": 0.6,
                        "paranoia": 0.6,
                        "aggression": 0.4,
                        "altruism": 0.3,
                        "ambition": 0.7,
                    },
                    "goals": ["survive"],
                },
            ],
            "locations": [],
            "factions": [],
            "resources": {"water": 3, "food": 2, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )


def test_repeated_dangerous_missions_increase_resentment_and_fear():
    world_state = build_world_state()
    system = RelationshipSystem(seed=11)

    for _ in range(3):
        system.apply_event(world_state, "player_1", "player_2", "mission_risk", severity=3)

    player = next(player for player in world_state.players if player["id"] == "player_2")
    rel = player["trustRelationships"]["player_1"]
    assert rel["trust"] < 0.5
    assert rel["resentment"] > 0.4
    assert rel["fear"] > 0.2


def test_saving_someone_increases_trust_and_loyalty():
    world_state = build_world_state()
    system = RelationshipSystem(seed=5)

    system.apply_event(world_state, "player_1", "player_2", "save", severity=2)

    player = next(player for player in world_state.players if player["id"] == "player_2")
    rel = player["trustRelationships"]["player_1"]
    assert rel["trust"] > 0.5
    assert rel["loyalty"] > 0.5
