from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import WorldState
from location_lookup import resolve_location_id


class InformationLayer:
    def __init__(self) -> None:
        self._sources = {"scouting", "rumor", "radio_report", "survivor_report", "direct_observation"}
        self._discovery_ranks = {"heard_of": 1, "met": 2, "known": 3}
        self._faction_fields = {
            "heard_of": ("id", "name"),
            "met": ("id", "name", "ideology_tags", "leader_npc_id"),
            "known": (
                "id", "name", "ideology_tags", "leader_npc_id",
                "territory", "strength", "militaryStrength", "stances",
            ),
        }
        self._settlement_fields = {
            "heard_of": ("id", "name", "location_id", "location"),
            "met": (
                "id", "name", "location_id", "location", "faction_id",
                "status", "population",
            ),
            "known": (
                "id", "name", "location_id", "location", "faction_id",
                "status", "population", "morale", "unrest", "defense",
                "stores", "needs", "tier", "founded_day",
            ),
        }

    def apply_information(self, world_state: WorldState | dict[str, Any], report: dict[str, Any], *, target_location: str) -> None:
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        location_id = resolve_location_id(state, target_location)
        raw_locations = state.get("locations", [])
        if isinstance(raw_locations, dict):
            location = raw_locations.get(location_id)
            if not isinstance(location, dict):
                location = next(
                    (
                        item for item in raw_locations.values()
                        if isinstance(item, dict) and item.get("id") == location_id
                    ),
                    None,
                )
        else:
            locations = self._get_locations(world_state)
            location = next(
                (item for item in locations if item.get("id") == location_id),
                None,
            )
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
                "playerId": report.get("playerId", report.get("player_id")),
            }
        )

        self._update_visible_state(location, known_information)

    def get_visible_state(self, world_state: WorldState | dict[str, Any], *, player_id: str) -> dict[str, Any]:
        visible: dict[str, Any] = {}
        for location in self._get_locations(world_state):
            known_information = self._information_for_player(
                location.get("knownInformation", []),
                player_id,
            )
            visible[location.get("id")] = {
                "visibleState": self._compose_visible_state(location, known_information),
                "knownInformation": copy.deepcopy(known_information),
            }
        return visible

    def discover_faction(
        self,
        world_state: WorldState | dict[str, Any],
        *,
        player_id: str,
        faction_id: str,
        level: str,
    ) -> None:
        self._record_discovery(
            world_state,
            player_id=player_id,
            entity_type="factions",
            entity_id=faction_id,
            level=level,
        )

    def discover_settlement(
        self,
        world_state: WorldState | dict[str, Any],
        *,
        player_id: str,
        settlement_id: str,
        level: str,
    ) -> None:
        self._record_discovery(
            world_state,
            player_id=player_id,
            entity_type="settlements",
            entity_id=settlement_id,
            level=level,
        )

    def get_player_known_world(
        self,
        world_state: WorldState | dict[str, Any],
        *,
        player_id: str,
    ) -> dict[str, Any]:
        state = world_state.to_dict() if hasattr(world_state, "to_dict") else world_state
        known_world = state.get("playerKnownWorld", {})
        if not isinstance(known_world, dict):
            known_world = {}
        player_records = known_world.get("players", {})
        player_record = (
            player_records.get(player_id, {})
            if isinstance(player_records, dict)
            else {}
        )
        result: dict[str, Any] = {
            "seed": known_world.get("seed", state.get("seed")),
            "locations": [],
            "rumors": copy.deepcopy(known_world.get("rumors", [])),
            "factions": self._project_entities(
                state.get("factions", []),
                player_record.get("factions", {}),
                self._faction_fields,
            ),
            "settlements": self._project_entities(
                state.get("settlements", []),
                player_record.get("settlements", {}),
                self._settlement_fields,
            ),
        }
        locations = known_world.get("locations", [])
        if isinstance(locations, list):
            scoped_information = self.get_visible_state(
                state,
                player_id=player_id,
            )
            result["locations"] = [
                {
                    **copy.deepcopy(location),
                    "knownInformation": scoped_information.get(
                        location.get("id"), {}
                    ).get("knownInformation", []),
                    "visibleState": scoped_information.get(
                        location.get("id"), {}
                    ).get("visibleState", {"status": "unknown"}),
                }
                for location in locations
                if isinstance(location, dict)
            ]
        return result

    def _record_discovery(
        self,
        world_state: WorldState | dict[str, Any],
        *,
        player_id: str,
        entity_type: str,
        entity_id: str,
        level: str,
    ) -> None:
        if level not in self._discovery_ranks:
            raise ValueError(f"Unknown discovery level: {level}")
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        entities = state.get(entity_type, [])
        if isinstance(entities, dict):
            entities = list(entities.values())
        exists = any(
            isinstance(item, dict) and item.get("id") == entity_id
            for item in entities
        )
        if not exists:
            raise ValueError(f"Unknown {entity_type[:-1]} id: {entity_id}")

        known_world = state.setdefault("playerKnownWorld", {})
        players = known_world.setdefault("players", {})
        player_record = players.setdefault(player_id, {})
        discoveries = player_record.setdefault(entity_type, {})
        previous = discoveries.get(entity_id)
        if (
            previous not in self._discovery_ranks
            or self._discovery_ranks[level] > self._discovery_ranks[previous]
        ):
            discoveries[entity_id] = level

    def _project_entities(
        self,
        entities: Any,
        discoveries: Any,
        fields_by_level: dict[str, tuple[str, ...]],
    ) -> list[dict[str, Any]]:
        if isinstance(entities, dict):
            entities = list(entities.values())
        if not isinstance(discoveries, dict) or not isinstance(entities, list):
            return []
        by_id = {
            str(item.get("id")): item
            for item in entities
            if isinstance(item, dict) and item.get("id") is not None
        }
        projected = []
        for entity_id in sorted(discoveries):
            level = discoveries[entity_id]
            source = by_id.get(str(entity_id))
            if source is None or level not in fields_by_level:
                continue
            visible = {
                field: copy.deepcopy(source[field])
                for field in fields_by_level[level]
                if field in source
            }
            visible["discoveryLevel"] = level
            projected.append(visible)
        return projected

    @staticmethod
    def _information_for_player(
        known_information: Any,
        player_id: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(known_information, list):
            return []
        return [
            item
            for item in known_information
            if isinstance(item, dict)
            and item.get("playerId", item.get("player_id")) in (None, player_id)
        ]

    def get_map_reveal_state(self, world_state: WorldState | dict[str, Any], *, player_id: str) -> dict[str, Any]:
        current_day = int(getattr(world_state, "currentDay", 0) if not isinstance(world_state, dict) else world_state.get("currentDay", 0))
        locations = self._get_locations(world_state)
        known_information = [
            item
            for location in locations
            for item in self._information_for_player(
                location.get("knownInformation", []),
                player_id,
            )
        ]

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
            locations = world_state.get("locations", [])
            if isinstance(locations, dict):
                return [item for item in locations.values() if isinstance(item, dict)]
            return locations if isinstance(locations, list) else []
        return world_state.locations

    def _compose_visible_state(
        self,
        location: dict[str, Any],
        known_information: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if known_information is None:
            known_information = location.get("knownInformation", [])
        if not known_information:
            return {"status": "unknown"}

        weighted = self._weighted_summary(known_information)
        visible = {"status": weighted["status"]}
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
