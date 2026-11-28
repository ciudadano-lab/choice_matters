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
from world_content import load_world_content


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
                "id", "name", "location_id", "location", "coordinates", "faction_id",
                "status", "population",
            ),
            "known": (
                "id", "name", "location_id", "location", "coordinates", "faction_id",
                "status", "population", "morale", "unrest", "defense",
                "stores", "needs", "tier", "founded_day",
            ),
        }
        self._discovery_rules = load_world_content().faction_rules.discovery

    def apply_information(self, world_state: WorldState | dict[str, Any], report: dict[str, Any], *, target_location: str) -> None:
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        if report.get("source") in self._discovery_rules.report_sources:
            player_id = report.get("playerId", report.get("player_id"))
            if player_id is not None:
                self.discover_report_entities(
                    world_state,
                    report,
                    player_id=str(player_id),
                )
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

    def initialize_player_discoveries(
        self,
        world_state: WorldState | dict[str, Any],
    ) -> None:
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        players = state.get("players", [])
        factions = state.get("factions", [])
        settlements = state.get("settlements", [])
        faction_ids = {
            str(item.get("id"))
            for item in factions
            if isinstance(item, dict) and item.get("id") is not None
        }
        own_faction_id = (
            "faction_survivors"
            if "faction_survivors" in faction_ids
            else None
        )
        for player in players:
            if not isinstance(player, dict) or player.get("id") is None:
                continue
            player_id = str(player["id"])
            faction_id = player.get("factionId", player.get("faction_id", own_faction_id))
            if isinstance(faction_id, str) and faction_id in faction_ids:
                self.discover_faction(
                    world_state,
                    player_id=player_id,
                    faction_id=faction_id,
                    level=self._discovery_rules.initial_faction_level,
                )

            location_id = resolve_location_id(state, player.get("spawnLocationId", player.get("location")))
            location = next(
                (
                    item for item in state.get("locations", [])
                    if isinstance(item, dict) and item.get("id") == location_id
                ),
                None,
            )
            coordinates = location.get("coordinates", {}) if isinstance(location, dict) else {}
            nearest = min(
                (
                    item for item in settlements
                    if isinstance(item, dict) and item.get("id") is not None
                ),
                key=lambda item: (
                    0
                    if item.get("location_id", item.get("location")) == location_id
                    else (
                        (int(item.get("coordinates", {}).get("x", 0)) - int(coordinates.get("x", 0))) ** 2
                        + (int(item.get("coordinates", {}).get("y", 0)) - int(coordinates.get("y", 0))) ** 2
                    ),
                    str(item.get("id")),
                ),
                default=None,
            )
            if nearest is not None:
                self.discover_settlement(
                    world_state,
                    player_id=player_id,
                    settlement_id=str(nearest["id"]),
                    level=self._discovery_rules.initial_settlement_level,
                )

    def record_action_discoveries(
        self,
        world_state: WorldState | dict[str, Any],
        *,
        action_type: str,
        actor_id: str,
        target: str,
        parameters: dict[str, Any] | None = None,
        outcome_tier: str | None = None,
    ) -> None:
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        parameters = parameters or {}
        players = state.get("players", [])
        player = next(
            (item for item in players if isinstance(item, dict) and item.get("id") == actor_id),
            None,
        )
        if player is None:
            return

        settlements = [
            item for item in state.get("settlements", [])
            if isinstance(item, dict)
        ]
        locations = state.get("locations", [])
        if isinstance(locations, dict):
            locations = list(locations.values())

        def find_settlement(value: Any) -> dict[str, Any] | None:
            if not isinstance(value, str):
                return None
            return next(
                (
                    item for item in settlements
                    if value in {
                        str(item.get("id", "")),
                        str(item.get("location_id", item.get("location", ""))),
                    }
                ),
                None,
            )

        if action_type in {"travel", "search"}:
            location_id = resolve_location_id(state, target) or target
            settlement = find_settlement(location_id)
            level = (
                self._discovery_rules.travel_settlement_level
                if action_type == "travel"
                else self._discovery_rules.search_settlement_level
            )
            if settlement is not None:
                self.discover_settlement(
                    world_state,
                    player_id=actor_id,
                    settlement_id=str(settlement["id"]),
                    level=level,
                )

        if action_type == "establish_settlement":
            founded = next(
                (
                    item for item in settlements
                    if item.get("founderId", item.get("founder_id")) == actor_id
                    and (
                        item.get("id") == parameters.get("settlementId")
                        or item.get("id") == parameters.get("settlement_id")
                        or item.get("location_id", item.get("location"))
                        == (resolve_location_id(state, target) or target)
                    )
                ),
                None,
            )
            if founded is not None:
                self.discover_settlement(
                    world_state,
                    player_id=actor_id,
                    settlement_id=str(founded["id"]),
                    level=self._discovery_rules.founded_settlement_level,
                )

        if action_type in {"rest", "trade", "negotiate", "recruit"}:
            current_location = resolve_location_id(state, player.get("location")) or player.get("location")
            resolved_target = resolve_location_id(state, target) or target
            settlement = find_settlement(current_location) or find_settlement(resolved_target)
            if settlement is not None:
                self.discover_settlement(
                    world_state,
                    player_id=actor_id,
                    settlement_id=str(settlement["id"]),
                    level=self._discovery_rules.presence_settlement_level,
                )

        if action_type not in {"negotiate", "trade", "communicate", "send_mission"}:
            return

        target_entity = parameters.get("targetEntity")
        entity_ids: list[tuple[str, str]] = []
        if isinstance(target_entity, dict):
            for key, entity_type in (
                ("factionId", "factions"),
                ("faction_id", "factions"),
                ("settlementId", "settlements"),
                ("settlement_id", "settlements"),
                ("entityId", ""),
                ("id", ""),
            ):
                value = target_entity.get(key)
                if isinstance(value, str):
                    inferred_type = entity_type or str(
                        target_entity.get("type", target_entity.get("entityType", ""))
                    ).casefold()
                    if inferred_type in {"faction", "factions"}:
                        entity_ids.append(("factions", value))
                    elif inferred_type in {"settlement", "settlements"}:
                        entity_ids.append(("settlements", value))

        factions = state.get("factions", [])
        resolved_target = resolve_location_id(state, target) or target
        for entity_type, entities in (("factions", factions), ("settlements", settlements)):
            if not isinstance(entities, list):
                continue
            for entity in entities:
                entity_id = entity.get("id")
                if not isinstance(entity_id, str):
                    continue
                entity_location = entity.get("location_id", entity.get("location"))
                if (
                    target == entity_id
                    or target == entity.get("name")
                    or target == entity_location
                    or resolved_target == entity_location
                    or (entity_type, entity_id) in entity_ids
                ):
                    entity_ids.append((entity_type, entity_id))

        if not entity_ids:
            return
        known_world = state.get("playerKnownWorld")
        if not isinstance(known_world, dict):
            known_world = {}
            state["playerKnownWorld"] = known_world
        player_records = known_world.get("players")
        if not isinstance(player_records, dict):
            player_records = {}
            known_world["players"] = player_records
        player_record = player_records.setdefault(actor_id, {})
        if not isinstance(player_record, dict):
            player_record = {}
            player_records[actor_id] = player_record
        interaction_counts = player_record.setdefault("_interaction_counts", {})
        if not isinstance(interaction_counts, dict):
            interaction_counts = {}
            player_record["_interaction_counts"] = interaction_counts
        success = outcome_tier in {"success", "critical_success"}
        for entity_type, entity_id in sorted(set(entity_ids)):
            count_key = f"{entity_type}:{entity_id}"
            count = int(interaction_counts.get(count_key, 0)) + 1
            interaction_counts[count_key] = count
            level = self._discovery_rules.interaction_first_level
            if count >= self._discovery_rules.interaction_repeat_count:
                level = self._discovery_rules.interaction_repeat_level
            if success:
                level = self._discovery_rules.interaction_success_level
            if entity_type == "factions":
                self.discover_faction(
                    world_state,
                    player_id=actor_id,
                    faction_id=entity_id,
                    level=level,
                )
            else:
                self.discover_settlement(
                    world_state,
                    player_id=actor_id,
                    settlement_id=entity_id,
                    level=level,
                )

    def discover_report_entities(
        self,
        world_state: WorldState | dict[str, Any],
        report: dict[str, Any],
        *,
        player_id: str,
    ) -> None:
        state = world_state.__dict__ if isinstance(world_state, WorldState) else world_state
        source = report.get("source")
        if source not in self._discovery_rules.report_sources:
            return
        explicit_ids: set[tuple[str, str]] = set()
        for key, entity_type in (
            ("factionIds", "factions"),
            ("faction_ids", "factions"),
            ("settlementIds", "settlements"),
            ("settlement_ids", "settlements"),
        ):
            values = report.get(key, [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                explicit_ids.update(
                    (entity_type, value)
                    for value in values if isinstance(value, str)
                )
        generic_ids = report.get("entityIds", report.get("entity_ids", []))
        if isinstance(generic_ids, list):
            for item in generic_ids:
                if not isinstance(item, dict):
                    continue
                entity_type = str(item.get("type", item.get("entityType", ""))).casefold()
                entity_id = item.get("id")
                if isinstance(entity_id, str):
                    if entity_type in {"faction", "factions"}:
                        explicit_ids.add(("factions", entity_id))
                    elif entity_type in {"settlement", "settlements"}:
                        explicit_ids.add(("settlements", entity_id))
        entities = (
            ("factions", state.get("factions", [])),
            ("settlements", state.get("settlements", [])),
        )
        mentioned = str(
            report.get("content", report.get("text", report.get("summary", "")))
        ).casefold()
        for entity_type, records in entities:
            if isinstance(records, dict):
                records = list(records.values())
            for entity in records if isinstance(records, list) else []:
                if not isinstance(entity, dict) or not isinstance(entity.get("id"), str):
                    continue
                entity_id = entity["id"]
                name = str(entity.get("name", ""))
                if (
                    (entity_type, entity_id) in explicit_ids
                    or entity_id.casefold() in mentioned
                    or (name and name.casefold() in mentioned)
                ):
                    if entity_type == "factions":
                        self.discover_faction(
                            world_state,
                            player_id=player_id,
                            faction_id=entity_id,
                            level=self._discovery_rules.report_level,
                        )
                    else:
                        self.discover_settlement(
                            world_state,
                            player_id=player_id,
                            settlement_id=entity_id,
                            level=self._discovery_rules.report_level,
                        )

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
        player = next(
            (
                item for item in state.get("players", [])
                if isinstance(item, dict) and item.get("id") == player_id
            ),
            None,
        )
        entities = player.get("entities", {}) if isinstance(player, dict) else {}
        references = entities.get("knownCharacters", []) if isinstance(entities, dict) else []
        world_npcs = state.get("npcs", {})
        known_npcs = []
        if isinstance(world_npcs, dict) and isinstance(references, list):
            seen_npc_ids: set[str] = set()
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                npc_id = reference.get("npcId")
                if not isinstance(npc_id, str) or npc_id in seen_npc_ids:
                    continue
                npc = world_npcs.get(npc_id)
                if not isinstance(npc, dict):
                    continue
                seen_npc_ids.add(npc_id)
                known_npcs.append({
                    "id": npc_id,
                    "name": npc.get("name", npc_id),
                    "role": npc.get("role", "unknown"),
                    "status": npc.get("status", "alive"),
                })
        result: dict[str, Any] = {
            "seed": known_world.get("seed", state.get("seed")),
            "locations": [],
            "rumors": copy.deepcopy(known_world.get("rumors", [])),
            "npcs": sorted(known_npcs, key=lambda npc: npc["id"]),
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

        known_world = state.get("playerKnownWorld")
        if not isinstance(known_world, dict):
            known_world = {}
            state["playerKnownWorld"] = known_world
        players = known_world.setdefault("players", {})
        if not isinstance(players, dict):
            players = {}
            known_world["players"] = players
        player_record = players.setdefault(player_id, {})
        if not isinstance(player_record, dict):
            player_record = {}
            players[player_id] = player_record
        discoveries = player_record.setdefault(entity_type, {})
        if not isinstance(discoveries, dict):
            discoveries = {}
            player_record[entity_type] = discoveries
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
