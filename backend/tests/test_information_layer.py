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


def test_information_layer_resolves_name_in_id_keyed_location_mapping():
    state = {
        "currentDay": 1,
        "locations": {
            "location_clinic": {
                "name": "Clinic",
                "knownInformation": [],
                "visibleState": {"status": "unknown"},
            },
        },
    }

    InformationLayer().apply_information(
        state,
        {
            "source": "direct_observation",
            "reliability": 0.8,
            "timestamp": 1,
            "confidence": 0.8,
            "content": "The clinic is occupied.",
        },
        target_location="cLiNiC",
    )

    assert state["locations"]["location_clinic"]["knownInformation"][0]["content"] == (
        "The clinic is occupied."
    )


def test_player_scoped_discoveries_and_location_reports_do_not_leak():
    world = WorldState.from_dict({
        "players": [
            {"id": "player_1", "location": "school"},
            {"id": "player_2", "location": "school"},
        ],
        "locations": [{
            "id": "school",
            "name": "School",
            "discovered": True,
            "knownInformation": [],
            "visibleState": {"status": "unknown"},
        }],
        "factions": [{
            "id": "faction_hidden",
            "name": "Hidden Faction",
            "goals": ["secret_goal"],
            "resources": {"food": 99},
            "player_dispositions": {"player_2": -80},
            "ideology_tags": ["security"],
            "leader_npc_id": "npc_leader",
            "territory": ["school"],
            "strength": 4,
            "militaryStrength": 3,
            "stances": {"faction_other": "hostile"},
        }],
        "settlements": [{
            "id": "hidden_settlement",
            "name": "Hidden Camp",
            "location_id": "school",
            "location": "school",
            "faction_id": "faction_hidden",
            "status": "stable",
            "population": 12,
            "morale": 70,
            "unrest": 5,
            "defense": 20,
            "stores": {"food": 15},
            "attention_by_player": {"player_2": 50},
        }],
    })
    layer = InformationLayer()
    layer.apply_information(
        world,
        {
            "player_id": "player_2",
            "source": "direct_observation",
            "reliability": 0.9,
            "confidence": 0.9,
            "timestamp": 1,
            "content": "The camp is occupied.",
        },
        target_location="school",
    )
    layer.discover_faction(
        world,
        player_id="player_2",
        faction_id="faction_hidden",
        level="known",
    )
    layer.discover_settlement(
        world,
        player_id="player_2",
        settlement_id="hidden_settlement",
        level="known",
    )
    world.refresh_layers()

    player_a_visible = layer.get_visible_state(world, player_id="player_1")
    player_b_visible = layer.get_visible_state(world, player_id="player_2")
    player_a_world = layer.get_player_known_world(world, player_id="player_1")
    player_b_world = layer.get_player_known_world(world, player_id="player_2")

    assert player_a_visible["school"]["knownInformation"] == []
    assert player_a_visible["school"]["visibleState"] == {"status": "unknown"}
    assert player_b_visible["school"]["knownInformation"][0]["content"] == (
        "The camp is occupied."
    )
    assert player_a_world["factions"] == []
    assert player_a_world["settlements"] == []
    assert player_b_world["factions"][0]["discoveryLevel"] == "known"
    assert "goals" not in player_b_world["factions"][0]
    assert "resources" not in player_b_world["factions"][0]
    assert "player_dispositions" not in player_b_world["factions"][0]
    assert "attention_by_player" not in player_b_world["settlements"][0]


def test_discovery_levels_are_monotonic_and_control_visible_fields():
    world = WorldState.from_dict({
        "players": [{"id": "player_1"}],
        "factions": [{
            "id": "faction_known",
            "name": "Known Group",
            "goals": ["hidden"],
            "ideology_tags": ["cooperation"],
            "leader_npc_id": "npc_leader",
            "territory": ["school"],
            "strength": 3,
            "resources": {"food": 100},
        }],
        "settlements": [],
    })
    layer = InformationLayer()

    layer.discover_faction(
        world,
        player_id="player_1",
        faction_id="faction_known",
        level="met",
    )
    layer.discover_faction(
        world,
        player_id="player_1",
        faction_id="faction_known",
        level="heard_of",
    )
    met_view = layer.get_player_known_world(world, player_id="player_1")
    assert met_view["factions"][0]["discoveryLevel"] == "met"
    assert "ideology_tags" in met_view["factions"][0]
    assert "territory" not in met_view["factions"][0]
    assert "goals" not in met_view["factions"][0]

    layer.discover_faction(
        world,
        player_id="player_1",
        faction_id="faction_known",
        level="known",
    )
    known_view = layer.get_player_known_world(world, player_id="player_1")
    assert known_view["factions"][0]["discoveryLevel"] == "known"
    assert "territory" in known_view["factions"][0]


def test_client_state_projects_only_recipient_discoveries():
    from main import build_client_state

    world = WorldState.from_dict({
        "players": [
            {"id": "player_1", "location": "school"},
            {"id": "player_2", "location": "school"},
        ],
        "locations": [{"id": "school", "name": "School", "discovered": True}],
        "factions": [{
            "id": "faction_hidden",
            "name": "Hidden Faction",
            "goals": ["secret"],
            "resources": {"food": 10},
        }],
        "settlements": [{
            "id": "hidden_settlement",
            "name": "Hidden Settlement",
            "location_id": "school",
            "faction_id": "faction_hidden",
            "population": 10,
            "morale": 50,
            "stores": {"food": 10},
        }],
    })
    layer = InformationLayer()
    layer.discover_faction(
        world,
        player_id="player_2",
        faction_id="faction_hidden",
        level="met",
    )
    layer.discover_settlement(
        world,
        player_id="player_2",
        settlement_id="hidden_settlement",
        level="met",
    )
    room = {
        "players": {"1": {}, "2": {}},
        "world_state": world.to_dict(),
    }

    visible_to_a = build_client_state(room, 1)["world_state"]
    visible_to_b = build_client_state(room, 2)["world_state"]

    assert visible_to_a["factions"] == []
    assert visible_to_a["settlements"] == []
    assert visible_to_b["factions"][0]["id"] == "faction_hidden"
    assert visible_to_b["settlements"][0]["id"] == "hidden_settlement"
    assert "goals" not in visible_to_b["factions"][0]
    assert "resources" not in visible_to_b["factions"][0]
