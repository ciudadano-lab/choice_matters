from game_data import fresh_room_state
from simulation_engine import StructuredAction, SimulationEngine
from world_state import WorldState, generate_initial_world_state, materialize_poi


def test_generate_initial_world_state_has_three_layers():
    world = generate_initial_world_state([], seed=11)

    assert world.geography
    assert any(feature["type"] == "river" for feature in world.geography)
    assert any(feature["type"] == "highway" for feature in world.geography)

    assert world.locations
    assert any(location["category"] == "Residential" for location in world.locations)
    assert any(location["category"] == "Commercial" for location in world.locations)
    assert any(location["category"] == "Infrastructure" for location in world.locations)

    assert world.poiTemplates
    assert any(template["name"] == "The Red Door" for template in world.poiTemplates)


def test_materialize_poi_adds_a_discovered_location():
    world = generate_initial_world_state([], seed=11)

    poi = materialize_poi(world, "poi_the_red_door")

    assert poi is not None
    assert poi["name"] == "The Red Door"
    assert poi["discovered"] is True
    assert poi["visibleState"]["status"] == "known"
    assert any(location["id"] == poi["id"] for location in world.locations)


def test_generate_initial_world_state_includes_city_layout_and_procedural_traits():
    world = generate_initial_world_state([], seed=11)

    assert world.cityLayout["cityName"]
    assert world.cityLayout["districts"]
    assert world.cityLayout["roads"]
    assert any(location.get("history") for location in world.locations)
    assert any(location.get("factionControl") is not None for location in world.locations)


def test_world_state_separates_actual_player_and_rumored_layers():
    world = generate_initial_world_state([], seed=21)

    assert world.actualWorld["locations"]
    assert world.playerKnownWorld["locations"]
    assert world.rumoredWorld["rumors"]
    assert len(world.actualWorld["locations"]) >= len(world.playerKnownWorld["locations"])


def test_simulation_engine_registers_validated_discovery():
    world = generate_initial_world_state([], seed=7)
    world.proposedDiscoveries = [{
        "id": "proposal_search_1",
        "templateId": "poi_the_bunker",
        "source": "search",
        "confidence": 0.95,
        "kind": "poi",
    }]

    result = SimulationEngine(seed=7).apply(
        world,
        StructuredAction(
            actionId="action_search_1",
            actionType="search",
            actor="player_1",
            target="location_central_hospital_1",
        ),
    )

    assert len(result.updatedWorldState["locations"]) > len(world.locations)
    assert any(item.get("source") == "search" for item in result.updatedWorldState.get("proposedDiscoveries", []))


def test_fresh_room_state_assigns_unique_seeded_spawns_and_local_knowledge():
    room_state = fresh_room_state(seed=404)

    spawn_ids = {
        slot: room_state["players"][slot]["facts"].get("spawnLocationId")
        for slot in ["1", "2", "3"]
    }
    assert len(set(spawn_ids.values())) == 3
    assert all(room_state["players"][slot]["facts"].get("location") for slot in ["1", "2", "3"])
    assert all(room_state["players"][slot]["facts"].get("knownNearbyLocations") for slot in ["1", "2", "3"])
