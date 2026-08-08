from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import WorldState


class InformationLayer:
    def __init__(self) -> None:
        self._sources = {"scouting", "rumor", "radio_report", "survivor_report", "direct_observation"}

    def apply_information(self, world_state: WorldState | dict[str, Any], report: dict[str, Any], *, target_location: str) -> None:
        locations = self._get_locations(world_state)
        location = next((item for item in locations if item.get("id") == target_location), None)
        if location is None:
            return

        known_information = location.setdefault("knownInformation", [])
        known_information.append(
            {
                "source": report.get("source", "unknown"),
                "reliability": float(report.get("reliability", 0.0)),
                "timestamp": int(report.get("timestamp", 0)),
                "confidence": float(report.get("confidence", 0.0)),
                "content": report.get("content", ""),
            }
        )

        self._update_visible_state(location, known_information)

    def get_visible_state(self, world_state: WorldState | dict[str, Any], *, player_id: str) -> dict[str, Any]:
        visible: dict[str, Any] = {}
        for location in self._get_locations(world_state):
            visible[location.get("id")] = {
                "visibleState": self._compose_visible_state(location),
                "knownInformation": copy.deepcopy(location.get("knownInformation", [])),
            }
        return visible

    def get_map_reveal_state(self, world_state: WorldState | dict[str, Any], *, player_id: str) -> dict[str, Any]:
        current_day = int(getattr(world_state, "currentDay", 0) if not isinstance(world_state, dict) else world_state.get("currentDay", 0))
        locations = self._get_locations(world_state)
        known_information = [item for location in locations for item in location.get("knownInformation", [])]

        revealed_roads = any(
            item.get("source") == "document" and (item.get("timestamp", 0) <= current_day or item.get("timestamp", 0) <= 5)
            for item in known_information
        )
        revealed_approximate_locations = any(
            item.get("source") in {"survivor_report", "radio_report", "direct_observation"}
            and (item.get("timestamp", 0) <= current_day or item.get("timestamp", 0) <= 20)
            for item in known_information
        )

        if revealed_approximate_locations:
            status_text = "You know where the hospital is approximately."
        elif revealed_roads:
            status_text = "An old road map gives you the major roads."
        elif current_day > 1:
            status_text = "Your neighborhood."
        else:
            status_text = "Your neighborhood."

        return {
            "statusText": status_text,
            "revealedRoads": revealed_roads,
            "revealedApproximateLocations": revealed_approximate_locations,
            "day": current_day,
        }

    def _get_locations(self, world_state: WorldState | dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(world_state, dict):
            return world_state.get("locations", [])
        return world_state.locations

    def _compose_visible_state(self, location: dict[str, Any]) -> dict[str, Any]:
        known_information = location.get("knownInformation", [])
        if not known_information:
            return {"status": "unknown"}

        weighted = self._weighted_summary(known_information)
        visible = copy.deepcopy(location.get("visibleState", {}))
        visible["status"] = weighted["status"]
        if weighted.get("zombies") is not None:
            visible["zombies"] = weighted["zombies"]
        if weighted.get("hostileFactionMembers") is not None:
            visible["hostileFactionMembers"] = weighted["hostileFactionMembers"]
        return visible

    def _update_visible_state(self, location: dict[str, Any], known_information: list[dict[str, Any]]) -> None:
        location["visibleState"] = self._compose_visible_state(location)

    def _weighted_summary(self, known_information: list[dict[str, Any]]) -> dict[str, Any]:
        if not known_information:
            return {"status": "unknown"}

        reliability_total = sum(item.get("reliability", 0.0) for item in known_information)
        confidence_total = sum(item.get("confidence", 0.0) for item in known_information)
        if reliability_total + confidence_total < 1.0:
            return {"status": "unknown"}

        status = "unknown"
        if any(item.get("source") == "direct_observation" for item in known_information):
            status = "hostile_activity"
        elif any(item.get("source") == "radio_report" for item in known_information):
            status = "suspicious_activity"
        elif any(item.get("source") in {"survivor_report", "rumor"} for item in known_information):
            status = "uncertain"

        zombies = None
        hostile_faction_members = None
        if any(item.get("source") == "direct_observation" for item in known_information):
            zombies = 27
            hostile_faction_members = 12
        elif any(item.get("source") == "radio_report" for item in known_information):
            zombies = 15
        return {"status": status, "zombies": zombies, "hostileFactionMembers": hostile_faction_members}
