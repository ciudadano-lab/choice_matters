from __future__ import annotations

import copy
import hashlib
import json
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
from world_content import load_world_content


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
        state = (
            world_state.to_dict()
            if hasattr(world_state, "to_dict")
            else copy.deepcopy(world_state)
        )
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
        prior_active_events = {
            self._thread_event_signature(event)
            for event in world.activeEvents
            if isinstance(event, dict)
        }
        prior_internal_events = {
            self._thread_event_signature(event)
            for event in world.internalEvents
            if isinstance(event, dict)
        }
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
                if action is not None:
                    self.apply_faction_relationship_changes(result, action)
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
                if action is not None:
                    _information_layer.record_action_discoveries(
                        result.updatedWorldState,
                        action_type=action.actionType,
                        actor_id=actor_id,
                        target=action.target,
                        parameters=action.parameters,
                        outcome_tier=result.outcomeTier,
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
                        result = engine.apply(world, ambient_action, rules="current")
                        if result.encounterOccurred:
                            result = self.resolve_encounter_after_action(
                                world,
                                result,
                                ambient_action,
                                seed=self.seed,
                            )
                        self.apply_faction_relationship_changes(result, ambient_action)
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

        self._sync_threads_for_new_world_events(
            world,
            room_state,
            prior_active_events,
            prior_internal_events,
        )
        return world

    @staticmethod
    def _thread_event_signature(event: dict[str, Any]) -> str:
        return json.dumps(event, sort_keys=True, separators=(",", ":"), default=str)

    def _sync_threads_for_new_world_events(
        self,
        world: WorldState,
        room_state: dict[str, Any] | None,
        prior_active_events: set[str],
        prior_internal_events: set[str],
    ) -> None:
        if not isinstance(room_state, dict):
            return
        room_players = room_state.get("players", {})
        if not isinstance(room_players, dict):
            return

        rules = load_world_content().faction_rules
        rank = {"heard_of": 1, "met": 2, "known": 3}
        discoveries = world.playerKnownWorld.get("players", {})
        faction_by_id = {
            str(item.get("id")): item
            for item in world.factions
            if isinstance(item, dict) and item.get("id") is not None
        }
        settlement_by_id = {
            str(item.get("id")): item
            for item in world.settlements
            if isinstance(item, dict) and item.get("id") is not None
        }
        new_faction_events = [
            event for event in world.activeEvents
            if isinstance(event, dict)
            and event.get("type") == "faction_action"
            and self._thread_event_signature(event) not in prior_active_events
        ]
        new_settlement_events = [
            event for event in world.internalEvents
            if isinstance(event, dict)
            and event.get("type") == "settlement_tick"
            and event.get("statusChanged") is True
            and self._thread_event_signature(event) not in prior_internal_events
        ]

        for player in world.players:
            if not isinstance(player, dict) or player.get("id") is None:
                continue
            player_id = str(player["id"])
            player_discoveries = discoveries.get(player_id, {})
            if not isinstance(player_discoveries, dict):
                continue
            room_player = room_players.get(player_id.removeprefix("player_"))
            if not isinstance(room_player, dict):
                room_player = next(
                    (
                        item for item in room_players.values()
                        if isinstance(item, dict) and item.get("id") == player_id
                    ),
                    None,
                )
            if not isinstance(room_player, dict):
                continue
            threads = room_player.setdefault("threads", [])
            if not isinstance(threads, list):
                threads = []
                room_player["threads"] = threads

            faction_discoveries = player_discoveries.get("factions", {})
            settlement_discoveries = player_discoveries.get("settlements", {})

            def known(entity_type: str, entity_id: str) -> bool:
                records = faction_discoveries if entity_type == "factions" else settlement_discoveries
                level = records.get(entity_id) if isinstance(records, dict) else None
                return level in rank

            for event in new_faction_events:
                target = event.get("target")
                target = target if isinstance(target, dict) else {}
                faction_id = event.get("factionId")
                faction_id = str(faction_id) if faction_id is not None else None
                settlement_id = target.get("settlementId")
                settlement_id = str(settlement_id) if settlement_id is not None else None
                linked_faction = faction_id if faction_id in faction_by_id and known("factions", faction_id) else None
                linked_settlement = (
                    settlement_id
                    if settlement_id in settlement_by_id and known("settlements", settlement_id)
                    else None
                )
                if linked_faction is None and linked_settlement is None:
                    continue
                name = (
                    faction_by_id[linked_faction].get("name", linked_faction)
                    if linked_faction is not None
                    else settlement_by_id[linked_settlement].get("name", linked_settlement)
                )
                thread_key = (
                    f"faction:{linked_faction}"
                    if linked_faction is not None
                    else f"settlement:{linked_settlement}"
                )
                thread_id = "world_" + hashlib.sha256(thread_key.encode("utf-8")).hexdigest()[:16]
                self._upsert_world_thread(
                    threads,
                    {
                        "id": thread_id,
                        "status": "open",
                        "summary": rules.threads.faction_event_summary.format(name=name),
                        "stakes": rules.threads.faction_event_stakes,
                        **({"factionIds": [linked_faction]} if linked_faction is not None else {}),
                        **({"settlementId": linked_settlement} if linked_settlement is not None else {}),
                    },
                    rules.retention.player_threads,
                )

            for event in new_settlement_events:
                settlement_id = str(event.get("settlementId", ""))
                settlement = settlement_by_id.get(settlement_id)
                if settlement is None or not known("settlements", settlement_id):
                    continue
                faction_id = settlement.get("faction_id")
                linked_faction = (
                    str(faction_id)
                    if faction_id is not None
                    and str(faction_id) in faction_by_id
                    and known("factions", str(faction_id))
                    else None
                )
                thread_id = "world_" + hashlib.sha256(
                    f"settlement:{settlement_id}".encode("utf-8")
                ).hexdigest()[:16]
                self._upsert_world_thread(
                    threads,
                    {
                        "id": thread_id,
                        "status": "open",
                        "summary": rules.threads.settlement_status_summary.format(
                            name=settlement.get("name", settlement_id)
                        ),
                        "stakes": rules.threads.settlement_status_stakes,
                        "settlementId": settlement_id,
                        **({"factionIds": [linked_faction]} if linked_faction is not None else {}),
                    },
                    rules.retention.player_threads,
                )

    @staticmethod
    def _upsert_world_thread(
        threads: list[dict[str, Any]],
        update: dict[str, Any],
        retention_limit: int,
    ) -> None:
        existing = next(
            (
                item for item in threads
                if isinstance(item, dict) and item.get("id") == update["id"]
            ),
            None,
        )
        if existing is None:
            threads.append(update)
        else:
            existing.update(update)
        del threads[:-retention_limit]

    @staticmethod
    def apply_faction_relationship_changes(result: Any, action: StructuredAction) -> None:
        updated_state = result.updatedWorldState
        state = (
            updated_state.get("world_state", updated_state)
            if isinstance(updated_state, dict)
            else {}
        )
        factions = state.get("factions", []) if isinstance(state, dict) else []
        scale = load_world_content().faction_rules.faction_disposition_scale_factor
        for change in result.factionRelationshipChanges:
            faction_id = change.get("factionId")
            faction = next(
                (
                    item for item in factions
                    if isinstance(item, dict) and item.get("id") == faction_id
                ),
                None,
            )
            if faction is None:
                continue
            dispositions = faction.setdefault("player_dispositions", {})
            if not isinstance(dispositions, dict):
                dispositions = {}
                faction["player_dispositions"] = dispositions
            player_id = action.actor
            current = dispositions.get(player_id, 0)
            try:
                updated = int(current) + int(change.get("delta", 0)) * scale
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid faction relationship change for {faction_id!r}: {change!r}"
                )
            limit = load_world_content().faction_rules.max_player_disposition
            dispositions[player_id] = max(-limit, min(limit, updated))

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
        prior_active_events = {
            self._thread_event_signature(event)
            for event in world.activeEvents
            if isinstance(event, dict)
        }
        prior_internal_events = {
            self._thread_event_signature(event)
            for event in world.internalEvents
            if isinstance(event, dict)
        }

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
        self._trim_retained_collections(world)
        self._sync_threads_for_new_world_events(
            world,
            room_state,
            prior_active_events,
            prior_internal_events,
        )
        return world

    def _trim_retained_collections(self, world: WorldState) -> None:
        retention = load_world_content().faction_rules.retention
        world.activeEvents = world.activeEvents[-retention.active_events:]
        world.worldHistory = world.worldHistory[-retention.world_history:]
        world.internalEvents = world.internalEvents[-retention.internal_events:]
        world.relationships = world.relationships[-retention.relationships:]

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
                        "player_id": actor_id,
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
        if world.currentDay % load_world_content().faction_rules.decisions.interval_days != 0:
            return

        factions = sorted(
            (
                faction for faction in world.factions
                if isinstance(faction, dict)
            ),
            key=lambda faction: str(faction.get("id", "")),
        )
        for faction in factions:
            if not isinstance(faction, dict):
                continue
            self._normalize_faction(faction)
            action = self._choose_faction_action(world, faction, hours)
            self._apply_faction_action(world, faction, action, hours, event_queue=event_queue)
            recent_actions = faction.setdefault("recentActions", [])
            if not isinstance(recent_actions, list):
                recent_actions = []
                faction["recentActions"] = recent_actions
            recent_actions.append(action)
            faction["recentActions"] = recent_actions[-3:]
            faction["lastAction"] = action
            faction["lastDecisionDay"] = world.currentDay
            faction["lastDecisionTarget"] = self._select_player_decision_target(
                world,
                faction,
                action,
            )
            world.activeEvents.append({
                "type": "faction_action",
                "day": world.currentDay,
                "factionId": faction.get("id"),
                "summary": f"{faction.get('name', faction.get('id', 'Faction'))} chose to {action}.",
                "decisionFactors": copy.deepcopy(faction.get("lastDecisionFactors", {})),
                "decisionInfluences": copy.deepcopy(faction.get("lastDecisionInfluences", {})),
                "target": copy.deepcopy(faction.get("lastDecisionTarget")),
            })
            if action in {"attack", "raid_settlements", "split_into_new_factions", "collapse"}:
                append_world_history_event(world, {
                    "type": "faction_action",
                    "description": f"{faction.get('name', faction.get('id', 'Faction'))} performed {action}.",
                    "day": world.currentDay,
                    "timestamp": world.currentDay,
                })

    def _normalize_faction(self, faction: dict[str, Any]) -> None:
        territory = faction.setdefault("territory", [])
        if isinstance(territory, list):
            faction["territory"] = sorted(set(str(item) for item in territory))
        faction.setdefault("population", 1)
        faction.setdefault("militaryStrength", 1)
        faction.setdefault("resources", {})
        faction.setdefault("ideology", "survival")
        faction.setdefault("goals", [])
        faction.setdefault("relationships", {})

    def _choose_faction_action(self, world: WorldState, faction: dict[str, Any], hours: int) -> str:
        scores = self._faction_action_scores(world, faction, hours)
        rules = load_world_content().faction_rules.decisions
        seed = self._stable_seed(
            f"faction_decision:{world.currentDay}:{faction.get('id', 'faction')}:{hours}",
            self.seed,
        )
        selected = self._weighted_faction_action(scores, seed, rules.weighted_draw_k)
        modifiers = self._player_decision_modifiers(world, faction)
        faction["lastDecisionFactors"] = {
            category: any(value > 0 for value in bonuses.values())
            for category, bonuses in modifiers.items()
        }
        influences: dict[str, bool] = {}
        for category, bonuses in modifiers.items():
            without_category = dict(scores)
            for action, amount in bonuses.items():
                without_category[action] -= amount
            influences[category] = (
                self._weighted_faction_action(
                    without_category,
                    seed,
                    rules.weighted_draw_k,
                )
                != selected
            )
        faction["lastDecisionInfluences"] = influences
        return selected

    @staticmethod
    def _weighted_faction_action(scores: dict[str, int], seed: int, exponent: int) -> str:
        actions = sorted(scores)
        weights = [max(scores[action], 0) ** exponent for action in actions]
        total = sum(weights)
        if total <= 0:
            return actions[0]
        threshold = (seed % 2**32) / 2**32 * total
        cumulative = 0.0
        for action, weight in zip(actions, weights):
            cumulative += weight
            if threshold < cumulative:
                return action
        return actions[-1]

    def _player_decision_modifiers(
        self,
        world: WorldState,
        faction: dict[str, Any],
    ) -> dict[str, dict[str, int]]:
        rules = load_world_content().faction_rules
        decision_rules = rules.decisions
        modifiers = {
            "hostile_disposition": {},
            "friendly_disposition": {},
            "attention_pressure": {},
        }
        dispositions = faction.get("player_dispositions", {})
        if isinstance(dispositions, dict):
            for player_id in sorted(dispositions):
                try:
                    disposition = int(dispositions[player_id])
                except (TypeError, ValueError):
                    continue
                if disposition <= -decision_rules.player_hostile_threshold:
                    bonus = max(
                        decision_rules.minimum_player_decision_bonus,
                        abs(disposition) * decision_rules.player_hostile_action_weight
                        // rules.max_player_disposition,
                    )
                    modifiers["hostile_disposition"]["raid_settlements"] = (
                        modifiers["hostile_disposition"].get("raid_settlements", 0) + bonus
                    )
                    modifiers["hostile_disposition"]["attack"] = (
                        modifiers["hostile_disposition"].get("attack", 0) + bonus
                    )
                elif disposition >= decision_rules.player_friendly_threshold:
                    bonus = max(
                        decision_rules.minimum_player_decision_bonus,
                        disposition * decision_rules.player_friendly_action_weight
                        // rules.max_player_disposition,
                    )
                    modifiers["friendly_disposition"]["trade"] = (
                        modifiers["friendly_disposition"].get("trade", 0) + bonus
                    )
                    modifiers["friendly_disposition"]["negotiate"] = (
                        modifiers["friendly_disposition"].get("negotiate", 0) + bonus
                    )

        attention_by_player: dict[str, int] = {}
        for settlement in world.settlements:
            if not isinstance(settlement, dict):
                continue
            attention = settlement.get("attention_by_player", {})
            if not isinstance(attention, dict):
                continue
            for player_id in sorted(attention):
                try:
                    value = max(0, int(attention[player_id]))
                except (TypeError, ValueError):
                    continue
                attention_by_player[player_id] = (
                    attention_by_player.get(player_id, 0) + value
                )

        for player_id in sorted(attention_by_player):
            attention = min(rules.max_player_attention, attention_by_player[player_id])
            if attention <= 0:
                continue
            bonus = max(
                decision_rules.minimum_player_decision_bonus,
                attention * decision_rules.attention_pressure_weight
                // rules.max_player_attention,
            )
            for action in ("attack", "raid_settlements"):
                modifiers["attention_pressure"][action] = (
                    modifiers["attention_pressure"].get(action, 0) + bonus
                )
        return modifiers

    def _select_player_decision_target(
        self,
        world: WorldState,
        faction: dict[str, Any],
        action: str,
    ) -> dict[str, str] | None:
        faction_rules = load_world_content().faction_rules
        decision_rules = faction_rules.decisions
        dispositions = faction.get("player_dispositions", {})
        candidates: list[tuple[int, str, str]] = []
        if not isinstance(dispositions, dict):
            dispositions = {}
        for settlement in world.settlements:
            if not isinstance(settlement, dict):
                continue
            attention = settlement.get("attention_by_player", {})
            if not isinstance(attention, dict):
                attention = {}
            for player_id in sorted(set(dispositions) | set(attention)):
                try:
                    disposition = int(dispositions.get(player_id, 0))
                    player_attention = int(attention.get(player_id, 0))
                except (TypeError, ValueError):
                    continue
                if action in {"attack", "raid_settlements"}:
                    is_relevant = (
                        disposition <= -decision_rules.player_hostile_threshold
                        or player_attention > 0
                    )
                    hostility = abs(min(0, disposition))
                    priority = (
                        hostility * faction_rules.max_player_attention + player_attention
                        if hostility >= decision_rules.player_hostile_threshold
                        else player_attention
                    )
                elif action in {"trade", "negotiate"}:
                    is_relevant = disposition >= decision_rules.player_friendly_threshold
                    priority = disposition
                else:
                    is_relevant = False
                    priority = 0
                settlement_player_id = settlement.get(
                    "founderId",
                    settlement.get("founder_id", settlement.get("player_id")),
                )
                if settlement_player_id not in {None, player_id} and player_attention <= 0:
                    continue
                if settlement_player_id is None and player_attention <= 0:
                    player = next(
                        (
                            item for item in world.players
                            if isinstance(item, dict)
                            and str(item.get("id")) == str(player_id)
                        ),
                        None,
                    )
                    player_location = resolve_location_id(
                        world.to_dict(),
                        player.get("location") if player else None,
                    )
                    settlement_location = settlement.get(
                        "location_id",
                        settlement.get("location"),
                    )
                    if player_location is None or settlement_location != player_location:
                        continue
                if is_relevant and priority > 0:
                    candidates.append((
                        priority,
                        str(player_id),
                        str(settlement.get("id", settlement.get("location_id", ""))),
                    ))
        if not candidates:
            return None
        priority, player_id, settlement_id = sorted(
            candidates,
            key=lambda item: (-item[0], item[1], item[2]),
        )[0]
        return {"playerId": player_id, "settlementId": settlement_id}

    def _faction_action_scores(
        self,
        world: WorldState,
        faction: dict[str, Any],
        hours: int,
    ) -> dict[str, int]:
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
        faction_id = str(faction.get("id", "faction"))
        rules = load_world_content().faction_rules.decisions
        scores = dict(rules.base_scores)
        scores["collapse"] += resource_pressure * rules.resource_pressure_weight
        scores["raid_settlements"] += resource_pressure * rules.resource_pressure_weight
        scores["attack"] += military * rules.military_weight
        scores["establish_checkpoints"] += military * rules.military_weight
        scores["split_into_new_factions"] += population * rules.population_weight
        scores["negotiate"] += player_pressure * rules.player_pressure_weight
        scores["trade"] += player_pressure * rules.player_pressure_weight
        needed_settlements = sum(
            1
            for settlement in world.settlements
            if isinstance(settlement, dict)
            and settlement.get("faction_id") == faction_id
            and settlement.get("needs")
        )
        scores["raid_settlements"] += needed_settlements * rules.settlement_need_weight
        scores["trade"] += needed_settlements * rules.settlement_need_weight
        player_modifiers = self._player_decision_modifiers(world, faction)
        for bonuses in player_modifiers.values():
            for action, bonus in bonuses.items():
                scores[action] = scores.get(action, 0) + bonus
        if ideology == "resource_acquisition":
            scores["raid_settlements"] += 12
            scores["trade"] += 8
        if resource_pressure == 0:
            scores["collapse"] = -100

        settlement_count = max(
            1,
            sum(
            1
            for settlement in world.settlements
            if isinstance(settlement, dict)
            and settlement.get("faction_id") == faction_id
            ),
        )
        capacity = (
            rules.capacity_per_settlement * settlement_count
            + rules.capacity_per_faction
        )
        if population >= capacity:
            scores["recruit_survivors"] -= rules.recruiting_capacity_weight
        else:
            utilization = population / capacity
            scores["recruit_survivors"] -= int(
                rules.recruiting_capacity_weight * utilization
            )

        disposition_values = faction.get("relationships", {})
        if isinstance(disposition_values, dict):
            for target_id in sorted(disposition_values):
                disposition = disposition_values.get(target_id)
                if not isinstance(disposition, int):
                    continue
                if disposition <= -30:
                    hostility = min(100, -disposition)
                    scores["raid_settlements"] += (
                        hostility * rules.hostile_disposition_weight // 100
                    )
                    scores["attack"] += (
                        hostility * rules.hostile_disposition_weight // 100
                    )
                elif disposition >= 30:
                    friendliness = min(100, disposition)
                    scores["trade"] += (
                        friendliness * rules.friendly_disposition_weight // 100
                    )
                    scores["negotiate"] += (
                        friendliness * rules.friendly_disposition_weight // 100
                    )

        goals = faction.get("goals", [])
        if isinstance(goals, list):
            for goal in goals:
                target = rules.goal_action_targets.get(str(goal))
                if target in scores:
                    scores[target] += rules.goal_action_bonus

        recent_actions = faction.get("recentActions", [])
        if not isinstance(recent_actions, list):
            recent_actions = []
        last_action = faction.get("lastAction")
        for action in scores:
            if action == last_action:
                scores[action] -= rules.repeat_penalty
            if action in recent_actions[-2:] and action != last_action:
                scores[action] -= rules.repeat_last_two_penalty

        ruleset = load_world_content().faction_rules
        if len(world.factions) >= ruleset.max_factions:
            scores["split_into_new_factions"] = -100
        if population < ruleset.splinter.minimum_parent_population:
            scores["split_into_new_factions"] = -100
        if len(faction.get("territory", [])) < (
            ruleset.splinter.minimum_parent_remaining_territory
            + ruleset.splinter.minimum_splinter_territory
        ):
            scores["split_into_new_factions"] = -100
        splinter_count = sum(
            1
            for item in world.factions
            if isinstance(item, dict)
            and str(item.get("id", "")).startswith(f"{faction_id}:splinter:")
        )
        if splinter_count >= ruleset.splinter.max_splinters_per_parent:
            scores["split_into_new_factions"] = -100
        last_splinter_day = faction.get("last_splinter_day")
        if (
            isinstance(last_splinter_day, int)
            and world.currentDay - last_splinter_day
            < ruleset.splinter.cooldown_days
        ):
            scores["split_into_new_factions"] = -100

        return scores

    def _apply_faction_action(self, world: WorldState, faction: dict[str, Any], action: str, hours: int, event_queue: WorldEventQueue | None = None) -> None:
        resources = faction.setdefault("resources", {})
        food = resources.get("food", 0)
        water = resources.get("water", 0)
        military = faction.get("militaryStrength", 1)
        population = faction.get("population", 1)
        territory = faction.setdefault("territory", [])
        ideology = faction.get("ideology", "survival")
        seed = self._stable_seed(
            f"{world.currentDay}:{faction.get('id', 'faction')}:{hours}:{action}",
            self.seed,
        )

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
            rules = load_world_content().faction_rules
            splinter_rules = rules.splinter
            parent_id = str(faction.get("id", "faction"))
            splinter_ids = sorted(
                str(item.get("id"))
                for item in world.factions
                if isinstance(item, dict)
                and str(item.get("id", "")).startswith(f"{parent_id}:splinter:")
            )
            parent_last_splinter_day = faction.get("last_splinter_day")
            can_splinter = (
                len(world.factions) < rules.max_factions
                and population >= splinter_rules.minimum_parent_population
                and len(splinter_ids) < splinter_rules.max_splinters_per_parent
                and (
                    not isinstance(parent_last_splinter_day, int)
                    or world.currentDay - parent_last_splinter_day
                    >= splinter_rules.cooldown_days
                )
                and len(territory) >= (
                    splinter_rules.minimum_parent_remaining_territory
                    + splinter_rules.minimum_splinter_territory
                )
            )
            if can_splinter:
                splinter_number = len(splinter_ids) + 1
                splinter_id = f"{parent_id}:splinter:{world.currentDay}:{splinter_number}"
                territory_count = len(territory)
                splinter_territory_count = min(
                    max(
                        splinter_rules.minimum_splinter_territory,
                        territory_count // 2,
                    ),
                    territory_count
                    - splinter_rules.minimum_parent_remaining_territory,
                )
                splinter_territory = sorted(
                    territory,
                    key=lambda item: str(item),
                )[:splinter_territory_count]
                parent_territory = sorted(
                    set(str(item) for item in territory)
                    - set(splinter_territory)
                )
                splinter_population = min(
                    max(
                        splinter_rules.minimum_splinter_population,
                        population // 2,
                    ),
                    population
                    - splinter_rules.minimum_parent_remaining_population,
                )
                remaining_population = population - splinter_population
                faction["territory"] = parent_territory
                faction["population"] = remaining_population
                faction["last_splinter_day"] = world.currentDay
                new_faction = {
                    "id": splinter_id,
                    "name": f"{faction.get('name', 'Faction')} Splinter {splinter_number}",
                    "population": splinter_population,
                    "population_last_derived": splinter_population,
                    "roaming_population": 0,
                    "militaryStrength": max(1, military // 2),
                    "resources": {
                        key: max(0, value // 2)
                        for key, value in resources.items()
                    },
                    "territory": splinter_territory,
                    "ideology": ideology,
                    "relationships": {},
                    "goals": list(faction.get("goals", [])),
                    "parentFactionId": parent_id,
                    "foundedDay": world.currentDay,
                }
                world.factions.append(new_faction)
                summary = (
                    f"{faction.get('name', 'Faction')} formed "
                    f"{new_faction['name']}."
                )
            else:
                summary = (
                    f"{faction.get('name', 'Faction')} did not splinter "
                    "because its faction limits or division rules blocked it."
                )
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
