from world_state import WorldState, generate_initial_world_state, materialize_poi, materialize_emergent_location
from information_layer import InformationLayer


def test_information_layer_reveals_only_credible_information():
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
                    "id": "hospital",
                    "name": "Hospital",
                    "type": "building",
                    "coordinates": {"x": 10, "y": 2},
                    "discovered": True,
                    "actualState": {"zombies": 27, "hostileFactionMembers": 12},
                    "visibleState": {"status": "unknown"},
                    "zombiePopulation": 0,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 3,
                    "dangerLevel": 4,
                }
            ],
            "factions": [],
            "resources": {"water": 3, "food": 2, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )

    layer = InformationLayer()
    report = {
        "source": "survivor_report",
        "reliability": 0.35,
        "timestamp": 1,
        "confidence": 0.3,
        "content": "The hospital is abandoned.",
    }
    layer.apply_information(world_state, report, target_location="hospital")
    visible = layer.get_visible_state(world_state, player_id="player_1")

    assert visible["hospital"]["knownInformation"]
    assert visible["hospital"]["knownInformation"][-1]["content"] == "The hospital is abandoned."
    assert visible["hospital"]["visibleState"]["status"] == "unknown"


def test_information_layer_can_reveal_truth_with_sufficient_evidence():
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
                    "id": "hospital",
                    "name": "Hospital",
                    "type": "building",
                    "coordinates": {"x": 10, "y": 2},
                    "discovered": True,
                    "actualState": {"zombies": 27, "hostileFactionMembers": 12},
                    "visibleState": {"status": "unknown"},
                    "zombiePopulation": 0,
                    "resources": {},
                    "controllingFaction": None,
                    "securityLevel": 3,
                    "dangerLevel": 4,
                }
            ],
            "factions": [],
            "resources": {"water": 3, "food": 2, "medical_supplies": 1},
            "activeEvents": [],
            "discoveredInformation": [],
            "rumors": [],
            "worldHistory": [],
            "relationships": [],
        }
    )

    layer = InformationLayer()
    layer.apply_information(world_state, {"source": "radio_report", "reliability": 0.75, "timestamp": 1, "confidence": 0.8, "content": "Movement detected around the hospital."}, target_location="hospital")
    layer.apply_information(world_state, {"source": "direct_observation", "reliability": 0.95, "timestamp": 2, "confidence": 0.9, "content": "The hospital has active movement and light on the upper floor."}, target_location="hospital")
    visible = layer.get_visible_state(world_state, player_id="player_1")

    assert visible["hospital"]["visibleState"]["status"] == "hostile_activity"
    assert visible["hospital"]["visibleState"]["zombies"] == 27


def test_world_state_tracks_known_discoverable_and_emergent_locations():
    world_state = generate_initial_world_state([], seed=11)

    known_locations = [location for location in world_state.locations if location.get("locationKind") == "known"]
    assert known_locations

    discoverable = materialize_poi(world_state, "poi_the_red_door")
    assert discoverable is not None
    assert discoverable["locationKind"] == "discoverable"

    emergent = materialize_emergent_location(world_state, "emergent_1")
    assert emergent is not None
    assert emergent["locationKind"] == "emergent"


def test_information_layer_progresses_map_reveal_state():
    world_state = generate_initial_world_state([], seed=11)
    world_state.currentDay = 1
    layer = InformationLayer()

    initial_view = layer.get_map_reveal_state(world_state, player_id="player_1")
    assert initial_view["statusText"] == "Your neighborhood."
    assert initial_view["revealedRoads"] is False

    layer.apply_information(
        world_state,
        {"source": "document", "reliability": 0.9, "timestamp": 5, "confidence": 0.8, "content": "An old road map reveals the major roads around the district."},
        target_location="location_central_hospital_1",
    )
    road_view = layer.get_map_reveal_state(world_state, player_id="player_1")
    assert road_view["revealedRoads"] is True

    layer.apply_information(
        world_state,
        {"source": "survivor_report", "reliability": 0.8, "timestamp": 20, "confidence": 0.8, "content": "The hospital is somewhere north of the school."},
        target_location="location_central_hospital_1",
    )
    final_view = layer.get_map_reveal_state(world_state, player_id="player_1")
    assert final_view["revealedApproximateLocations"] is True
