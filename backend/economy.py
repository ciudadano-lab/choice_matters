from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BACKEND_DIR / "content"
ECONOMY_PATH = CONTENT_DIR / "economy.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        try:
            loaded = json.load(handle)
        except json.JSONDecodeError:
            return {}
    return loaded if isinstance(loaded, dict) else {}


def get_economy_config() -> dict[str, Any]:
    return _load_json(ECONOMY_PATH)


def get_daily_resource_consumption() -> dict[str, int]:
    data = get_economy_config()
    daily = data.get("daily_food_consumption_per_player", 1)
    water = data.get("daily_water_consumption_per_player", 1)
    return {
        "food": int(daily),
        "water": int(water),
    }


def get_starting_resources(player_count: int = 1) -> dict[str, int]:
    if player_count < 0:
        raise ValueError("player_count must be non-negative")
    starting = get_economy_config().get(
        "starting_resources",
        {"food": 6, "water": 4, "medical_supplies": 2},
    )
    if not isinstance(starting, dict):
        raise ValueError(f"{ECONOMY_PATH}: starting_resources must be an object")
    return {
        str(name): max(0, int(value)) * player_count
        for name, value in starting.items()
    }


def get_resource_caps(player_count: int = 1) -> dict[str, int]:
    if player_count < 0:
        raise ValueError("player_count must be non-negative")
    caps = get_economy_config().get("resource_caps", {})
    if not isinstance(caps, dict):
        raise ValueError(f"{ECONOMY_PATH}: resource_caps must be an object")
    return {
        str(name): max(0, int(value)) * player_count
        for name, value in caps.items()
    }


def cap_resource_stores(
    resources: dict[str, Any],
    player_count: int = 1,
) -> dict[str, Any]:
    capped = dict(resources)
    for resource, cap in get_resource_caps(player_count).items():
        if resource in capped:
            capped[resource] = max(0, min(cap, int(capped[resource])))
    return capped


def get_health_recovery_config() -> dict[str, int]:
    data = get_economy_config()
    names = (
        "health_regen_per_day",
        "health_regen_min_food",
        "health_regen_min_water",
        "starvation_health_loss_per_day",
        "dehydration_health_loss_per_day",
        "empty_food_satiety_loss_per_day",
        "empty_water_satiety_loss_per_day",
        "fed_satiety_recovery_per_day",
        "rest_health_recovery",
    )
    return {name: max(0, int(data.get(name, 0))) for name in names}


def get_yield_for_location(action_name: str, location_type: str | None = None) -> dict[str, int]:
    data = get_economy_config()
    action_yields = data.get("yield_actions", {})
    action_yield = action_yields.get(action_name, {}) if isinstance(action_yields, dict) else {}
    location_yields = data.get("location_yields", {})
    location_key = str(location_type or "default").casefold()
    base = {}
    if isinstance(location_yields, dict):
        base = location_yields.get(
            location_key,
            location_yields.get("default", {}),
        )
    if not isinstance(base, dict):
        base = {}

    if isinstance(action_yield, dict) and any(
        isinstance(value, dict) for value in action_yield.values()
    ):
        selected = action_yield.get(location_key, action_yield.get("default", {}))
    else:
        selected = action_yield
    if not isinstance(selected, dict):
        raise ValueError(
            f"{ECONOMY_PATH}: yield_actions.{action_name} must be an object"
        )

    merged = dict(base)
    merged.update(selected)
    return {str(resource): int(value) for resource, value in merged.items()}


def apply_action_yield(state_before: Any, result: Any, action: Any) -> Any:
    action_name = getattr(action, "actionType", None)
    if action_name not in {"gather", "search", "scavenge", "forage"}:
        return result
    if getattr(result, "rejected", False) or getattr(result, "physicallyImpossible", False):
        return result

    if hasattr(state_before, "to_dict"):
        state_before = state_before.to_dict()
    before_world = (
        state_before.get("world_state", state_before)
        if isinstance(state_before, dict)
        else {}
    )
    locations = before_world.get("locations", [])
    location_id = getattr(action, "target", None)
    if isinstance(locations, dict):
        location = locations.get(location_id)
        if location is None:
            location = next(
                (
                    item for item in locations.values()
                    if isinstance(item, dict) and item.get("id") == location_id
                ),
                {},
            )
    elif isinstance(locations, list):
        location = next(
            (
                item for item in locations
                if isinstance(item, dict) and item.get("id") == location_id
            ),
            {},
        )
    else:
        location = {}
    location_type = location.get("type") if isinstance(location, dict) else None
    yields = get_yield_for_location(action_name, location_type)

    updated = result.updatedWorldState
    after_world = (
        updated.get("world_state", updated)
        if isinstance(updated, dict)
        else {}
    )
    resources = after_world.setdefault("resources", {})
    original_resources = before_world.get("resources", {})
    changes = result.resourceChanges
    for resource, amount in yields.items():
        old_change = changes.get(resource, 0)
        initial = original_resources.get(resource, 0)
        direct_delta = resources.get(resource, 0) - initial - old_change
        resources[resource] = initial + direct_delta + amount
    players = after_world.get("players", [])
    player_count = len(players) if isinstance(players, list) else 1
    after_world["resources"] = cap_resource_stores(resources, player_count)
    for resource in yields:
        changes[resource] = (
            after_world["resources"].get(resource, 0)
            - original_resources.get(resource, 0)
        )
    return result
