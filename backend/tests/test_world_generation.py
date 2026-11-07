from game_data import fresh_room_state
from simulation_engine import StructuredAction, SimulationEngine
from world_state import LOCATION_RESOURCE_PROFILES, WorldState, _assign_coordinates, _make_location, _generate_districts, generate_initial_world_state, materialize_poi, settlement_tier


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
    assert poi["connections"]
    assert poi["id"] in world.cityLayout["roadNetwork"]["access"]


def test_generate_initial_world_state_includes_city_layout_and_procedural_traits():
    world = generate_initial_world_state([], seed=11)

    assert world.cityLayout["cityName"]
    assert world.cityLayout["districts"]
    assert world.cityLayout["roads"]
    assert world.cityLayout["roadNetwork"]["version"] == 2
    assert world.cityLayout["roadNetwork"]["subdiv"] == 4
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


def test_fresh_room_state_changes_spawn_assignments_between_room_seeds():
    first_room = fresh_room_state(seed=404)
    second_room = fresh_room_state(seed=405)

    first_assignments = {
        slot: first_room["players"][slot]["facts"].get("spawnLocationId")
        for slot in ["1", "2", "3"]
    }
    second_assignments = {
        slot: second_room["players"][slot]["facts"].get("spawnLocationId")
        for slot in ["1", "2", "3"]
    }

    assert first_assignments != second_assignments


def test_world_players_start_with_empty_entity_ledgers():
    world = generate_initial_world_state([], seed=11)

    assert all(player["entities"] == {"companions": [], "possessions": []} for player in world.players)


def test_location_resources_follow_type_profiles_deterministically():
    district = {"id": "district_test"}
    hospital = _make_location("Test Hospital", "Infrastructure", "hospital", district, 404, 1, discovered=True, visibility="known")
    same_hospital = _make_location("Test Hospital", "Infrastructure", "hospital", district, 404, 1, discovered=True, visibility="known")

    assert set(hospital["resources"]) == set(LOCATION_RESOURCE_PROFILES["hospital"])
    assert hospital["resourcesMax"] == hospital["resources"]
    assert hospital["resources"] == same_hospital["resources"]
    assert 2 <= hospital["resources"]["medical_supplies"] <= 6


def test_secret_cache_gets_one_seeded_wildcard_resource():
    cache = _make_location("Test Cache", "Discoverable", "secret_cache", {"id": "district_test"}, 404, 1, discovered=False, visibility="unknown")

    assert len(cache["resources"]) == 1
    assert 3 <= next(iter(cache["resources"].values())) <= 8


def test_unknown_location_type_uses_defensive_food_fallback():
    location = _make_location("Future Site", "Test", "future_type", {"id": "district_test"}, 404, 1, discovered=True, visibility="known")

    assert set(location["resources"]) == {"food"}
    assert 0 <= location["resources"]["food"] <= 2


def test_settlement_tier_thresholds_have_no_upper_population_cap():
    assert settlement_tier(1) == 1
    assert settlement_tier(30) == 1
    assert settlement_tier(31) == 2
    assert settlement_tier(100) == 2
    assert settlement_tier(101) == 3
    assert settlement_tier(200) == 3
    assert settlement_tier(201) == 4
    assert settlement_tier(500) == 4
    assert settlement_tier(10000) == 4


def test_coordinate_assignment_relaxes_spacing_without_collisions():
    districts = [{"id": "district_test", "area": {"x": 0, "y": 0, "width": 8, "height": 8}}]
    location_ids = [f"location_{index}" for index in range(5)]
    assigned = _assign_coordinates({"district_test": location_ids}, districts, seed=404)

    assert len(set(assigned.values())) == len(location_ids)
    distances = [
        ((assigned[left][0] - assigned[right][0]) ** 2 + (assigned[left][1] - assigned[right][1]) ** 2) ** 0.5
        for index, left in enumerate(location_ids)
        for right in location_ids[index + 1:]
    ]
    assert min(distances) >= 1.41


def test_same_role_locations_balance_across_duplicate_role_districts():
    districts = _generate_districts(404)
    duplicate_role = next(
        role for role in {district["role"] for district in districts}
        if sum(district["role"] == role for district in districts) > 1
    )
    role_districts = [district for district in districts if district["role"] == duplicate_role]
    locations = generate_initial_world_state([], seed=404).locations
    assigned = [location for location in locations if location["district"] in {district["id"] for district in role_districts}]

    if assigned:
        counts = {district["id"]: 0 for district in role_districts}
        for location in assigned:
            counts[location["district"]] += 1
        assert max(counts.values()) - min(counts.values()) <= 1
