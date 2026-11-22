from __future__ import annotations

from typing import Any


def resolve_location_id(state: dict[str, Any], value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    locations = state.get("locations", [])
    entries: list[tuple[str | None, Any]] = []
    if isinstance(locations, list):
        entries = [(None, location) for location in locations]
    elif isinstance(locations, dict):
        entries = [
            (key if isinstance(key, str) else None, location)
            for key, location in locations.items()
        ]

    for key, location in entries:
        if not isinstance(location, dict):
            continue
        location_id = location.get("id")
        if location_id == value:
            return value
        if location_id is None and key == value:
            return value

    value_folded = value.casefold()
    for key, location in entries:
        if not isinstance(location, dict):
            continue
        location_id = location.get("id")
        if not isinstance(location_id, str):
            location_id = key
        name = location.get("name")
        if isinstance(location_id, str) and isinstance(name, str) and name.casefold() == value_folded:
            return location_id
    return None
