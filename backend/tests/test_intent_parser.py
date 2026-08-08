from world_state import WorldState
from intent_parser import parse_player_intent


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
                    "currentActivity": "observing",
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
                    "currentActivity": "observing",
                },
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
                    "zombiePopulation": 0,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 1,
                    "dangerLevel": 1,
                },
                {
                    "id": "pharmacy",
                    "name": "Pharmacy",
                    "type": "building",
                    "coordinates": {"x": 2, "y": 1},
                    "discovered": True,
                    "actualState": {"status": "uncertain"},
                    "visibleState": {"status": "uncertain"},
                    "zombiePopulation": 2,
                    "resources": {"medical_supplies": 4},
                    "controllingFaction": None,
                    "securityLevel": 2,
                    "dangerLevel": 2,
                },
            ],
            "factions": [],
            "resources": {"water": 5, "food": 3, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )


def test_parses_multi_action_command():
    world_state = build_world_state()

    actions, error = parse_player_intent(
        "Reinforce the school windows with desks and send Player 2 to search the pharmacy.",
        actor="player_1",
        world_state=world_state,
    )

    assert error is None
    assert [action.actionType for action in actions] == ["reinforce", "search"]
    assert actions[0].target == "school"
    assert actions[1].actor == "player_2"
    assert actions[1].target == "pharmacy"


def test_rejects_actions_that_require_travel_without_transport():
    world_state = build_world_state()
    world_state.locations.append(
        {
            "id": "hospital",
            "name": "Hospital",
            "type": "building",
            "coordinates": {"x": 30, "y": 0},
            "discovered": False,
            "actualState": {"status": "uncertain"},
            "visibleState": {"status": "uncertain"},
            "zombiePopulation": 6,
            "resources": {"medical_supplies": 8},
            "controllingFaction": None,
            "securityLevel": 3,
            "dangerLevel": 4,
        }
    )

    actions, error = parse_player_intent("I want to search the hospital.", actor="player_1", world_state=world_state)

    assert actions == []
    assert error == "Action requires travel first.\nEstimated travel time: 2 days."
