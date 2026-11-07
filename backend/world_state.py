from __future__ import annotations

import copy
import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

from road_network import attach_location, generate_road_network


DEFAULT_LOCATION = {
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
}

GRID_SIZE = 20


def settlement_tier(population: int) -> int:
    if population <= 30:
        return 1
    if population <= 100:
        return 2
    if population <= 200:
        return 3
    return 4

LOCATION_RESOURCE_PROFILES: dict[str, dict[str, tuple[int, int]]] = {
    "hospital": {"medical_supplies": (2, 6)},
    "police_station": {"ammunition": (1, 3), "intel": (1, 2)},
    "school": {"building_materials": (0, 2), "food": (0, 2)},
    "supermarket": {"food": (3, 8), "water": (1, 3)},
    "apartment": {"food": (0, 2), "water": (0, 2)},
    "house": {"food": (0, 2), "building_materials": (0, 1)},
    "garage": {"tools": (1, 3), "fuel": (0, 2)},
    "pharmacy": {"medical_supplies": (2, 5)},
    "hardware_store": {"tools": (2, 6), "building_materials": (1, 3)},
    "restaurant": {"food": (2, 5)},
    "warehouse": {"building_materials": (2, 6), "tools": (0, 2)},
    "fire_station": {"medical_supplies": (0, 2), "tools": (1, 3)},
    "power_station": {"fuel": (2, 5), "electronics": (1, 3)},
    "farm": {"food": (3, 8), "water": (1, 3)},
    "clinic": {"medical_supplies": (2, 5)},
    "workshop": {"tools": (2, 5), "building_materials": (1, 3)},
    "radio_station": {"electronics": (2, 5), "intel": (1, 3)},
    "rail_yard": {"building_materials": (2, 5), "fuel": (1, 3)},
    "watchtower": {"intel": (1, 3), "ammunition": (0, 2)},
    "library": {"intel": (2, 6)},
    "theater": {"luxury_goods": (1, 3)},
    "townhomes": {"food": (0, 2), "water": (0, 2)},
    "cul_de_sac": {"food": (0, 2)},
    "scrapyard": {"building_materials": (2, 6), "tools": (1, 3)},
    "freight_depot": {"building_materials": (2, 5), "fuel": (1, 3)},
    "fishing_coop": {"food": (2, 5), "tools": (0, 2)},
    "ferry_landing": {"fuel": (0, 2), "intel": (0, 1)},
    "shopping_mall": {"luxury_goods": (2, 6), "electronics": (1, 3)},
    "cinema": {"luxury_goods": (1, 4), "electronics": (0, 2)},
    "laundromat": {"water": (1, 3), "luxury_goods": (0, 1)},
    "motel": {"water": (0, 2), "luxury_goods": (0, 2)},
    "courthouse": {"intel": (2, 5)},
    "armory": {"ammunition": (3, 8)},
    "prison": {"ammunition": (1, 3), "building_materials": (0, 2)},
    "silo": {"food": (2, 6)},
    "gas_station": {"fuel": (2, 6)},
    "barn": {"food": (1, 4), "building_materials": (0, 2)},
    "campground": {"food": (0, 2), "water": (0, 2)},
    "ranger_station": {"intel": (1, 3), "tools": (0, 2)},
    "abandoned_nightclub": {"luxury_goods": (1, 3), "electronics": (0, 2)},
    "fortified_church": {"medical_supplies": (0, 2), "food": (0, 2)},
    "military_bunker": {"ammunition": (2, 6), "medical_supplies": (1, 3)},
    "smuggler_warehouse": {"ammunition": (1, 4), "luxury_goods": (1, 4)},
    "underground_tunnel": {"intel": (0, 2)},
    "marina": {"food": (1, 3), "fuel": (0, 2)},
    "greenhouse": {"food": (2, 6)},
    "military_bunker_large": {"ammunition": (3, 8), "medical_supplies": (2, 5)},
    "checkpoint": {"ammunition": (1, 3), "intel": (1, 3)},
    "looted_store": {"electronics": (0, 2), "luxury_goods": (0, 2)},
    "overrun_farmhouse": {"food": (0, 3)},
    "nature_trail": {"food": (0, 1)},
    "hunting_cabin": {"food": (1, 4), "ammunition": (0, 2)},
}

SECRET_CACHE_RESOURCE_TYPES = [
    "food", "water", "medical_supplies", "fuel", "tools",
    "building_materials", "ammunition", "intel", "electronics", "luxury_goods",
]


def _coerce_dict(value: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return copy.deepcopy(default or {})


def _coerce_list(value: Any, default: list[Any] | None = None) -> list[Any]:
    if isinstance(value, list):
        return copy.deepcopy(value)
    return copy.deepcopy(default or [])


def _coerce_str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_int(value: Any, default: int = 0) -> int:
    return int(value) if isinstance(value, int) else default


def _coerce_bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _has_grid_locations(locations: list[dict[str, Any]], grid_size: int = GRID_SIZE) -> bool:
    occupied: set[tuple[int, int]] = set()
    for location in locations:
        coordinates = location.get("coordinates")
        if not isinstance(coordinates, dict):
            return False
        x, y = coordinates.get("x"), coordinates.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            return False
        if not (0 <= x < grid_size and 0 <= y < grid_size) or (x, y) in occupied:
            return False
        occupied.add((x, y))
    return True


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _stable_int(value: str, minimum: int = 0, maximum: int = 100) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    base = int(digest[:16], 16)
    return minimum + (base % (maximum - minimum + 1))


def _stable_choice(value: str, options: list[Any]) -> Any:
    if not options:
        raise ValueError("No options provided to _stable_choice")
    index = _stable_int(value, 0, len(options) - 1)
    return options[index]


def _make_coordinate(seed: str, field_name: str, low: int, high: int) -> int:
    return _stable_int(f"{seed}:{field_name}", low, high)


def _make_id(name: str, index: int) -> str:
    normalized = name.lower().replace(" ", "_").replace("'", "")
    return f"location_{normalized}_{index}"


def _build_world_layers(seed: int, locations: list[dict[str, Any]], rumors: list[dict[str, Any]], *, geography: list[dict[str, Any]] | None = None, factions: list[dict[str, Any]] | None = None, resources: dict[str, Any] | None = None, cityLayout: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    actual_world = {
        "seed": seed,
        "locations": copy.deepcopy(locations),
        "geography": copy.deepcopy(geography or []),
        "factions": copy.deepcopy(factions or []),
        "resources": copy.deepcopy(resources or {}),
        "cityLayout": copy.deepcopy(cityLayout or {}),
    }
    known_locations = [
        copy.deepcopy(location)
        for location in locations
        if location.get("discovered", False) and location.get("locationKind") != "discoverable"
    ]
    player_known_world = {
        "seed": seed,
        "locations": known_locations,
        "rumors": copy.deepcopy(rumors),
    }
    rumored_world = {
        "seed": seed,
        "rumors": copy.deepcopy(rumors),
        "pendingReports": [],
    }
    return actual_world, player_known_world, rumored_world


def _build_spawn_for_player(seed: int, slot: int, locations: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_locations = [
        location for location in locations
        if location.get("discovered", False) and location.get("locationKind") != "discoverable"
    ]
    if not candidate_locations:
        candidate_locations = locations

    spawn_seed = _stable_int(f"spawn:{seed}:{slot}", 0, 1000)
    room_spawn_seeds = [
        spawn_seed if candidate_slot == slot else _stable_int(f"spawn:{seed}:{candidate_slot}", 0, 1000)
        for candidate_slot in (1, 2, 3)
    ]
    shuffled_locations = list(candidate_locations)
    shuffle_seed = _stable_int(
        f"spawn-order:{seed}:{','.join(str(value) for value in sorted(room_spawn_seeds))}",
        0,
        2**32 - 1,
    )
    random.Random(shuffle_seed).shuffle(shuffled_locations)
    index = (slot - 1) % len(shuffled_locations)
    base_location = shuffled_locations[index]
    spawn_coordinate = {
        "x": base_location.get("coordinates", {}).get("x", 0),
        "y": base_location.get("coordinates", {}).get("y", 0),
    }
    nearby = [
        location for location in candidate_locations
        if location.get("id") != base_location.get("id")
        and abs(location.get("coordinates", {}).get("x", 0) - spawn_coordinate["x"]) <= 2
        and abs(location.get("coordinates", {}).get("y", 0) - spawn_coordinate["y"]) <= 2
    ][:3]
    nearby_names = [location.get("name", "the area") for location in nearby]
    if not nearby_names:
        nearby_names = [base_location.get("name", "the area")]

    return {
        "spawnLocationId": base_location.get("id"),
        "spawnLocationName": base_location.get("name"),
        "spawnCoordinates": spawn_coordinate,
        "nearbyLocationIds": [location.get("id") for location in nearby],
        "knownNearbyLocations": nearby_names,
        "spawnSeed": spawn_seed,
    }


def assign_spawn_to_player(player: dict[str, Any], seed: int, slot: int, locations: list[dict[str, Any]]) -> dict[str, Any]:
    spawn_info = _build_spawn_for_player(seed, slot, locations)
    player["spawnLocationId"] = spawn_info["spawnLocationId"]
    player["spawnLocationName"] = spawn_info["spawnLocationName"]
    player["spawnCoordinates"] = spawn_info["spawnCoordinates"]
    player["nearbyLocationIds"] = spawn_info["nearbyLocationIds"]
    player["knownNearbyLocations"] = spawn_info["knownNearbyLocations"]
    player["spawnSeed"] = spawn_info["spawnSeed"]
    player.setdefault("location", spawn_info["spawnLocationName"])
    player.setdefault("statusEffects", [])

    facts = player.get("facts")
    if isinstance(facts, dict):
        facts["location"] = spawn_info["spawnLocationName"]
        facts["spawnLocationId"] = spawn_info["spawnLocationId"]
        facts["spawnLocationName"] = spawn_info["spawnLocationName"]
        facts["spawnCoordinates"] = spawn_info["spawnCoordinates"]
        facts["nearbyLocationIds"] = spawn_info["nearbyLocationIds"]
        facts["knownNearbyLocations"] = spawn_info["knownNearbyLocations"]
        facts["spawnSeed"] = spawn_info["spawnSeed"]
    return spawn_info


def _ensure_player_entities(player: dict[str, Any]) -> None:
    entities = player.get("entities")
    if not isinstance(entities, dict):
        entities = {}
        player["entities"] = entities
    for entity_type in ("companions", "possessions", "knownCharacters"):
        if not isinstance(entities.get(entity_type), list):
            entities[entity_type] = []
    if not isinstance(player.get("pendingEvents"), list):
        player["pendingEvents"] = []
    if not isinstance(player.get("statusEffects"), list):
        player["statusEffects"] = []
    if not isinstance(player.get("statusEffects"), list):
        player["statusEffects"] = []
    if not isinstance(player.get("statusEffects"), list):
        player["statusEffects"] = []


def _generate_geography(seed: int) -> list[dict[str, Any]]:
    highway_y = _make_coordinate(f"{seed}:highway", "y", 2, 7)
    river_x = _make_coordinate(f"{seed}:river", "x", 1, 8)
    return [
        {
            "type": "highway",
            "name": "Old State Highway",
            "path": [{"x": 0, "y": highway_y}, {"x": 9, "y": highway_y}],
        },
        {
            "type": "river",
            "name": "Ashen River",
            "path": [{"x": river_x, "y": 0}, {"x": river_x, "y": 9}],
        },
        {
            "type": "road",
            "name": "Northridge Road",
            "path": [{"x": 0, "y": 4}, {"x": 9, "y": 4}],
        },
        {
            "type": "forest",
            "name": "Pine Hollow",
            "area": [{"x": 0, "y": 7}, {"x": 3, "y": 9}],
        },
        {
            "type": "hills",
            "name": "Gravel Ridge",
            "area": [{"x": 6, "y": 6}, {"x": 9, "y": 9}],
        },
        {
            "type": "urban",
            "name": "Old Quarter",
            "area": [{"x": 3, "y": 2}, {"x": 6, "y": 5}],
        },
        {
            "type": "industrial",
            "name": "South Works",
            "area": [{"x": 7, "y": 1}, {"x": 9, "y": 3}],
        },
    ]


def _zone_center(area: dict[str, int], seed: str) -> tuple[int, int]:
    x = _make_coordinate(seed, "x", area["x"], area["x"] + area.get("width", 0))
    y = _make_coordinate(seed, "y", area["y"], area["y"] + area.get("height", 0))
    return x, y


def _generate_districts(seed: int) -> list[dict[str, Any]]:
    role_names = {
        "core": ("Downtown", "Central", "Civic", "Market"),
        "residential": ("Maple", "Westwood", "Cedar", "Birchwood"),
        "industry": ("Forge", "Railworks", "South Works", "Ironworks"),
        "trade": ("Riverside", "Canal", "Harbor", "Ferry"),
        "commercial": ("Golden", "Broadway", "Sunrise", "Plaza"),
        "institutional": ("County", "Union", "Fort", "Civic"),
        "rural": ("Greenway", "Miller", "Crossroads", "Farmland"),
        "wilderness": ("Pine Hollow", "Northwood", "Ranger", "Wildwood"),
        "outskirts": ("Outer", "Boundary", "Edge", "Perimeter"),
    }
    roles = ["core", "residential", "industry", "trade", "commercial", "institutional", "rural", "wilderness"]
    district_count = _stable_int(f"district-count:{seed}", 8, 11)
    for index in range(district_count - len(roles)):
        roles.append(_stable_choice(f"district-role:{seed}:{index}", roles[1:]))

    def max_pieces(area: tuple[int, int, int, int]) -> int:
        _, _, width, height = area
        return (width // 3) * (height // 3)

    def split_options(area: tuple[int, int, int, int]) -> list[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
        x, y, width, height = area
        options = []
        if width >= 6:
            for cut in range(3, width - 2):
                options.append(((x, y, cut, height), (x + cut, y, width - cut, height)))
        if height >= 6:
            for cut in range(3, height - 2):
                options.append(((x, y, width, cut), (x, y + cut, width, height - cut)))
        return options

    def partition(area: tuple[int, int, int, int], pieces: int, key: str) -> list[tuple[int, int, int, int]] | None:
        if pieces == 1:
            return [area]
        options = split_options(area)
        options = [option for option in options if max_pieces(option[0]) + max_pieces(option[1]) >= pieces]
        options.sort(key=lambda option: _stable_int(f"{key}:{option}", 0, 2**32 - 1))
        for left, right in options:
            left_max = max_pieces(left)
            right_max = max_pieces(right)
            allocations = [value for value in range(1, pieces) if value <= left_max and pieces - value <= right_max]
            allocations.sort(key=lambda value: _stable_int(f"{key}:{left}:{right}:{value}", 0, 2**32 - 1))
            for left_pieces in allocations:
                left_result = partition(left, left_pieces, f"{key}:l:{left_pieces}")
                if left_result is None:
                    continue
                right_result = partition(right, pieces - left_pieces, f"{key}:r:{pieces - left_pieces}")
                if right_result is not None:
                    return left_result + right_result
        return None

    areas = partition((0, 0, GRID_SIZE, GRID_SIZE), district_count, f"district-partition:{seed}")
    if areas is None:
        raise RuntimeError(f"Unable to partition {GRID_SIZE}x{GRID_SIZE} grid into {district_count} districts")
    role_demand = {
        "core": 10,
        "residential": 9,
        "industry": 9,
        "trade": 7,
        "commercial": 5,
        "institutional": 5,
        "rural": 5,
        "wilderness": 4,
    }
    areas.sort(key=lambda area: (-area[2] * area[3], area[1], area[0]))
    roles.sort(key=lambda role: (-role_demand.get(role, 0), role))
    districts = []
    for index, (role, (x, y, width, height)) in enumerate(zip(roles, areas)):
        adjective = _stable_choice(f"district-name:{seed}:{index}:adjective", role_names[role])
        districts.append({
            "id": f"district_{index + 1}",
            "name": f"{adjective} {role.replace('_', ' ').title()}",
            "area": {"x": x, "y": y, "width": width, "height": height},
            "role": role,
        })
    return districts


def _shuffled_cells(district: dict[str, Any], seed: int) -> list[tuple[int, int]]:
    """Deterministic Fisher-Yates shuffle of every (x, y) cell in a
    district, seeded per-district so results are reproducible per room
    seed."""
    area = district["area"]
    cells = [(x, y) for y in range(area["y"], area["y"] + area["height"])
             for x in range(area["x"], area["x"] + area["width"])]
    rng_seed = f"{seed}:{district['id']}:cellshuffle"
    for i in range(len(cells) - 1, 0, -1):
        j = _stable_int(f"{rng_seed}:{i}", 0, i)
        cells[i], cells[j] = cells[j], cells[i]
    return cells


def _assign_coordinates(
    locations_by_district: dict[str, list[str]],
    districts: list[dict[str, Any]],
    seed: int,
    min_separation: float = 3.0,
) -> dict[str, tuple[int, int]]:
    """Assigns every location a globally-unique (x, y) cell. Each district
    gets its own deterministically-shuffled cell order so results are
    stable per seed; if a district somehow runs out of cells, remaining
    locations fall back to any unused cell on the whole grid, keeping the
    guarantee of zero collisions."""
    assigned: dict[str, tuple[int, int]] = {}
    used: set[tuple[int, int]] = set()
    all_cells = [(x, y) for y in range(GRID_SIZE) for x in range(GRID_SIZE)]

    for district in districts:
        district_id = district["id"]
        loc_ids = locations_by_district.get(district_id, [])
        cell_order = _shuffled_cells(district, seed)
        placed_in_district: list[tuple[int, int]] = []
        for loc_id in loc_ids:
            cell = None
            for required_separation in (
                min_separation,
                min_separation * 0.66,
                min_separation * 0.33,
                0,
            ):
                cell = next(
                    (
                        candidate
                        for candidate in cell_order
                        if candidate not in used
                        and all(
                            ((candidate[0] - placed[0]) ** 2 + (candidate[1] - placed[1]) ** 2) ** 0.5
                            >= required_separation
                            for placed in placed_in_district
                        )
                    ),
                    None,
                )
                if cell is not None:
                    break
            if cell is None:
                cell = next(c for c in all_cells if c not in used)
            used.add(cell)
            placed_in_district.append(cell)
            assigned[loc_id] = cell
    return assigned





def _make_location(name: str, category: str, loc_type: str, district: dict[str, Any], seed: int, index: int, *, discovered: bool, visibility: str, location_kind: str = "known") -> dict[str, Any]:
    history_options = [
        "A former community center now used as a shelter.",
        "A place once prized by locals before the outbreak.",
        "The building endured years of neglect before the collapse.",
        "Evidence suggests this site was repurposed during the first weeks.",
    ]
    faction_options = ["survivors", "raiders", "militia"]
    if loc_type == "secret_cache":
        resource_type = _stable_choice(f"{seed}:{name}:{index}:secret_resource", SECRET_CACHE_RESOURCE_TYPES)
        resources = {resource_type: _stable_int(f"{seed}:{name}:{index}:{resource_type}", 3, 8)}
    else:
        resource_profile = LOCATION_RESOURCE_PROFILES.get(loc_type)
        if resource_profile is None:
            resources = {"food": _stable_int(f"{seed}:{name}:{index}:food", 0, 2)}
        else:
            resources = {
                resource: _stable_int(f"{seed}:{name}:{index}:{resource}", minimum, maximum)
                for resource, (minimum, maximum) in resource_profile.items()
            }
    return {
        "id": _make_id(name, index),
        "name": name,
        "type": loc_type,
        "category": category,
        "district": district["id"],
        "coordinates": {"x": 0, "y": 0},  # assigned later by _assign_coordinates
        "discovered": discovered,
        "locationKind": location_kind,
        "actualState": {"status": "uncertain" if not discovered else "secure"},
        "visibleState": {"status": visibility},
        "zombiePopulation": _stable_int(f"{seed}:{name}:{index}:zombies", 0, 4 if discovered else 6),
        "resources": resources,
        "resourcesMax": copy.deepcopy(resources),
        "controllingFaction": _stable_choice(f"{seed}:{name}:{index}:faction", faction_options),
        "factionControl": _stable_choice(f"{seed}:{name}:{index}:faction_control", faction_options),
        "securityLevel": _stable_int(f"{seed}:{name}:{index}:security", 1, 3),
        "dangerLevel": _stable_int(f"{seed}:{name}:{index}:danger", 1, 4),
        "history": _stable_choice(f"{seed}:{name}:{index}:history", history_options),
        "secret": f"{name} has a hidden cache or story tied to the outbreak.",
        "connections": [],
    }


def _generate_locations(seed: int) -> list[dict[str, Any]]:
    districts = _generate_districts(seed)
    district_by_id = {d["id"]: d for d in districts}
    districts_by_role: dict[str, list[dict[str, Any]]] = {}
    for district in districts:
        districts_by_role.setdefault(district["role"], []).append(district)

    known_locations = [
        ("Central Hospital", "Infrastructure", "hospital", "known", "core"),
        ("Northside Police Station", "Infrastructure", "police_station", "known", "core"),
        ("Westwood School", "Infrastructure", "school", "known", "residential"),
        ("East Market Supermarket", "Commercial", "supermarket", "known", "core"),
    ]
    generic_templates = [
        ("Riverside Apartments", "Residential", "apartment", "residential"),
        ("Maple Houses", "Residential", "house", "residential"),
        ("Hollow Garage", "Residential", "garage", "residential"),
        ("Corner Pharmacy", "Commercial", "pharmacy", "core"),
        ("Rusty Hardware", "Commercial", "hardware_store", "industry"),
        ("Salty Diner", "Commercial", "restaurant", "core"),
        ("Iron Warehouse", "Commercial", "warehouse", "industry"),
        ("Beacon Fire Station", "Infrastructure", "fire_station", "core"),
        ("East Power Station", "Infrastructure", "power_station", "industry"),
        ("Greenway Farmstead", "Resource", "farm", "rural"),
        ("Millbrook Clinic", "Infrastructure", "clinic", "residential"),
        ("Dockside Workshop", "Infrastructure", "workshop", "trade"),
        ("Highline Radio Relay", "Infrastructure", "radio_station", "industry"),
        ("Old Rail Yard", "Industrial", "rail_yard", "industry"),
        ("Cedar Watchtower", "Infrastructure", "watchtower", "institutional"),
        ("Downtown Library", "Infrastructure", "library", "core"),
        ("Grand Regency Theater", "Commercial", "theater", "core"),
        ("Birchwood Townhomes", "Residential", "townhomes", "residential"),
        ("Cul-de-Sac Court", "Residential", "cul_de_sac", "residential"),
        ("Scrapyard Metalworks", "Industrial", "scrapyard", "industry"),
        ("Freight Rail Depot", "Infrastructure", "freight_depot", "industry"),
        ("Fisherman's Co-op", "Commercial", "fishing_coop", "trade"),
        ("Old Ferry Landing", "Infrastructure", "ferry_landing", "trade"),
        ("Golden Plaza Mall", "Commercial", "shopping_mall", "commercial"),
        ("Silver Screen Cinema", "Commercial", "cinema", "commercial"),
        ("Sunrise Laundromat", "Commercial", "laundromat", "commercial"),
        ("Budget Motel 9", "Commercial", "motel", "commercial"),
        ("County Courthouse", "Infrastructure", "courthouse", "institutional"),
        ("National Guard Armory", "Infrastructure", "armory", "institutional"),
        ("Riverbend Correctional Facility", "Infrastructure", "prison", "institutional"),
        ("Miller Family Farm", "Resource", "farm", "rural"),
        ("Sunset Grain Silo", "Resource", "silo", "rural"),
        ("Dusty Crossroads Gas Station", "Commercial", "gas_station", "rural"),
        ("Old Barn Shelter", "Residential", "barn", "rural"),
        ("Pine Hollow Campground", "Infrastructure", "campground", "wilderness"),
        ("Ranger Lookout Station", "Infrastructure", "ranger_station", "wilderness"),
    ]
    discoverable_templates = [
        ("The Red Door", "Discoverable", "abandoned_nightclub", "core"),
        ("St. Mary's Shelter", "Discoverable", "fortified_church", "residential"),
        ("The Bunker", "Discoverable", "military_bunker", "institutional"),
        ("Hidden Cache", "Discoverable", "secret_cache", "trade"),
        ("Smuggler's Warehouse", "Discoverable", "smuggler_warehouse", "industry"),
        ("Old Tunnel Entrance", "Discoverable", "underground_tunnel", "core"),
        ("Sunken Marina", "Discoverable", "marina", "trade"),
        ("Overgrown Greenhouse", "Discoverable", "greenhouse", "rural"),
        ("Command Bunker Ruins", "Discoverable", "military_bunker_large", "institutional"),
        ("Abandoned Checkpoint", "Discoverable", "checkpoint", "institutional"),
        ("Looted Electronics Store", "Discoverable", "looted_store", "commercial"),
        ("Overrun Farmhouse", "Discoverable", "overrun_farmhouse", "rural"),
        ("Overgrown Nature Trail", "Discoverable", "nature_trail", "wilderness"),
        ("Hidden Hunting Cabin", "Discoverable", "hunting_cabin", "wilderness"),
    ]

    locations: list[dict[str, Any]] = []
    locations_by_district: dict[str, list[str]] = {d["id"]: [] for d in districts}
    district_loads: dict[str, int] = {d["id"]: 0 for d in districts}
    index = 0

    role_fallbacks = {"commercial": "core", "institutional": "core", "rural": "residential", "wilderness": "residential"}

    def choose_district(role: str, location_name: str) -> dict[str, Any]:
        candidates = districts_by_role.get(role) or districts_by_role.get(role_fallbacks.get(role, "")) or districts
        if len(candidates) == 1:
            return candidates[0]

        def load_ratio(district: dict[str, Any]) -> float:
            area = district["area"]
            area_size = max(1, int(area["width"]) * int(area["height"]))
            return district_loads[district["id"]] / area_size

        lowest_ratio = min(load_ratio(district) for district in candidates)
        balanced = [district for district in candidates if load_ratio(district) == lowest_ratio]
        index = _stable_int(
            f"location-district:{seed}:{location_name}:{role}",
            0,
            len(balanced) - 1,
        )
        return balanced[index]

    def add(name, category, loc_type, role, *, discovered, visibility, kind):
        nonlocal index
        index += 1
        district = choose_district(role, name)
        district_id = district["id"]
        district_loads[district_id] += 1
        loc = _make_location(name, category, loc_type, district, seed, index, discovered=discovered, visibility=visibility, location_kind=kind)
        locations.append(loc)
        locations_by_district[district_id].append(loc["id"])

    for name, category, loc_type, visibility, role in known_locations:
        add(name, category, loc_type, role, discovered=True, visibility=visibility, kind="known")

    for name, category, loc_type, role in generic_templates:
        add(name, category, loc_type, role, discovered=True, visibility="known", kind="known")

    for name, category, loc_type, role in discoverable_templates:
        add(name, category, loc_type, role, discovered=False, visibility="unknown", kind="discoverable")

    coords = _assign_coordinates(locations_by_district, districts, seed)
    for loc in locations:
        x, y = coords[loc["id"]]
        loc["coordinates"] = {"x": x, "y": y}

    return locations


def _generate_city_layout(seed: int) -> dict[str, Any]:
    districts = _generate_districts(seed)
    road_parts = ["North", "South", "East", "West", "Cross", "Market", "Foundry", "Cedar", "Old"]
    road_suffixes = ["Road", "Avenue", "Loop", "Way", "Boulevard"]

    def center(district: dict[str, Any]) -> tuple[float, float]:
        area = district["area"]
        return area["x"] + area["width"] / 2, area["y"] + area["height"] / 2

    roads = []
    connected = [districts[0]]
    remaining = districts[1:]
    for index, district in enumerate(remaining):
        nearest = min(connected, key=lambda other: (
            (center(district)[0] - center(other)[0]) ** 2 +
            (center(district)[1] - center(other)[1]) ** 2,
            other["id"],
        ))
        roads.append({
            "name": f"{_stable_choice(f'connector-part:{seed}:{index}', road_parts)} {_stable_choice(f'connector-suffix:{seed}:{index}', road_suffixes)}",
            "from": nearest["id"],
            "to": district["id"],
        })
        connected.append(district)
    return {
        "cityName": f"{_stable_choice(str(seed), ['Harbor', 'Rook', 'Ash', 'Cinder', 'Morrow'])} City",
        "districts": districts,
        "roads": roads,
        "seed": seed,
        "gridSize": GRID_SIZE,
    }


def _generate_emergent_templates(seed: int) -> list[dict[str, Any]]:
    emergent_names = [
        "Factory Settlement",
        "Iron Market",
        "Hidden Convoy",
        "Sanctuary Outpost",
    ]
    return [
        {
            "id": f"emergent_{i + 1}",
            "name": emergent_names[i],
            "type": "emergent_template",
            "description": f"A potential emergent location shaped by survivors and factions after day {5 + i * 10}.",
            "triggerDay": 5 + i * 10,
            "seed": seed + i,
        }
        for i in range(len(emergent_names))
    ]


def _generate_poi_templates(seed: int) -> list[dict[str, Any]]:
    templates = [
        {
            "id": "poi_the_red_door",
            "name": "The Red Door",
            "description": "An abandoned underground nightclub beneath a seemingly ordinary apartment building.",
            "category": "Discoverable",
            "type": "abandoned_nightclub",
            "seed": seed + 1,
        },
        {
            "id": "poi_st_marys_shelter",
            "name": "St. Mary's Shelter",
            "description": "A church that has been converted into a fortified survivor settlement.",
            "category": "Discoverable",
            "type": "fortified_church",
            "seed": seed + 2,
        },
        {
            "id": "poi_the_bunker",
            "name": "The Bunker",
            "description": "A military emergency facility hidden beneath an industrial warehouse.",
            "category": "Discoverable",
            "type": "military_bunker",
            "seed": seed + 3,
        },
    ]
    return templates


def _refresh_layers_for_state(world_state: "WorldState" | dict[str, Any], *, seed: int | None = None, locations: list[dict[str, Any]] | None = None, rumors: list[dict[str, Any]] | None = None, geography: list[dict[str, Any]] | None = None, factions: list[dict[str, Any]] | None = None, resources: dict[str, Any] | None = None, cityLayout: dict[str, Any] | None = None) -> None:
    if isinstance(world_state, dict):
        state_seed = seed if seed is not None else world_state.get("seed", 7)
        state_locations = locations if locations is not None else world_state.get("locations", [])
        state_rumors = rumors if rumors is not None else world_state.get("rumors", [])
        state_geography = geography if geography is not None else world_state.get("geography", [])
        state_factions = factions if factions is not None else world_state.get("factions", [])
        state_resources = resources if resources is not None else world_state.get("resources", {})
        state_city_layout = cityLayout if cityLayout is not None else world_state.get("cityLayout", {})
        actual_world, player_known_world, rumored_world = _build_world_layers(
            state_seed,
            state_locations,
            state_rumors,
            geography=state_geography,
            factions=state_factions,
            resources=state_resources,
            cityLayout=state_city_layout,
        )
        world_state["actualWorld"] = actual_world
        world_state["playerKnownWorld"] = player_known_world
        world_state["rumoredWorld"] = rumored_world
        return

    state_seed = seed if seed is not None else world_state.seed
    state_locations = locations if locations is not None else world_state.locations
    state_rumors = rumors if rumors is not None else world_state.rumors
    state_geography = geography if geography is not None else world_state.geography
    state_factions = factions if factions is not None else world_state.factions
    state_resources = resources if resources is not None else world_state.resources
    state_city_layout = cityLayout if cityLayout is not None else world_state.cityLayout
    actual_world, player_known_world, rumored_world = _build_world_layers(
        state_seed,
        state_locations,
        state_rumors,
        geography=state_geography,
        factions=state_factions,
        resources=state_resources,
        cityLayout=state_city_layout,
    )
    world_state.actualWorld = actual_world
    world_state.playerKnownWorld = player_known_world
    world_state.rumoredWorld = rumored_world


def _ensure_road_network(world_state: "WorldState" | dict[str, Any]) -> dict[str, Any]:
    if isinstance(world_state, dict):
        locations = world_state.setdefault("locations", [])
        city_layout = world_state.setdefault("cityLayout", {})
        seed = world_state.get("seed", 7)
    else:
        locations = world_state.locations
        city_layout = world_state.cityLayout
        seed = world_state.seed
    if not isinstance(city_layout, dict):
        city_layout = {}
        if isinstance(world_state, dict):
            world_state["cityLayout"] = city_layout
        else:
            world_state.cityLayout = city_layout
    grid = city_layout.get("gridSize", GRID_SIZE)
    road_network = city_layout.get("roadNetwork")
    if not isinstance(road_network, dict) or road_network.get("version") != 2:
        road_network = generate_road_network(locations, seed, grid)
        city_layout["roadNetwork"] = road_network
        _refresh_layers_for_state(world_state)
    return road_network


def _ensure_free_location_cell(
    location: dict[str, Any],
    locations: list[dict[str, Any]],
    district: dict[str, Any],
    seed: int,
    city_layout: dict[str, Any],
) -> None:
    grid = city_layout.get("gridSize", GRID_SIZE)
    occupied = {
        (item.get("coordinates", {}).get("x"), item.get("coordinates", {}).get("y"))
        for item in locations
        if item.get("id") != location.get("id")
    }
    coordinate = location["coordinates"]
    cell = (coordinate["x"], coordinate["y"])
    if 0 <= cell[0] < grid and 0 <= cell[1] < grid and cell not in occupied:
        return

    area = district.get("area", {})
    candidates = [
        (x, y)
        for y in range(int(area.get("y", 0)), int(area.get("y", 0)) + int(area.get("height", 0)))
        for x in range(int(area.get("x", 0)), int(area.get("x", 0)) + int(area.get("width", 0)))
        if 0 <= x < grid and 0 <= y < grid and (x, y) not in occupied
    ]
    if not candidates:
        candidates = [
            (x, y)
            for y in range(grid)
            for x in range(grid)
            if (x, y) not in occupied
        ]
    if not candidates:
        raise RuntimeError("Cannot materialize a location: the city grid is full")
    start = _stable_int(f"materialize-cell:{seed}:{location['id']}", 0, len(candidates) - 1)
    coordinate["x"], coordinate["y"] = candidates[start]


def _append_materialized_location(
    world_state: "WorldState" | dict[str, Any],
    location: dict[str, Any],
    district: dict[str, Any],
) -> None:
    if isinstance(world_state, dict):
        locations = world_state.setdefault("locations", [])
        seed = world_state.get("seed", 7)
        city_layout = world_state.setdefault("cityLayout", {})
    else:
        locations = world_state.locations
        seed = world_state.seed
        city_layout = world_state.cityLayout
    if not isinstance(city_layout, dict):
        city_layout = {}
        if isinstance(world_state, dict):
            world_state["cityLayout"] = city_layout
        else:
            world_state.cityLayout = city_layout
    grid = city_layout.get("gridSize", GRID_SIZE)
    if any(item.get("id") == location.get("id") for item in locations):
        return

    _ensure_free_location_cell(location, locations, district, seed, city_layout)
    road_network = _ensure_road_network(world_state)
    attach_location(road_network, location, locations, seed, grid)
    locations.append(location)
    _refresh_layers_for_state(world_state)


def materialize_poi(world_state: "WorldState" | dict[str, Any], poi_id: str) -> dict[str, Any] | None:
    state = world_state if isinstance(world_state, WorldState) else world_state
    templates = state.get("poiTemplates", []) if isinstance(state, dict) else state.poiTemplates
    template = next((item for item in templates if item.get("id") == poi_id), None)
    if template is None:
        return None

    city_layout = state.get("cityLayout", {}) if isinstance(state, dict) else state.cityLayout
    districts = city_layout.get("districts", [])
    candidates = [district for district in districts if district.get("role") == "core"] or districts
    if not candidates:
        return None
    district = candidates[_stable_int(f"materialize-poi:{template.get('seed', 0)}:{poi_id}", 0, len(candidates) - 1)]
    x = _make_coordinate(f"{template.get('seed', 0)}:{template.get('name', poi_id)}", "x", district["area"]["x"], district["area"]["x"] + district["area"]["width"] - 1)
    y = _make_coordinate(f"{template.get('seed', 0)}:{template.get('name', poi_id)}", "y", district["area"]["y"], district["area"]["y"] + district["area"]["height"] - 1)
    location = {
        "id": poi_id,
        "name": template["name"],
        "type": template["type"],
        "category": template["category"],
        "district": district["id"],
        "coordinates": {"x": x, "y": y},
        "discovered": True,
        "locationKind": "discoverable",
        "actualState": {"status": "secure"},
        "visibleState": {"status": "known"},
        "zombiePopulation": 0,
        "resources": {},
        "controllingFaction": None,
        "securityLevel": 2,
        "dangerLevel": 3,
    }

    _append_materialized_location(world_state, location, district)
    return location


def materialize_emergent_location(world_state: "WorldState" | dict[str, Any], template_id: str) -> dict[str, Any] | None:
    state = world_state if isinstance(world_state, WorldState) else world_state
    templates = state.get("emergentTemplates", []) if isinstance(state, dict) else state.emergentTemplates
    template = next((item for item in templates if item.get("id") == template_id), None)
    if template is None:
        return None

    city_layout = state.get("cityLayout", {}) if isinstance(state, dict) else state.cityLayout
    districts = city_layout.get("districts", [])
    candidates = [district for district in districts if district.get("role") == "industry"] or districts
    if not candidates:
        return None
    district = candidates[_stable_int(f"materialize-emergent:{template.get('seed', 0)}:{template_id}", 0, len(candidates) - 1)]
    x = _make_coordinate(f"{template.get('seed', 0)}:{template.get('name', template_id)}", "x", district["area"]["x"], district["area"]["x"] + district["area"]["width"] - 1)
    y = _make_coordinate(f"{template.get('seed', 0)}:{template.get('name', template_id)}", "y", district["area"]["y"], district["area"]["y"] + district["area"]["height"] - 1)
    location = {
        "id": template_id,
        "name": template["name"],
        "type": template.get("type", "emergent_template"),
        "category": "Emergent",
        "district": district["id"],
        "coordinates": {"x": x, "y": y},
        "discovered": True,
        "locationKind": "emergent",
        "actualState": {"status": "forming"},
        "visibleState": {"status": "emerging"},
        "zombiePopulation": 0,
        "resources": {},
        "controllingFaction": None,
        "securityLevel": 1,
        "dangerLevel": 2,
    }

    _append_materialized_location(world_state, location, district)
    return location


def generate_initial_world_state(players: list[dict[str, Any]], seed: int) -> WorldState:
    geography = _generate_geography(seed)
    locations = _generate_locations(seed)
    poi_templates = _generate_poi_templates(seed)
    factions = [
        {
            "id": "faction_survivors",
            "name": "Local Survivors",
            "population": max(1, len(players)),
            "militaryStrength": 2,
            "resources": {"water": 3, "food": 4, "medical_supplies": 1},
            "territory": [loc["id"] for loc in locations if loc["type"] == "hospital" or loc["type"] == "school"],
            "ideology": "survival",
            "relationships": {},
            "goals": ["stay alive", "find shelter"],
        }
    ]
    world = WorldState(
        currentTime="06:00",
        currentDay=1,
        currentSeason="spring",
        weather="clear",
        geography=geography,
        locations=locations,
        players=players,
        factions=factions,
        seed=seed,
        settlements=[],
        resources={"water": 4, "food": 6, "medical_supplies": 2},
        activeEvents=[],
        discoveredInformation=[],
        rumors=[{"source": "local", "text": "A hospital and school are marked on an old map."}],
        worldHistory=normalize_world_history([{"day": 1, "summary": "The city has collapsed into a fractured survival zone."}]),
        relationships=[],
    )
    for player in world.players:
        _ensure_player_entities(player)
    world.cityLayout = _generate_city_layout(seed)
    world.cityLayout["roadNetwork"] = generate_road_network(world.locations, seed, GRID_SIZE)
    world.settlements = []
    world.emergentTemplates = _generate_emergent_templates(seed)
    world.poiTemplates = poi_templates
    if players:
        for index, player in enumerate(players):
            slot = index + 1
            assign_spawn_to_player(player, seed, slot, locations)
    else:
        for slot in [1, 2, 3]:
            spawn_info = _build_spawn_for_player(seed, slot, locations)
            world.players.append({
                "id": f"player_{slot}",
                "name": f"Player {slot}",
                "health": 100,
                "stamina": 100,
                "hunger": 50,
                "stress": 25,
                "location": spawn_info["spawnLocationName"],
                "skills": ["survival"],
                "inventory": [],
                "alive": True,
                "trustRelationships": {},
                "loyalty": 0.5,
                "fear": 0.2,
                "resentment": 0.0,
                "currentActivity": "observing",
                "entities": {"companions": [], "possessions": []},
                "spawnLocationId": spawn_info["spawnLocationId"],
                "spawnLocationName": spawn_info["spawnLocationName"],
                "spawnCoordinates": spawn_info["spawnCoordinates"],
                "nearbyLocationIds": spawn_info["nearbyLocationIds"],
                "knownNearbyLocations": spawn_info["knownNearbyLocations"],
                "spawnSeed": spawn_info["spawnSeed"],
            })
    world.refresh_layers()
    return world


def normalize_world_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(history or []):
        if not isinstance(entry, dict):
            continue
        if "eventId" in entry:
            event = entry
            event.setdefault("timestamp", event.get("timestamp", event.get("day", index + 1)))
            event.setdefault("causes", [])
            event.setdefault("effects", [])
            normalized.append(event)
            continue

        day = entry.get("day", index + 1)
        description = entry.get("summary") or entry.get("description") or "World event"
        normalized.append({
            "eventId": f"event_{_stable_hash(f'{day}:{description}:{index}')}",
            "timestamp": entry.get("timestamp", day),
            "type": entry.get("type", "world_event"),
            "description": str(description),
            "causes": [],
            "effects": [],
        })
    return normalized


def append_world_history_event(world_state: "WorldState" | dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    history = normalize_world_history(world_state.worldHistory if hasattr(world_state, "worldHistory") else world_state.get("worldHistory", []))
    event_id = payload.get("eventId") or f"event_{_stable_hash(payload.get('description', 'event') + str(len(history) + 1))}"
    event = {
        "eventId": event_id,
        "timestamp": payload.get("timestamp", payload.get("day", len(history) + 1)),
        "type": payload.get("type", "world_event"),
        "description": payload.get("description", payload.get("summary", "World event")),
        "causes": copy.deepcopy(payload.get("causes", [])),
        "effects": copy.deepcopy(payload.get("effects", [])),
    }

    history.append(event)

    for cause_id in event.get("causes", []):
        for prior_event in history:
            if prior_event.get("eventId") != cause_id:
                continue
            prior_effects = prior_event.setdefault("effects", [])
            if event_id not in prior_effects:
                prior_effects.append(event_id)
            if hasattr(world_state, "worldHistory"):
                world_state.worldHistory = history
            else:
                world_state["worldHistory"] = history
            break

    if hasattr(world_state, "worldHistory"):
        world_state.worldHistory = history
    else:
        world_state["worldHistory"] = history

    return next(item for item in history if item.get("eventId") == event_id)


@dataclass
class WorldState:
    currentTime: str = "00:00"
    currentDay: int = 1
    currentSeason: str = "spring"
    weather: str = "clear"
    geography: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    players: list[dict[str, Any]] = field(default_factory=list)
    factions: list[dict[str, Any]] = field(default_factory=list)
    settlements: list[dict[str, Any]] = field(default_factory=list)
    resources: dict[str, Any] = field(default_factory=dict)
    activeEvents: list[dict[str, Any]] = field(default_factory=list)
    discoveredInformation: list[dict[str, Any]] = field(default_factory=list)
    rumors: list[dict[str, Any]] = field(default_factory=list)
    worldHistory: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    seed: int = 7
    emergentTemplates: list[dict[str, Any]] = field(default_factory=list)
    poiTemplates: list[dict[str, Any]] = field(default_factory=list)
    cityLayout: dict[str, Any] = field(default_factory=dict)
    actualWorld: dict[str, Any] = field(default_factory=dict)
    playerKnownWorld: dict[str, Any] = field(default_factory=dict)
    rumoredWorld: dict[str, Any] = field(default_factory=dict)
    proposedDiscoveries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_room_state(cls, room_state: dict[str, Any] | None, seed: int | None = None) -> "WorldState":
        raw = room_state or {}
        existing = raw.get("world_state") if isinstance(raw.get("world_state"), dict) else None
        if existing:
            return cls.from_dict(existing)

        players_state = raw.get("players", {})
        players: list[dict[str, Any]] = []
        if isinstance(players_state, dict):
            for slot_key in sorted(players_state, key=lambda item: int(item)):
                player_state = players_state[slot_key]
                if isinstance(player_state, dict):
                    players.append(
                        {
                            "id": f"player_{slot_key}",
                            "name": f"Player {slot_key}",
                            "health": 100,
                            "stamina": 100,
                            "hunger": 50,
                            "stress": 25,
                            "location": player_state.get("facts", {}).get("location", "Unknown Shelter"),
                            "skills": ["survival"],
                            "inventory": [],
                            "alive": True,
                            "trustRelationships": {},
                            "loyalty": 0.5,
                            "fear": 0.2,
                            "resentment": 0.0,
                            "currentActivity": "observing",
                            "entities": {"companions": [], "possessions": []},
                            "pendingEvents": [],
                            "statusEffects": [],
                        }
                    )

        generator_seed = seed if seed is not None else 7
        return generate_initial_world_state(players, generator_seed)

    def refresh_layers(self) -> None:
        actual_world, player_known_world, rumored_world = _build_world_layers(
            self.seed,
            self.locations,
            self.rumors,
            geography=self.geography,
            factions=self.factions,
            resources=self.resources,
            cityLayout=self.cityLayout,
        )
        self.actualWorld = actual_world
        self.playerKnownWorld = player_known_world
        self.rumoredWorld = rumored_world

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorldState":
        state = cls(
            currentTime=_coerce_str(raw.get("currentTime"), "00:00"),
            currentDay=_coerce_int(raw.get("currentDay"), 1),
            currentSeason=_coerce_str(raw.get("currentSeason"), "spring"),
            weather=_coerce_str(raw.get("weather"), "clear"),
            geography=_coerce_list(raw.get("geography"), []),
            locations=_coerce_list(raw.get("locations"), []),
            players=_coerce_list(raw.get("players"), []),
            factions=_coerce_list(raw.get("factions"), []),
            settlements=_coerce_list(raw.get("settlements"), []),
            resources=_coerce_dict(raw.get("resources"), {}),
            activeEvents=_coerce_list(raw.get("activeEvents"), []),
            discoveredInformation=_coerce_list(raw.get("discoveredInformation"), []),
            rumors=_coerce_list(raw.get("rumors"), []),
            worldHistory=_coerce_list(raw.get("worldHistory"), []),
            relationships=_coerce_list(raw.get("relationships"), []),
            seed=_coerce_int(raw.get("seed"), 7),
            emergentTemplates=_coerce_list(raw.get("emergentTemplates"), []),
            poiTemplates=_coerce_list(raw.get("poiTemplates"), []),
            cityLayout=_coerce_dict(raw.get("cityLayout"), {}),
            actualWorld=_coerce_dict(raw.get("actualWorld"), {}),
            playerKnownWorld=_coerce_dict(raw.get("playerKnownWorld"), {}),
            rumoredWorld=_coerce_dict(raw.get("rumoredWorld"), {}),
            proposedDiscoveries=_coerce_list(raw.get("proposedDiscoveries"), []),
        )
        for player in state.players:
            if isinstance(player, dict):
                _ensure_player_entities(player)
        grid = state.cityLayout.get("gridSize", GRID_SIZE)
        road_network = state.cityLayout.get("roadNetwork")
        if (
            (not isinstance(road_network, dict) or road_network.get("version") != 2)
            and _has_grid_locations(state.locations, grid)
        ):
            state.cityLayout["roadNetwork"] = generate_road_network(
                state.locations,
                state.seed,
                grid,
            )
            state.refresh_layers()
        if not state.actualWorld:
            state.refresh_layers()
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "currentTime": self.currentTime,
            "currentDay": self.currentDay,
            "currentSeason": self.currentSeason,
            "weather": self.weather,
            "geography": copy.deepcopy(self.geography),
            "locations": copy.deepcopy(self.locations),
            "players": copy.deepcopy(self.players),
            "factions": copy.deepcopy(self.factions),
            "settlements": copy.deepcopy(self.settlements),
            "resources": copy.deepcopy(self.resources),
            "activeEvents": copy.deepcopy(self.activeEvents),
            "discoveredInformation": copy.deepcopy(self.discoveredInformation),
            "rumors": copy.deepcopy(self.rumors),
            "worldHistory": copy.deepcopy(self.worldHistory),
            "relationships": copy.deepcopy(self.relationships),
            "seed": self.seed,
            "emergentTemplates": copy.deepcopy(self.emergentTemplates),
            "poiTemplates": copy.deepcopy(self.poiTemplates),
            "cityLayout": copy.deepcopy(self.cityLayout),
            "actualWorld": copy.deepcopy(self.actualWorld),
            "playerKnownWorld": copy.deepcopy(self.playerKnownWorld),
            "rumoredWorld": copy.deepcopy(self.rumoredWorld),
            "proposedDiscoveries": copy.deepcopy(self.proposedDiscoveries),
        }

    def apply_to_room_state(self, room_state: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(room_state)
        normalized["world_state"] = self.to_dict()
        return normalized


def normalize_room_state(room_state: dict[str, Any] | None) -> dict[str, Any]:
    raw = copy.deepcopy(room_state or {})
    if not raw:
        raw = {}
    world_state = WorldState.from_room_state(raw)
    city_layout = world_state.cityLayout
    grid = city_layout.get("gridSize", GRID_SIZE)
    road_network = city_layout.get("roadNetwork")
    if (
        (not isinstance(road_network, dict) or road_network.get("version") != 2)
        and _has_grid_locations(world_state.locations, grid)
    ):
        city_layout["roadNetwork"] = generate_road_network(
            world_state.locations,
            world_state.seed,
            grid,
        )
        world_state.refresh_layers()
    raw["world_state"] = world_state.to_dict()
    if not isinstance(raw.get("pendingDecisions"), dict):
        raw["pendingDecisions"] = {}
    try:
        raw["roundNumber"] = max(1, int(raw.get("roundNumber", 1)))
    except (TypeError, ValueError):
        raw["roundNumber"] = 1
    return raw
