from __future__ import annotations

import copy
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import WorldState, append_world_history_event, settlement_tier
from simulation_engine import SimulationEngine
from information_layer import InformationLayer


DEFAULT_STRATEGIC_PERIOD_DAYS = 21
_information_layer = InformationLayer()


@dataclass
class WorldEvent:
    day: int
    summary: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


class WorldEventQueue:
    def __init__(self) -> None:
        self._events: list[WorldEvent] = []

    def add(self, event: WorldEvent) -> None:
        self._events.append(event)
        self._events.sort(key=lambda item: (item.day, item.summary))

    def next_event(self) -> dict[str, Any] | None:
        if not self._events:
            return None
        event = self._events.pop(0)
        return {"day": event.day, "summary": event.summary, "type": event.event_type, "payload": event.payload}

    def has_pending_events(self) -> bool:
        return bool(self._events)


class WorldTick:
    def __init__(self, seed: int = 7) -> None:
        self.seed = seed
        self.last_period_outcome: dict[str, Any] = {}

    def advance(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        *,
        hours: int = 24,
        run_autopilot: bool = True,
        room_state: dict[str, Any] | None = None,
    ) -> WorldState:
        state = copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        world = WorldState.from_dict(state)

        if run_autopilot and self._has_active_policy(world):
            return self._advance_strategic_period(world, event_queue, hours, room_state=room_state)

        current_day = world.currentDay
        current_time = world.currentTime
        parts = current_time.split(":") if ":" in current_time else ["00", "00"]
        hour = int(parts[0]) + hours
        new_day = current_day + (hour // 24)
        new_hour = hour % 24
        world.currentDay = new_day
        world.currentTime = f"{new_hour:02d}:{parts[1] if len(parts) > 1 else '00'}"

        weather_options = ["clear", "rain", "storm"]
        world.weather = weather_options[(self._stable_seed(world.currentDay, self.seed) + hours) % len(weather_options)]

        for player in world.players:
            active_effects: list[dict[str, Any]] = []
            for effect in player.get("statusEffects", []):
                if not isinstance(effect, dict):
                    continue
                expires_day = effect.get("expiresDay")
                mechanical = effect.get("mechanicalEffect", {})
                if isinstance(mechanical, dict):
                    for stat, delta in mechanical.items():
                        if stat in {"health", "stamina", "hunger", "stress"}:
                            current = player.get(stat, 0)
                            player[stat] = max(0, current + int(delta))
                if expires_day is None:
                    active_effects.append(effect)
                    continue
                if int(expires_day) > world.currentDay:
                    active_effects.append(effect)
                elif int(expires_day) == world.currentDay:
                    continue
                else:
                    continue
            player["statusEffects"] = active_effects
            player["hunger"] = max(0, player.get("hunger", 0) - 1)
            player["stress"] = max(0, player.get("stress", 0) + 1)

        for location in world.locations:
            location["zombiePopulation"] = max(0, location.get("zombiePopulation", 0) + 1)
            if location.get("zombiePopulation", 0) > 4:
                location["dangerLevel"] = min(5, location.get("dangerLevel", 1) + 1)

        self._regenerate_location_resources(world)

        for faction in world.factions:
            faction["resources"] = {
                **faction.get("resources", {}),
                "food": faction.get("resources", {}).get("food", 0) - 1,
            }

        self._simulate_factions(world, hours, event_queue=event_queue)
        self._simulate_settlements(world)

        world.resources["food"] = max(0, world.resources.get("food", 0) - 1)
        world.resources["water"] = max(0, world.resources.get("water", 0) - 1)

        if world.resources.get("food", 0) < 2:
            world.activeEvents.append({"type": "food_shortage", "day": world.currentDay, "summary": "Food supplies are running low."})

        if self._stable_seed(world.currentDay, self.seed + 1) % 2 == 0:
            event_queue.add(WorldEvent(day=world.currentDay + 1, summary="Survivors report strange noise nearby.", event_type="encounter"))
        if self._stable_seed(world.currentDay, self.seed + 2) % 3 == 0:
            event_queue.add(WorldEvent(day=world.currentDay + 2, summary="A supply cache is discovered.", event_type="supply"))

        world.activeEvents.append({"type": "tick", "day": world.currentDay, "summary": "World tick processed."})
        return world

    def _regenerate_location_resources(self, world: WorldState) -> None:
        if world.currentDay % 3 != 0:
            return
        for location in world.locations:
            current = location.get("resources")
            maximum = location.get("resourcesMax")
            if not isinstance(current, dict) or not isinstance(maximum, dict):
                continue
            for resource, max_value in maximum.items():
                current_value = max(0, int(current.get(resource, 0)))
                cap = max(0, int(max_value))
                if current_value >= cap:
                    continue
                growth_seed = self._stable_seed(
                    f"resource-regen:{world.currentDay}:{location.get('id', '')}:{resource}",
                    self.seed,
                )
                current[resource] = min(cap, current_value + growth_seed % 2)

    def _simulate_settlements(self, world: WorldState) -> None:
        for settlement in world.settlements:
            if not isinstance(settlement, dict):
                continue
            settlement_id = str(settlement.get("id", "settlement"))
            population_before = max(0, int(settlement.get("population", 0)))
            tier_before = settlement_tier(population_before)
            growth_seed = self._stable_seed(
                f"{world.currentDay}:{settlement_id}",
                self.seed + 23,
            )
            growth = growth_seed % 5
            settlement["population"] = population_before + growth
            tier_after = settlement_tier(settlement["population"])
            settlement["tier"] = tier_after
            if tier_after == tier_before:
                continue

            settlement_name = settlement.get("name", settlement_id)
            summary = f"{settlement_name} has grown into tier {tier_after}."
            world.activeEvents.append({
                "type": "settlement_growth",
                "day": world.currentDay,
                "summary": summary,
                "settlementId": settlement_id,
                "fromTier": tier_before,
                "toTier": tier_after,
            })
            append_world_history_event(world, {
                "type": "settlement_growth",
                "description": summary,
                "day": world.currentDay,
                "timestamp": world.currentDay,
                "causes": [settlement_id],
            })

    def _has_active_policy(self, world: WorldState) -> bool:
        return any(
            isinstance(player, dict) and isinstance(player.get("currentPolicy"), dict)
            for player in world.players
        )

    def _advance_strategic_period(
        self,
        world: WorldState,
        event_queue: WorldEventQueue,
        hours: int,
        *,
        room_state: dict[str, Any] | None = None,
    ) -> WorldState:
        days = max(1, hours // 24)
        engine = SimulationEngine(seed=self.seed)
        daily_outcomes: list[dict[str, Any]] = []

        for day_offset in range(days):
            for player in list(world.players):
                if not isinstance(player, dict):
                    continue
                policy = player.get("currentPolicy")
                if not isinstance(policy, dict):
                    continue
                actor_id = str(player.get("id", ""))
                result = engine.auto_resolve_policy_day(world, actor_id, policy, world.currentDay)
                world = WorldState.from_dict(result.updatedWorldState)
                self._apply_tactical_side_effects(world, result, actor_id, room_state)
                daily_outcomes.append({
                    "day": world.currentDay,
                    "playerId": actor_id,
                    "actionType": result.actionType,
                    "outcomeTier": result.outcomeTier,
                })
            world = self.advance(world, event_queue, hours=24, run_autopilot=False)

        if daily_outcomes:
            period_days = max(
                [
                    int(player.get("currentPolicy", {}).get("commitmentDays", DEFAULT_STRATEGIC_PERIOD_DAYS))
                    for player in world.players
                    if isinstance(player, dict) and isinstance(player.get("currentPolicy"), dict)
                ]
                or [DEFAULT_STRATEGIC_PERIOD_DAYS]
            )
            self.last_period_outcome = self._roll_up_period(daily_outcomes, period_days)
            world.activeEvents.append({
                "type": "strategic_period_outcome",
                "day": world.currentDay,
                "summary": f"Strategic period resolved at {self.last_period_outcome['outcomeTier']}.",
                "payload": copy.deepcopy(self.last_period_outcome),
            })
        else:
            self.last_period_outcome = {}
        return world

    def _apply_tactical_side_effects(
        self,
        world: WorldState,
        result: Any,
        actor_id: str,
        room_state: dict[str, Any] | None,
    ) -> None:
        if result.actionType in {"search", "travel"} and result.informationDiscovered:
            source = "direct_observation" if result.actionType == "search" else "survivor_report"
            actor = next((player for player in world.players if player.get("id") == actor_id), None)
            target_location = actor.get("location", "") if actor else ""
            for info in result.informationDiscovered:
                _information_layer.apply_information(
                    world,
                    {
                        "source": source,
                        "reliability": 0.8,
                        "confidence": 0.8,
                        "timestamp": world.currentDay,
                        "content": info.get("content", ""),
                    },
                    target_location=target_location,
                )

            facts = self._get_player_facts(room_state, actor_id)
            if facts is not None:
                reveal_state = _information_layer.get_map_reveal_state(world, player_id=actor_id)
                facts["mapStatus"] = reveal_state.get("statusText")

        if result.actionType == "travel" and room_state is not None:
            facts = self._get_player_facts(room_state, actor_id)
            if facts is not None:
                for change in result.playerStatusChanges:
                    if change.get("playerId") == actor_id and "location" in change:
                        facts["location"] = change["location"]

    @staticmethod
    def _get_player_facts(room_state: dict[str, Any] | None, actor_id: str) -> dict[str, Any] | None:
        if not isinstance(room_state, dict):
            return None
        players = room_state.get("players", {})
        if not isinstance(players, dict):
            return None
        slot = actor_id.removeprefix("player_")
        player = players.get(slot) or players.get(actor_id)
        if not isinstance(player, dict):
            player = next(
                (item for item in players.values() if isinstance(item, dict) and item.get("id") == actor_id),
                None,
            )
        if not isinstance(player, dict):
            return None
        return player.setdefault("facts", {})

    def _roll_up_period(self, daily_outcomes: list[dict[str, Any]], period_days: int) -> dict[str, Any]:
        scores = {
            "critical_failure": 0.0,
            "failure": 0.25,
            "partial_success": 0.5,
            "success": 0.75,
            "critical_success": 1.0,
        }
        average = sum(scores.get(item["outcomeTier"], 0.5) for item in daily_outcomes) / len(daily_outcomes)
        if average >= 0.9:
            tier = "critical_success"
        elif average >= 0.65:
            tier = "success"
        elif average >= 0.4:
            tier = "partial_success"
        elif average >= 0.15:
            tier = "failure"
        else:
            tier = "critical_failure"
        return {
            "periodDays": period_days,
            "daysResolved": len({item["day"] for item in daily_outcomes}),
            "outcomeTier": tier,
            "dailyOutcomes": daily_outcomes,
        }

    def _simulate_factions(self, world: WorldState, hours: int, event_queue: WorldEventQueue | None = None) -> None:
        if not world.factions:
            return

        for index, faction in enumerate(world.factions):
            if not isinstance(faction, dict):
                continue
            self._normalize_faction(faction)
            action = self._choose_faction_action(world, faction, hours, index)
            self._apply_faction_action(world, faction, action, hours, index, event_queue=event_queue)
            faction["lastAction"] = action
            world.activeEvents.append({
                "type": "faction_action",
                "day": world.currentDay,
                "summary": f"{faction.get('name', faction.get('id', 'Faction'))} chose to {action}.",
            })
            if action in {"attack", "raid_settlements", "split_into_new_factions", "collapse"}:
                append_world_history_event(world, {
                    "type": "faction_action",
                    "description": f"{faction.get('name', faction.get('id', 'Faction'))} performed {action}.",
                    "day": world.currentDay,
                    "timestamp": world.currentDay,
                })

    def _normalize_faction(self, faction: dict[str, Any]) -> None:
        faction.setdefault("territory", [])
        faction.setdefault("population", 1)
        faction.setdefault("militaryStrength", 1)
        faction.setdefault("resources", {})
        faction.setdefault("ideology", "survival")
        faction.setdefault("goals", [])
        faction.setdefault("relationships", {})

    def _choose_faction_action(self, world: WorldState, faction: dict[str, Any], hours: int, index: int) -> str:
        resources = faction.get("resources", {})
        food = resources.get("food", 0)
        water = resources.get("water", 0)
        military = faction.get("militaryStrength", 1)
        population = faction.get("population", 1)
        ideology = faction.get("ideology", "survival")
        resource_pressure = int(food <= 0) + int(water <= 0)
        player_pressure = sum(
            1
            for item in world.worldHistory[-8:]
            if isinstance(item, dict) and any(word in str(item.get("summary", "")).lower() for word in ("trade", "negotiate", "attack", "recruit", "travel", "build"))
        )
        seed = self._stable_seed(f"{world.currentDay}:{faction.get('id', 'faction')}:{hours}:{index}", self.seed + index)

        if resource_pressure >= 2 and (seed % 5) == 0:
            return "collapse"
        if food <= 1 and (seed % 4) == 0:
            return "raid_settlements"
        if population > 4 and (seed % 7) == 0:
            return "split_into_new_factions"
        if military >= 3 and (seed % 6) == 0 and self._has_other_faction(world, faction):
            return "attack"
        if ideology == "resource_acquisition" and (seed % 5) == 0:
            return "raid_settlements"
        if player_pressure > 0 and (seed % 3) == 0:
            return "negotiate" if (seed % 2) == 0 else "trade"
        if military >= 2 and (seed % 4) == 0:
            return "establish_checkpoints"
        if population > 2 and (seed % 5) == 1:
            return "recruit_survivors"
        if (seed % 3) == 0:
            return "expand_territory"
        return "trade" if self._has_other_faction(world, faction) else "negotiate"

    def _apply_faction_action(self, world: WorldState, faction: dict[str, Any], action: str, hours: int, index: int, event_queue: WorldEventQueue | None = None) -> None:
        resources = faction.setdefault("resources", {})
        food = resources.get("food", 0)
        water = resources.get("water", 0)
        military = faction.get("militaryStrength", 1)
        population = faction.get("population", 1)
        territory = faction.setdefault("territory", [])
        ideology = faction.get("ideology", "survival")
        seed = self._stable_seed(f"{world.currentDay}:{faction.get('id', 'faction')}:{hours}:{index}:{action}", self.seed + index + 11)

        target = self._select_target_faction(world, faction)
        if target is not None and action not in {"attack", "trade", "negotiate", "raid_settlements"}:
            self._update_relationship(world, faction.get("id"), target.get("id"), 1)

        summary = None
        if action == "expand_territory":
            candidates = [location for location in world.locations if location.get("id") not in territory]
            if candidates:
                location = candidates[0]
                territory.append(location.get("id"))
                summary = f"{faction.get('name', 'Faction')} expanded into {location.get('name', location.get('id', 'new territory'))}."
            else:
                summary = f"{faction.get('name', 'Faction')} consolidated control over existing territory."
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
        elif action == "recruit_survivors":
            faction["population"] = population + 1
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
            summary = f"{faction.get('name', 'Faction')} recruited new survivors to its ranks."
        elif action == "attack":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), -20 - (seed % 10))
                self._update_relationship(world, target.get("id"), faction.get("id"), -15 - (seed % 8))
                faction["militaryStrength"] = min(10, military + 1)
                target["militaryStrength"] = max(1, target.get("militaryStrength", 1) - 1)
                resources["food"] = max(0, food - 2)
                summary = f"{faction.get('name', 'Faction')} attacked {target.get('name', 'a rival faction')}."
            else:
                resources["food"] = max(0, food - 1)
                summary = f"{faction.get('name', 'Faction')} pressed their advantage against nearby rivals."
        elif action == "trade":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), 10 + (seed % 5))
                self._update_relationship(world, target.get("id"), faction.get("id"), 8 + (seed % 4))
                summary = f"{faction.get('name', 'Faction')} traded with {target.get('name', 'a rival faction')} for supplies."
            else:
                summary = f"{faction.get('name', 'Faction')} tested the market for barter and fuel."
            resources["water"] = water + 1
            resources["food"] = max(0, food - 1)
        elif action == "establish_checkpoints":
            territory = list(territory)
            checkpoint_site = territory[0] if territory else "checkpoint"
            location_name = next((item.get("name") for item in world.locations if item.get("id") == checkpoint_site), checkpoint_site)
            if len(territory) < 3:
                territory.append(checkpoint_site)
            faction["territory"] = territory
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
            summary = f"{faction.get('name', 'Faction')} established checkpoints around {location_name}."
        elif action == "raid_settlements":
            resources["food"] = food + 2
            resources["water"] = max(0, water - 1)
            faction["militaryStrength"] = min(10, military + 1)
            summary = f"{faction.get('name', 'Faction')} raided settlements for food and tools."
            if ideology != "survival":
                self._update_relationship(world, faction.get("id"), self._first_other_faction_id(world, faction), -12)
        elif action == "negotiate":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), 12 + (seed % 6))
                self._update_relationship(world, target.get("id"), faction.get("id"), 9 + (seed % 4))
                summary = f"{faction.get('name', 'Faction')} negotiated with {target.get('name', 'a rival faction')} over routes and rations."
            else:
                summary = f"{faction.get('name', 'Faction')} sought a temporary accord with nearby groups."
            resources["food"] = max(0, food - 1)
        elif action == "collapse":
            faction["population"] = max(1, population // 2)
            faction["militaryStrength"] = max(1, military // 2)
            resources["food"] = max(0, food // 2)
            resources["water"] = max(0, water // 2)
            summary = f"{faction.get('name', 'Faction')} collapsed under supply pressure and internal division."
        elif action == "split_into_new_factions":
            new_faction = {
                "id": f"{faction.get('id', 'faction')}_splinter",
                "name": f"{faction.get('name', 'Faction')} Splinter",
                "population": max(1, population // 2),
                "militaryStrength": max(1, military // 2),
                "resources": {key: max(0, value // 2) for key, value in resources.items()},
                "territory": list(territory[:1]),
                "ideology": ideology,
                "relationships": {},
                "goals": list(faction.get("goals", [])),
            }
            faction["population"] = max(1, population - new_faction["population"])
            world.factions.append(new_faction)
            summary = f"A splinter broke off from {faction.get('name', 'Faction')}: {new_faction.get('name', 'New Faction')}."
        else:
            resources["food"] = max(0, food - 1)
            summary = f"{faction.get('name', 'Faction')} adjusted operations to survive the week."

        if summary and event_queue is not None:
            event_queue.add(WorldEvent(day=world.currentDay, summary=summary, event_type="faction_action"))

    def _has_other_faction(self, world: WorldState, faction: dict[str, Any]) -> bool:
        return len([item for item in world.factions if isinstance(item, dict) and item.get("id") != faction.get("id")]) > 0

    def _select_target_faction(self, world: WorldState, faction: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [item for item in world.factions if isinstance(item, dict) and item.get("id") != faction.get("id")]
        return candidates[0] if candidates else None

    def _first_other_faction_id(self, world: WorldState, faction: dict[str, Any]) -> str | None:
        target = self._select_target_faction(world, faction)
        return target.get("id") if target is not None else None

    def _update_relationship(self, world: WorldState, source_id: str, target_id: str, delta: int) -> None:
        source = next((item for item in world.factions if isinstance(item, dict) and item.get("id") == source_id), None)
        target = next((item for item in world.factions if isinstance(item, dict) and item.get("id") == target_id), None)
        if source is None or target is None:
            return
        current = source.setdefault("relationships", {}).get(target_id, 0)
        new_score = max(-100, min(100, current + delta))
        source.setdefault("relationships", {})[target_id] = new_score
        world.relationships.append({"from": source_id, "to": target_id, "score": new_score, "delta": delta})

    def _stable_seed(self, day: int, seed: int) -> int:
        digest = hashlib.sha256(f"{day}:{seed}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)
