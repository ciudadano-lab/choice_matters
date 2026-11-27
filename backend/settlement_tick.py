from __future__ import annotations

import copy
import math
from typing import Any

from economy import get_yield_for_location
from world_content import WorldContent, load_world_content
from world_state import WorldState, normalize_settlement_record, settlement_tier


_WORSENING_STATUS = {
    "growing": "strained",
    "stable": "strained",
    "strained": "starving",
    "starving": "collapsing",
    "collapsing": "abandoned",
    "abandoned": "abandoned",
}
_RECOVERY_STATUS = {
    "abandoned": "abandoned",
    "collapsing": "starving",
    "starving": "strained",
    "strained": "stable",
    "stable": "stable",
    "growing": "growing",
}


class SettlementTick:
    def __init__(
        self,
        seed: int,
        content: WorldContent | None = None,
    ) -> None:
        self.seed = seed
        self.content = content or load_world_content()
        self.rules = self.content.settlements.defaults

    def advance_days(
        self,
        world_state: WorldState | dict[str, Any],
        days: int,
        *,
        first_day: int | None = None,
    ) -> WorldState:
        if days < 0:
            raise ValueError("days must be non-negative")
        if hasattr(world_state, "to_dict"):
            world = world_state
        else:
            world = WorldState.from_dict(copy.deepcopy(world_state))
        if days == 0:
            return world

        day_start = first_day if first_day is not None else world.currentDay + 1
        for day in range(day_start, day_start + days):
            event_count = 0
            for settlement in sorted(
                (item for item in world.settlements if isinstance(item, dict)),
                key=lambda item: str(item.get("id", "")),
            ):
                before_needs = set(settlement.get("needs", []))
                before_status = settlement.get("status")
                reasons = self._tick_settlement(world, settlement, day)
                needs_changed = before_needs != set(settlement.get("needs", []))
                status_changed = before_status != settlement.get("status")
                weekly = day % self.rules.weekly.interval_days == 0
                if (status_changed or needs_changed or weekly) and event_count < self.rules.max_events_per_day:
                    world.internalEvents.append({
                        "type": "settlement_tick",
                        "day": day,
                        "settlementId": settlement.get("id"),
                        "reasons": reasons,
                    })
                    event_count += 1

            if day % self.rules.weekly.interval_days == 0:
                for faction_id, changes in self._recompute_faction_populations(world).items():
                    if not changes or event_count >= self.rules.max_events_per_day:
                        continue
                    world.internalEvents.append({
                        "type": "faction_population_recomputed",
                        "day": day,
                        "factionId": faction_id,
                        "reasons": changes,
                    })
                    event_count += 1
        retention = self.content.faction_rules.retention
        world.internalEvents = world.internalEvents[-retention.internal_events:]
        return world

    def apply_foundation_grant(
        self,
        world_state: dict[str, Any],
        *,
        settlement_id: str,
        location_id: str,
        action_id: str,
    ) -> dict[str, Any]:
        state = copy.deepcopy(world_state)
        settlements = state.get("settlements", [])
        if not isinstance(settlements, list):
            raise ValueError("World settlements must be a list")
        settlement = next(
            (
                item for item in settlements
                if isinstance(item, dict)
                and item.get("location_id", item.get("location")) == location_id
            ),
            None,
        )
        if settlement is None or settlement.get("foundation_grant_applied") is True:
            return state

        normalized = normalize_settlement_record(
            settlement,
            self.content,
            current_day=self._int_value(state.get("currentDay"), 1),
        )
        stores = normalized["stores"]
        world_resources = state.get("resources")
        if not isinstance(world_resources, dict):
            world_resources = {}
            state["resources"] = world_resources

        transferred: dict[str, int] = {}
        for resource, requested in sorted(self.rules.foundation_grant.items()):
            current = self._int_value(stores.get(resource), 0)
            available = max(0, self._int_value(world_resources.get(resource), 0))
            cap = self.rules.daily.store_caps.get(resource, current + requested)
            amount = min(requested, available, max(0, cap - current))
            if amount <= 0:
                continue
            stores[resource] = current + amount
            world_resources[resource] = available - amount
            transferred[resource] = amount

        normalized["foundation_grant_applied"] = True
        normalized["needs"] = self._needs(stores)
        for index, item in enumerate(settlements):
            if item is settlement:
                settlements[index] = normalized
                break

        internal_events = state.setdefault("internalEvents", [])
        if not isinstance(internal_events, list):
            raise ValueError("World internalEvents must be a list")
        internal_events.append({
            "type": "settlement_foundation_grant",
            "day": self._int_value(state.get("currentDay"), 1),
            "settlementId": settlement_id,
            "actionId": action_id,
            "transferredResources": transferred,
            "reasons": ["Transferred available shared stores to the newly founded settlement."],
        })
        return state

    def _tick_settlement(
        self,
        world: WorldState,
        settlement: dict[str, Any],
        day: int,
    ) -> dict[str, Any]:
        before_stores = copy.deepcopy(settlement["stores"])
        self._decay_player_attention(settlement)
        population_before = max(0, self._int_value(settlement.get("population"), 0))
        location = self._find_location(
            world.locations,
            settlement.get("location_id", settlement.get("location")),
        )
        location_type = location.get("type") if location is not None else None
        location_yield = get_yield_for_location(
            self.rules.daily.production_action,
            location_type,
        )
        population_units = math.ceil(
            population_before / self.rules.daily.population_unit
        )

        consumption: dict[str, int] = {}
        production: dict[str, int] = {}
        resource_names = sorted(
            set(self.rules.daily.consumption_per_unit)
            | set(location_yield)
            | set(settlement["stores"])
        )
        for resource in resource_names:
            used = (
                population_units
                * self.rules.daily.consumption_per_unit.get(resource, 0)
            )
            produced = (
                max(0, location_yield.get(resource, 0))
                * population_units
                * self.rules.daily.production_multiplier
                // self.rules.daily.production_divisor
            )
            consumption[resource] = used
            production[resource] = produced
            change = max(
                -self.rules.daily.max_store_change,
                min(self.rules.daily.max_store_change, produced - used),
            )
            current = self._int_value(settlement["stores"].get(resource), 0)
            cap = self.rules.daily.store_caps.get(resource, current)
            settlement["stores"][resource] = max(0, min(cap, current + change))

        settlement["needs"] = self._needs(settlement["stores"])
        pressure = any(
            settlement["stores"].get(resource, 0)
            <= self.rules.needs_thresholds.get(resource, 0)
            for resource in self.rules.hysteresis.pressure_resources
        )
        supplied = all(
            settlement["stores"].get(resource, 0)
            >= self.rules.needs_thresholds.get(resource, 0)
            + self.rules.hysteresis.recovery_margin
            for resource in self.rules.hysteresis.pressure_resources
        )
        morale_before = settlement["morale"]
        unrest_before = settlement["unrest"]
        morale_delta = (
            self.rules.daily.morale_supplied_delta
            if supplied and morale_before < self.rules.daily.morale_target
            else self.rules.daily.morale_depleted_delta
            if pressure
            else 0
        )
        settlement["morale"] = self._clamp_stat(
            morale_before,
            morale_delta,
            self.rules.daily.max_stat_change,
        )
        unrest_delta = (
            self.rules.daily.unrest_supplied_delta
            if supplied
            else self.rules.daily.unrest_depleted_delta
            if pressure
            else 0
        )
        settlement["unrest"] = self._clamp_stat(
            unrest_before,
            unrest_delta,
            self.rules.daily.max_stat_change,
        )

        prior_status = settlement["status"]
        status_reasons = self._advance_status(
            settlement,
            day=day,
            pressure=pressure,
            supplied=supplied,
        )

        weekly_changes: dict[str, Any] = {}
        if day % self.rules.weekly.interval_days == 0:
            weekly_changes = self._apply_weekly_changes(
                world,
                settlement,
                day=day,
                supplied=supplied,
            )

        settlement["tier"] = settlement_tier(settlement["population"])
        settlement["location"] = settlement.get("location_id")
        settlement["foundedDay"] = settlement.get("founded_day", 1)

        return {
            "locationType": location_type or "default",
            "populationBefore": population_before,
            "populationAfter": settlement["population"],
            "consumption": consumption,
            "production": production,
            "storesBefore": before_stores,
            "storesAfter": copy.deepcopy(settlement["stores"]),
            "needs": list(settlement["needs"]),
            "morale": {
                "before": morale_before,
                "after": settlement["morale"],
            },
            "unrest": {
                "before": unrest_before,
                "after": settlement["unrest"],
            },
            "status": {
                "before": prior_status,
                "after": settlement["status"],
                "reason": status_reasons,
            },
            "weekly": weekly_changes,
        }

    def _decay_player_attention(self, settlement: dict[str, Any]) -> None:
        attention = settlement.get("attention_by_player")
        if not isinstance(attention, dict):
            return
        faction_rules = self.content.faction_rules
        for player_id in sorted(tuple(attention)):
            try:
                current = max(0, int(attention[player_id]))
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid attention value for player {player_id!r}: "
                    f"{attention[player_id]!r}"
                )
            updated = max(
                0,
                min(faction_rules.max_player_attention, current)
                - faction_rules.attention_decay_per_day,
            )
            if updated:
                attention[player_id] = updated
            else:
                attention.pop(player_id)

    def _advance_status(
        self,
        settlement: dict[str, Any],
        *,
        day: int,
        pressure: bool,
        supplied: bool,
    ) -> str:
        current = settlement.get("status", self.rules.status)
        grace_until = self._int_value(
            settlement.get("legacy_grace_until_day"),
            0,
        )
        pressure_ticks = self._int_value(
            settlement.get("status_pressure_ticks"),
            0,
        )
        recovery_ticks = self._int_value(
            settlement.get("status_recovery_ticks"),
            0,
        )

        if day <= grace_until:
            settlement["status_pressure_ticks"] = 0
            settlement["status_recovery_ticks"] = 0
            return "legacy_grace_period"

        if pressure:
            pressure_ticks += 1
            settlement["status_pressure_ticks"] = pressure_ticks
            settlement["status_recovery_ticks"] = 0
            if pressure_ticks >= self.rules.hysteresis.worsening_days:
                settlement["status"] = _WORSENING_STATUS.get(
                    current,
                    self.rules.status,
                )
                settlement["status_pressure_ticks"] = 0
                return "resources_below_threshold_for_consecutive_days"
            return "resources_below_threshold"

        settlement["status_pressure_ticks"] = 0
        if supplied:
            recovery_ticks += 1
            settlement["status_recovery_ticks"] = recovery_ticks
            if recovery_ticks >= self.rules.hysteresis.recovery_days:
                settlement["status"] = _RECOVERY_STATUS.get(
                    current,
                    self.rules.status,
                )
                settlement["status_recovery_ticks"] = 0
                return "stores_recovered_with_hysteresis_margin"
            return "stores_recovering"

        settlement["status_recovery_ticks"] = 0
        return "stores_between_pressure_and_recovery_thresholds"

    def _apply_weekly_changes(
        self,
        world: WorldState,
        settlement: dict[str, Any],
        *,
        day: int,
        supplied: bool,
    ) -> dict[str, Any]:
        before_population = self._int_value(settlement.get("population"), 0)
        before_defense = self._int_value(settlement.get("defense"), 0)
        population_delta = 0
        if supplied and settlement.get("status") in {"stable", "growing"}:
            population_delta = min(
                self.rules.weekly.population_growth,
                self.rules.weekly.max_population_change,
            )
        elif settlement.get("status") in {
            "starving",
            "collapsing",
            "abandoned",
        }:
            population_delta = -min(
                self.rules.weekly.population_decline,
                self.rules.weekly.max_population_change,
            )
        settlement["population"] = max(
            0,
            min(
                self.rules.weekly.population_cap,
                before_population + population_delta,
            ),
        )

        defense_delta = (
            self.rules.weekly.defense_supplied_delta
            if supplied
            else self.rules.weekly.defense_depleted_delta
        )
        settlement["defense"] = self._clamp_stat(
            before_defense,
            defense_delta,
            self.rules.weekly.max_stat_change,
        )

        leader_change = self._check_leader(world, settlement)
        if settlement["population"] == 0:
            settlement["status"] = "abandoned"
        changes: dict[str, Any] = {
            "population": {
                "before": before_population,
                "after": settlement["population"],
            },
            "defense": {
                "before": before_defense,
                "after": settlement["defense"],
            },
            "leader": leader_change,
        }
        settlement["populationLastChangedDay"] = day
        return changes

    @staticmethod
    def _check_leader(
        world: WorldState,
        settlement: dict[str, Any],
    ) -> dict[str, Any] | None:
        npcs = world.npcs
        current_id = settlement.get("leader_npc_id")
        current = npcs.get(str(current_id)) if isinstance(npcs, dict) else None
        if isinstance(current, dict) and current.get("status", "alive") == "alive":
            return None

        faction_id = settlement.get("faction_id")
        candidates = sorted(
            (
                (str(npc_id), npc)
                for npc_id, npc in npcs.items()
                if isinstance(npc, dict)
                and npc.get("faction_id") == faction_id
                and npc.get("status", "alive") == "alive"
            ),
            key=lambda item: item[0],
        )
        if not candidates:
            return {
                "previous": current_id,
                "replacement": None,
                "reason": "no_living_faction_npc_available",
            }
        replacement_id = candidates[0][0]
        settlement["leader_npc_id"] = replacement_id
        return {
            "previous": current_id,
            "replacement": replacement_id,
            "reason": "current_leader_unavailable",
        }

    def _recompute_faction_populations(
        self,
        world: WorldState,
    ) -> dict[str, dict[str, Any]]:
        settlement_populations: dict[str, int] = {}
        for settlement in world.settlements:
            if not isinstance(settlement, dict):
                continue
            faction_id = settlement.get("faction_id")
            if isinstance(faction_id, str):
                settlement_populations[faction_id] = (
                    settlement_populations.get(faction_id, 0)
                    + max(0, self._int_value(settlement.get("population"), 0))
                )

        changes: dict[str, dict[str, Any]] = {}
        factions = sorted(
            (item for item in world.factions if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        )
        for faction in factions:
            faction_id = str(faction.get("id", ""))
            settlement_population = settlement_populations.get(faction_id, 0)
            current_population = max(
                0,
                self._int_value(faction.get("population"), 0),
            )
            roaming = faction.get("roaming_population")
            if not isinstance(roaming, int):
                roaming = max(0, current_population - settlement_population)
            else:
                last_derived = faction.get("population_last_derived")
                if isinstance(last_derived, int):
                    roaming += current_population - last_derived
            roaming = max(
                0,
                min(
                    self.rules.weekly.roaming_population_cap,
                    roaming,
                ),
            )
            total = settlement_population + roaming
            previous = faction.get("population")
            faction["roaming_population"] = roaming
            faction["population"] = total
            faction["population_last_derived"] = total
            if previous != total:
                changes[faction_id] = {
                    "settlementPopulation": settlement_population,
                    "roamingPopulation": roaming,
                    "populationBefore": previous,
                    "populationAfter": total,
                    "reason": "settlement_population_plus_roaming_population",
                }
        return changes

    def _needs(self, stores: dict[str, Any]) -> list[str]:
        return sorted(
            resource
            for resource, threshold in self.rules.needs_thresholds.items()
            if self._int_value(stores.get(resource), 0) <= threshold
        )

    @staticmethod
    def _find_location(
        locations: Any,
        location_id: Any,
    ) -> dict[str, Any] | None:
        if isinstance(locations, dict):
            location = locations.get(location_id)
            if isinstance(location, dict):
                return location
            return next(
                (
                    item
                    for item in locations.values()
                    if isinstance(item, dict) and item.get("id") == location_id
                ),
                None,
            )
        if isinstance(locations, list):
            return next(
                (
                    item for item in locations
                    if isinstance(item, dict) and item.get("id") == location_id
                ),
                None,
            )
        return None

    @staticmethod
    def _int_value(value: Any, default: int) -> int:
        return value if isinstance(value, int) else default

    @staticmethod
    def _clamp_stat(current: int, delta: int, max_change: int) -> int:
        bounded_delta = max(-max_change, min(max_change, delta))
        return max(0, min(100, current + bounded_delta))
