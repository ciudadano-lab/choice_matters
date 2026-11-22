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

from world_state import WorldState, append_world_history_event
from simulation_engine import SimulationEngine, StructuredAction
from information_layer import InformationLayer
from location_lookup import resolve_location_id
from probability_config import TIME_SLOTS, clamp_player_stat
from plan_features import PlanFeatures
from factor_library import get_library
from economy import (
    cap_resource_stores,
    get_daily_resource_consumption,
    get_health_recovery_config,
)
from encounter_resolution import apply_encounter_outcome, resolve_encounter
from settlement_tick import SettlementTick


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
        self.last_policy_day_outcomes: list[dict[str, Any]] = []
        self.settlement_tick = SettlementTick(seed)

    @staticmethod
    def _time_to_minutes(value: Any) -> int:
        if not isinstance(value, str) or ":" not in value:
            return 0
        try:
            hour_text, minute_text = value.split(":", 1)
            return int(hour_text) * 60 + int(minute_text)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def slot_name_for_time(cls, value: Any) -> str:
        current_minutes = cls._time_to_minutes(value)
        current_slot = "night"
        for slot_name, slot_time in TIME_SLOTS:
            if current_minutes >= cls._time_to_minutes(slot_time):
                current_slot = slot_name
            else:
                break
        return current_slot

    @classmethod
    def slot_time_for_name(cls, slot_name: str) -> str:
        for name, slot_time in TIME_SLOTS:
            if name == slot_name:
                return slot_time
        return "06:00"

    def advance_slot(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        *,
        run_autopilot: bool = True,
        room_state: dict[str, Any] | None = None,
    ) -> WorldState:
        state = copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        world = WorldState.from_dict(state)

        current_slot = self.slot_name_for_time(world.currentTime)
        slot_index = next(
            (index for index, (slot_name, _) in enumerate(TIME_SLOTS) if slot_name == current_slot),
            0,
        )
        next_slot_index = (slot_index + 1) % len(TIME_SLOTS)
        next_slot_name, next_time = TIME_SLOTS[next_slot_index]
        if next_slot_name == "dawn":
            if world.currentTime != "22:00":
                world.currentTime = "22:00"
            return self.advance(
                world,
                event_queue,
                hours=8,
                run_autopilot=False,
                room_state=room_state,
            )
        world.currentTime = next_time
        return world

    def _plan_slot(self, policy: dict[str, Any]) -> str:
        features = PlanFeatures.from_untrusted(policy.get("planFeatures"))
        timing = features.timing if features is not None else "unspecified"
        return "day" if timing == "unspecified" else timing

    @staticmethod
    def _find_location(world: WorldState, location_id: str | None) -> dict[str, Any] | None:
        if location_id is None:
            return None
        if isinstance(world.locations, list):
            return next(
                (
                    location for location in world.locations
                    if isinstance(location, dict) and location.get("id") == location_id
                ),
                None,
            )
        if isinstance(world.locations, dict):
            location = world.locations.get(location_id)
            return location if isinstance(location, dict) else None
        return None

    def _is_outdoors(self, world: WorldState, player: dict[str, Any]) -> bool:
        location_id = resolve_location_id(world.to_dict(), player.get("location"))
        location = self._find_location(world, location_id)
        if location is None:
            return True
        location_type = str(location.get("type", "")).casefold().replace(" ", "_")
        if location_type in {"shelter", "safehouse"} or location.get("isShelter") is True:
            return False
        flags = location.get("flags", {})
        if not isinstance(flags, dict):
            return True
        if flags.get("shelter") is True:
            return False
        if flags.get("fortified") not in (True, 1):
            return True
        expiries = location.get("flagExpiries", {})
        expires_day = expiries.get("fortified") if isinstance(expiries, dict) else None
        try:
            return expires_day is not None and int(world.currentDay) >= int(expires_day)
        except (TypeError, ValueError):
            return False

    def _record_slot_result(
        self,
        world: WorldState,
        result: Any,
        player_id: str,
        slot_name: str,
        slot_day: int,
        slot_time: str,
        status_effects_before: int,
    ) -> dict[str, Any]:
        record = {
            "day": slot_day,
            "slot": slot_name,
            "slotTime": slot_time,
            "playerId": player_id,
            "actionType": result.actionType,
            "outcomeTier": result.outcomeTier,
            "rollLog": [
                step.to_dict() if hasattr(step, "to_dict") else copy.deepcopy(step)
                for step in result.rollLog
            ],
            "interruptStep": result.interruptStep,
            "encounterOccurred": result.encounterOccurred,
            "encounterOutcome": copy.deepcopy(result.encounterOutcome),
            "injuries": max(
                0,
                sum(
                    len(player.get("statusEffects", []))
                    for player in result.updatedWorldState.get("players", [])
                    if isinstance(player, dict) and player.get("id") == player_id
                ) - status_effects_before,
            ),
        }
        if result.interruptStep is not None:
            record["pauseRecord"] = {
                "day": slot_day,
                "slot": slot_name,
                "playerId": player_id,
                "actionType": result.actionType,
            }
        return record

    def advance_policy_day(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        *,
        room_state: dict[str, Any] | None = None,
    ) -> WorldState:
        world = WorldState.from_dict(
            copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        )
        engine = SimulationEngine(seed=self.seed)
        library = get_library()
        self.last_policy_day_outcomes = []

        for slot_name, slot_time in TIME_SLOTS:
            world.currentTime = slot_time
            slot_day = world.currentDay
            for player in list(world.players):
                if not isinstance(player, dict):
                    continue
                policy = player.get("currentPolicy")
                if not isinstance(policy, dict) or self._plan_slot(policy) != slot_name:
                    continue
                if player.get("alive", True) is False or player.get("health", 100) <= 0:
                    continue
                actor_id = str(player.get("id", ""))
                status_effects_before = len(player.get("statusEffects", []))
                result = engine.auto_resolve_policy_day(
                    world,
                    actor_id,
                    policy,
                    world.currentDay,
                )
                action = result.resolvedAction
                if result.encounterOccurred:
                    if action is None:
                        raise RuntimeError("Triggered encounter is missing its resolved action")
                    result = self.resolve_encounter_after_action(
                        world,
                        result,
                        action,
                        seed=action.parameters.get("seed", self.seed),
                    )
                if result.actionType == "establish_settlement" and action is not None:
                    target_location_id = resolve_location_id(
                        result.updatedWorldState,
                        action.target,
                    ) or action.target
                    result.updatedWorldState = self.settlement_tick.apply_foundation_grant(
                        result.updatedWorldState,
                        settlement_id=action.actionId,
                        location_id=target_location_id,
                        action_id=action.actionId,
                    )
                world = WorldState.from_dict(result.updatedWorldState)
                self._apply_tactical_side_effects(world, result, actor_id, room_state)
                self.last_policy_day_outcomes.append(
                    self._record_slot_result(
                        world,
                        result,
                        actor_id,
                        slot_name,
                        slot_day,
                        slot_time,
                        status_effects_before,
                    )
                )

            if slot_name == "night" and library.ambient_night is not None:
                ambient = library.ambient_night
                if ambient.enabled:
                    for player in list(world.players):
                        if (
                            not isinstance(player, dict)
                            or player.get("alive", True) is False
                            or player.get("health", 100) <= 0
                            or not self._is_outdoors(world, player)
                        ):
                            continue
                        actor_id = str(player.get("id", ""))
                        location_id = resolve_location_id(
                            world.to_dict(),
                            player.get("location"),
                        )
                        if location_id is None:
                            continue
                        status_effects_before = len(player.get("statusEffects", []))
                        ambient_action = StructuredAction(
                            actionId=f"ambient-{actor_id}-{world.currentDay}-night",
                            actionType="ambient_noise",
                            actor=actor_id,
                            target=location_id,
                            parameters={
                                "seed": self.seed,
                                "chainTemplate": ambient.chain_template,
                            },
                            timeCost=0,
                        )
                        result = engine.apply(world, ambient_action)
                        if result.encounterOccurred:
                            result = self.resolve_encounter_after_action(
                                world,
                                result,
                                ambient_action,
                                seed=self.seed,
                            )
                        world = WorldState.from_dict(result.updatedWorldState)
                        ambient_record = self._record_slot_result(
                            world,
                            result,
                            actor_id,
                            slot_name,
                            slot_day,
                            slot_time,
                            status_effects_before,
                        )
                        ambient_record["isAmbient"] = True
                        self.last_policy_day_outcomes.append(
                            ambient_record
                        )

            world = self.advance_slot(
                world,
                event_queue,
                run_autopilot=False,
                room_state=room_state,
            )

        return world

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
        days_elapsed = max(0, new_day - current_day)

        weather_options = ["clear", "rain", "storm"]
        world.weather = weather_options[(self._stable_seed(world.currentDay, self.seed) + hours) % len(weather_options)]

        daily_consumption = get_daily_resource_consumption()
        living_players = sum(
            player.get("alive", True) is not False and player.get("health", 100) > 0
            for player in world.players
        )
        food_available = world.resources.get("food", 0) >= (
            daily_consumption["food"] * living_players
        )
        water_available = world.resources.get("water", 0) >= (
            daily_consumption["water"] * living_players
        )
        for player in world.players:
            health_before_effects = player.get("health", 100)
            active_effects: list[dict[str, Any]] = []
            for effect in player.get("statusEffects", []):
                if not isinstance(effect, dict):
                    continue
                expires_day = effect.get("expiresDay")
                mechanical = effect.get("mechanicalEffect", {})
                if isinstance(mechanical, dict):
                    for stat, delta in mechanical.items():
                        if stat in {"health", "stamina", "hunger", "thirst", "stress"}:
                            current = player.get(stat, 0)
                            player[stat] = clamp_player_stat(current + int(delta))
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
            recovery = get_health_recovery_config()
            if food_available:
                player["hunger"] = min(
                    50,
                    clamp_player_stat(
                        player.get("hunger", 50)
                        + recovery["fed_satiety_recovery_per_day"]
                    ),
                )
            else:
                player["hunger"] = clamp_player_stat(
                    player.get("hunger", 50)
                    - recovery["empty_food_satiety_loss_per_day"]
                )
            if water_available:
                player["thirst"] = min(
                    50,
                    clamp_player_stat(
                        player.get("thirst", 50)
                        + recovery["fed_satiety_recovery_per_day"]
                    ),
                )
            else:
                player["thirst"] = clamp_player_stat(
                    player.get("thirst", 50)
                    - recovery["empty_water_satiety_loss_per_day"]
                )
            player["stress"] = clamp_player_stat(player.get("stress", 0) + 1)
            health = clamp_player_stat(player.get("health", 100))
            death_cause = None
            if health <= 0 and health_before_effects > 0:
                death_cause = "combat/injury"
                player["deathCause"] = death_cause
            elif health > 0:
                if (
                    world.resources.get("food", 0)
                    >= recovery["health_regen_min_food"] * living_players
                    and world.resources.get("water", 0)
                    >= recovery["health_regen_min_water"] * living_players
                ):
                    health += recovery["health_regen_per_day"]
                if not food_available and player["hunger"] <= 0:
                    health -= recovery["starvation_health_loss_per_day"]
                    death_cause = "starvation"
                if not water_available and player["thirst"] <= 0:
                    health -= recovery["dehydration_health_loss_per_day"]
                    death_cause = "dehydration"
                player["health"] = clamp_player_stat(health)
                if player["health"] <= 0 and death_cause is not None:
                    player["deathCause"] = death_cause

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

        world.resources["food"] = max(
            0,
            world.resources.get("food", 0)
            - daily_consumption["food"] * living_players,
        )
        world.resources["water"] = max(
            0,
            world.resources.get("water", 0)
            - daily_consumption["water"] * living_players,
        )
        world.resources = cap_resource_stores(
            world.resources,
            len(world.players),
        )
        if days_elapsed:
            world = self.settlement_tick.advance_days(
                world,
                days_elapsed,
                first_day=current_day + 1,
            )

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
        daily_outcomes: list[dict[str, Any]] = []

        for _ in range(days):
            world = self.advance_policy_day(
                world,
                event_queue,
                room_state=room_state,
            )
            daily_outcomes.extend(copy.deepcopy(self.last_policy_day_outcomes))

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

    def resolve_encounter_after_action(
        self,
        state: WorldState | dict[str, Any],
        result: Any,
        action: Any,
        *,
        seed: int | None = None,
    ) -> Any:
        if not result.encounterOccurred:
            return result
        state_dict = state.to_dict() if hasattr(state, "to_dict") else state
        players = state_dict.get("players", []) if isinstance(state_dict, dict) else []
        if isinstance(players, dict):
            player = players.get(action.actor)
        elif isinstance(players, list):
            player = next(
                (
                    item for item in players
                    if isinstance(item, dict) and item.get("id") == action.actor
                ),
                None,
            )
        else:
            player = None
        if not isinstance(player, dict):
            raise ValueError(f"Encounter actor {action.actor!r} is missing")

        outcome = resolve_encounter(
            state,
            player,
            action,
            result,
            self.seed if seed is None else seed,
        )
        slot = self.slot_name_for_time(state_dict.get("currentTime"))
        outcome_record = outcome.to_dict()
        outcome_record["slot"] = {
            "day": int(state_dict.get("currentDay", 1)),
            "name": slot,
        }
        result.updatedWorldState = apply_encounter_outcome(
            result.updatedWorldState,
            action.actor,
            outcome,
        )
        result.encounterOutcome = outcome_record
        result.encounterDetails = {
            **result.encounterDetails,
            "resolution": outcome_record,
        }
        event = {
            "type": "encounter_resolution",
            "day": int(state_dict.get("currentDay", 1)),
            "slot": slot,
            "summary": f"Encounter resolved as {outcome.outcome}.",
            "payload": outcome.to_dict(),
        }
        result.worldEventsTriggered.append(event)
        updated_state = result.updatedWorldState
        updated_world = (
            updated_state["world_state"]
            if isinstance(updated_state, dict)
            and isinstance(updated_state.get("world_state"), dict)
            else updated_state
        )
        if isinstance(updated_world, dict):
            updated_world.setdefault("activeEvents", []).append(copy.deepcopy(event))
        return result

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
            target_location = resolve_location_id(
                world.to_dict(),
                actor.get("location"),
            ) if actor else None
            if target_location is None:
                target_location = ""
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
