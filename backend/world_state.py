from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any


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

    index = (slot - 1) % len(candidate_locations)
    base_location = candidate_locations[index]
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
        "spawnSeed": _stable_int(f"spawn:{seed}:{slot}", 0, 1000),
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
    return [
        {"id": "downtown", "name": "Downtown", "area": {"x": 3, "y": 2, "width": 4, "height": 4}},
        {"id": "suburbs", "name": "Suburbs", "area": {"x": 0, "y": 4, "width": 4, "height": 5}},
        {"id": "industrial", "name": "Industrial District", "area": {"x": 6, "y": 1, "width": 4, "height": 4}},
        {"id": "riverside", "name": "Riverside", "area": {"x": 2, "y": 0, "width": 6, "height": 2}},
    ]


def _make_location(name: str, category: str, loc_type: str, district: dict[str, Any], seed: int, index: int, *, discovered: bool, visibility: str, location_kind: str = "known") -> dict[str, Any]:
    x = _make_coordinate(f"{seed}:{name}:{index}", "x", district["area"]["x"], district["area"]["x"] + district["area"]["width"] - 1)
    y = _make_coordinate(f"{seed}:{name}:{index}", "y", district["area"]["y"], district["area"]["y"] + district["area"]["height"] - 1)
    history_options = [
        "A former community center now used as a shelter.",
        "A place once prized by locals before the outbreak.",
        "The building endured years of neglect before the collapse.",
        "Evidence suggests this site was repurposed during the first weeks.",
    ]
    faction_options = ["survivors", "raiders", "militia"]
    return {
        "id": _make_id(name, index),
        "name": name,
        "type": loc_type,
        "category": category,
        "district": district["id"],
        "coordinates": {"x": x, "y": y},
        "discovered": discovered,
        "locationKind": location_kind,
        "actualState": {"status": "uncertain" if not discovered else "secure"},
        "visibleState": {"status": visibility},
        "zombiePopulation": _stable_int(f"{seed}:{name}:{index}:zombies", 0, 4 if discovered else 6),
        "resources": {"food": _stable_int(f"{seed}:{name}:{index}:food", 0, 4), "water": _stable_int(f"{seed}:{name}:{index}:water", 0, 3)},
        "controllingFaction": _stable_choice(f"{seed}:{name}:{index}:faction", faction_options),
        "factionControl": _stable_choice(f"{seed}:{name}:{index}:faction_control", faction_options),
        "securityLevel": _stable_int(f"{seed}:{name}:{index}:security", 1, 3),
        "dangerLevel": _stable_int(f"{seed}:{name}:{index}:danger", 1, 4),
        "history": _stable_choice(f"{seed}:{name}:{index}:history", history_options),
        "secret": f"{name} has a hidden cache or story tied to the outbreak.",
    }


def _generate_locations(seed: int) -> list[dict[str, Any]]:
    districts = _generate_districts(seed)
    known_locations = [
        ("Central Hospital", "Infrastructure", "hospital", "known"),
        ("Northside Police Station", "Infrastructure", "police_station", "known"),
        ("Westwood School", "Infrastructure", "school", "known"),
        ("East Market Supermarket", "Commercial", "supermarket", "known"),
    ]
    generic_templates = [
        ("Riverside Apartments", "Residential", "apartment"),
        ("Maple Houses", "Residential", "house"),
        ("Hollow Garage", "Residential", "garage"),
        ("Corner Pharmacy", "Commercial", "pharmacy"),
        ("Rusty Hardware", "Commercial", "hardware_store"),
        ("Salty Diner", "Commercial", "restaurant"),
        ("Iron Warehouse", "Commercial", "warehouse"),
        ("Beacon Fire Station", "Infrastructure", "fire_station"),
        ("East Power Station", "Infrastructure", "power_station"),
    ]
    discoverable_templates = [
        ("The Red Door", "Discoverable", "abandoned_nightclub"),
        ("St. Mary's Shelter", "Discoverable", "fortified_church"),
        ("The Bunker", "Discoverable", "military_bunker"),
        ("Hidden Cache", "Discoverable", "secret_cache"),
        ("Smuggler's Warehouse", "Discoverable", "smuggler_warehouse"),
        ("Old Tunnel Entrance", "Discoverable", "underground_tunnel"),
    ]

    locations: list[dict[str, Any]] = []
    indices = 0
    for index, (name, category, loc_type, visibility) in enumerate(known_locations, start=1):
        district = districts[index % len(districts)]
        locations.append(_make_location(name, category, loc_type, district, seed, index, discovered=True, visibility=visibility, location_kind="known"))
        indices = index

    for index, (name, category, loc_type) in enumerate(generic_templates, start=indices + 1):
        district = districts[(index + 1) % len(districts)]
        locations.append(_make_location(name, category, loc_type, district, seed, index, discovered=True, visibility="known", location_kind="known"))
        indices = index

    for index, (name, category, loc_type) in enumerate(discoverable_templates, start=indices + 1):
        district = districts[(index + 2) % len(districts)]
        locations.append(_make_location(name, category, loc_type, district, seed, index, discovered=False, visibility="unknown", location_kind="discoverable"))

    return locations


def _generate_city_layout(seed: int) -> dict[str, Any]:
    districts = [
        {"id": "downtown", "name": "Downtown", "role": "core"},
        {"id": "suburbs", "name": "Suburbs", "role": "residential"},
        {"id": "industrial", "name": "Industrial District", "role": "industry"},
        {"id": "riverside", "name": "Riverside", "role": "trade"},
    ]
    roads = [
        {"name": "North Loop", "from": "downtown", "to": "suburbs"},
        {"name": "Forge Avenue", "from": "downtown", "to": "industrial"},
        {"name": "Canal Road", "from": "riverside", "to": "downtown"},
    ]
    return {
        "cityName": f"{_stable_choice(str(seed), ['Harbor', 'Rook', 'Ash', 'Cinder', 'Morrow'])} City",
        "districts": districts,
        "roads": roads,
        "seed": seed,
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


def materialize_poi(world_state: "WorldState" | dict[str, Any], poi_id: str) -> dict[str, Any] | None:
    state = world_state if isinstance(world_state, WorldState) else world_state
    templates = state.get("poiTemplates", []) if isinstance(state, dict) else state.poiTemplates
    template = next((item for item in templates if item.get("id") == poi_id), None)
    if template is None:
        return None

    district = {"id": "downtown", "name": "Downtown", "area": {"x": 2, "y": 2, "width": 5, "height": 5}}
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

    if isinstance(world_state, dict):
        world_state.setdefault("locations", [])
        if not any(item.get("id") == location["id"] for item in world_state["locations"]):
            world_state["locations"].append(location)
        _refresh_layers_for_state(world_state)
        return location

    if not any(item.get("id") == location["id"] for item in world_state.locations):
        world_state.locations.append(location)
    _refresh_layers_for_state(world_state)
    return location


def materialize_emergent_location(world_state: "WorldState" | dict[str, Any], template_id: str) -> dict[str, Any] | None:
    state = world_state if isinstance(world_state, WorldState) else world_state
    templates = state.get("emergentTemplates", []) if isinstance(state, dict) else state.emergentTemplates
    template = next((item for item in templates if item.get("id") == template_id), None)
    if template is None:
        return None

    district = {"id": "industrial", "name": "Industrial District", "area": {"x": 6, "y": 1, "width": 4, "height": 4}}
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

    if isinstance(world_state, dict):
        world_state.setdefault("locations", [])
        if not any(item.get("id") == location["id"] for item in world_state["locations"]):
            world_state["locations"].append(location)
        _refresh_layers_for_state(world_state)
        return location

    if not any(item.get("id") == location["id"] for item in world_state.locations):
        world_state.locations.append(location)
    _refresh_layers_for_state(world_state)
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
    world.cityLayout = _generate_city_layout(seed)
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
    raw["world_state"] = world_state.to_dict()
    return raw
