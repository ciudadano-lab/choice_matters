import pytest

from game_data import TACTICAL_ACTION_TYPES
from world_state import WorldState
from intent_parser import IntentParser, parse_player_intent


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


def test_parser_coverage_matches_current_tactical_taxonomy():
    parser = IntentParser()

    assert set(parser._action_patterns) == set(TACTICAL_ACTION_TYPES)
    assert set(parser._GENERIC_ACTION_CONFIG) | set(parser._SPECIAL_ACTION_TYPES) == set(TACTICAL_ACTION_TYPES)


@pytest.mark.parametrize("command, expected_type", [
    ("Travel to the school.", "travel"),
    ("Search the pharmacy.", "search"),
    ("Scavenge the pharmacy.", "scavenge"),
    ("Gather supplies.", "gather"),
    ("Build a shelter.", "build"),
    ("Reinforce the school.", "reinforce"),
    ("Rest at the shelter.", "rest"),
    ("Communicate with the survivors.", "communicate"),
    ("Negotiate with the survivors.", "negotiate"),
    ("Attack the school.", "attack"),
    ("Defend the school.", "defend"),
    ("Trade with the survivors.", "trade"),
    ("Recruit survivors.", "recruit"),
    ("Send a mission to Player 2.", "send_mission"),
    ("Establish a settlement at the school.", "establish_settlement"),
])
def test_parser_recognizes_each_tactical_action(command, expected_type):
    actions, error = parse_player_intent(command, actor="player_1", world_state=build_world_state())

    assert error is None
    assert [action.actionType for action in actions] == [expected_type]
