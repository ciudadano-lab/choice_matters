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
from encounter_resolution import (
    apply_pending_encounter,
    get_available_tactical_options,
    get_default_tactical_option_id,
    resolve_pending_encounter,
)
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

    def to_list(self) -> list[dict[str, Any]]:
        return [
            {
                "day": event.day,
                "summary": event.summary,
                "event_type": event.event_type,
                "payload": copy.deepcopy(event.payload),
            }
            for event in self._events
        ]

    def replace_events(self, events: list[dict[str, Any]]) -> None:
        self._events = [
            WorldEvent(
                day=int(event["day"]),
                summary=str(event["summary"]),
                event_type=str(event["event_type"]),
                payload=copy.deepcopy(event.get("payload", {})),
            )
            for event in events
        ]
        self._events.sort(key=lambda item: (item.day, item.summary))


@dataclass
class PendingEncounter:
    player_id: str
    action_id: str
    action_type: str
    target: str
    parameters: dict[str, Any]
    day: int
    time: str
    seed: int
    plan_quality: float
    encounter_step: str
    step_index: int
    roll_log: list[dict[str, Any]]
    is_ambient: bool
    outcome_tier: str
    record_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "playerId": self.player_id,
            "actionId": self.action_id,
            "actionType": self.action_type,
            "target": self.target,
            "parameters": copy.deepcopy(self.parameters),
            "day": self.day,
            "time": self.time,
            "seed": self.seed,
            "planQuality": self.plan_quality,
            "encounterStep": self.encounter_step,
            "stepIndex": self.step_index,
            "rollLog": copy.deepcopy(self.roll_log),
            "isAmbient": self.is_ambient,
            "outcomeTier": self.outcome_tier,
            "recordIndex": self.record_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingEncounter:
        return cls(
            player_id=str(data["playerId"]),
            action_id=str(data["actionId"]),
            action_type=str(data["actionType"]),
            target=str(data.get("target", "")),
            parameters=copy.deepcopy(data.get("parameters", {})),
            day=int(data["day"]),
            time=str(data["time"]),
            seed=int(data["seed"]),
            plan_quality=float(data.get("planQuality", 0.0)),
            encounter_step=str(data["encounterStep"]),
            step_index=int(data["stepIndex"]),
            roll_log=copy.deepcopy(data.get("rollLog", [])),
            is_ambient=bool(data.get("isAmbient", False)),
            outcome_tier=str(data.get("outcomeTier", "success")),
            record_index=int(data["recordIndex"]),
        )


@dataclass
class PausedRun:
    run_id: str
    day: int
    slot: str
    resume_cursor: str
    pending_encounters: list[PendingEncounter]
    period_days: int
    remaining_days: int
    days_resolved: int
    decisions_collected: dict[str, Any]
    version: int
    per_day_results: list[dict[str, Any]]
    current_day_results: list[dict[str, Any]]
    current_slot_results: list[dict[str, Any]]
    period_summary_inputs: list[dict[str, Any]]
    queued_events: list[dict[str, Any]]
    prior_active_events: list[str]
    prior_internal_events: list[str]
    daily_state_hashes: list[str]
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "day": self.day,
            "slot": self.slot,
            "resumeCursor": self.resume_cursor,
            "pendingEncounters": [
                item.to_dict() for item in self.pending_encounters
            ],
            "periodDays": self.period_days,
            "remainingDays": self.remaining_days,
            "daysResolved": self.days_resolved,
            "decisionsCollected": copy.deepcopy(self.decisions_collected),
            "version": self.version,
            "perDayResults": copy.deepcopy(self.per_day_results),
            "currentDayResults": copy.deepcopy(self.current_day_results),
            "currentSlotResults": copy.deepcopy(self.current_slot_results),
            "periodSummaryInputs": copy.deepcopy(self.period_summary_inputs),
            "queuedEvents": copy.deepcopy(self.queued_events),
            "priorActiveEvents": list(self.prior_active_events),
            "priorInternalEvents": list(self.prior_internal_events),
            "dailyStateHashes": list(self.daily_state_hashes),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PausedRun:
        return cls(
            run_id=str(data["runId"]),
            day=int(data["day"]),
            slot=str(data["slot"]),
            resume_cursor=str(data["resumeCursor"]),
            pending_encounters=[
                PendingEncounter.from_dict(item)
                for item in data.get("pendingEncounters", [])
            ],
            period_days=int(data["periodDays"]),
            remaining_days=int(data["remainingDays"]),
            days_resolved=int(data["daysResolved"]),
            decisions_collected=copy.deepcopy(data.get("decisionsCollected", {})),
            version=int(data.get("version", 1)),
            per_day_results=copy.deepcopy(data.get("perDayResults", [])),
            current_day_results=copy.deepcopy(data.get("currentDayResults", [])),
            current_slot_results=copy.deepcopy(data.get("currentSlotResults", [])),
            period_summary_inputs=copy.deepcopy(data.get("periodSummaryInputs", [])),
            queued_events=copy.deepcopy(data.get("queuedEvents", [])),
            prior_active_events=list(data.get("priorActiveEvents", [])),
            prior_internal_events=list(data.get("priorInternalEvents", [])),
            daily_state_hashes=list(data.get("dailyStateHashes", [])),
            seed=int(data["seed"]),
        )


def _merge_pause_decisions(
    collected: dict[str, Any],
    submitted: dict[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(collected)
    for player_id, choice in submitted.items():
        key = str(player_id)
        if isinstance(choice, dict):
            current = merged.get(key)
            if not isinstance(current, dict):
                current = {}
            current.update(copy.deepcopy(choice))
            merged[key] = current
        else:
            merged[key] = copy.deepcopy(choice)
    return merged


def _choice_for_pending(
    choices: dict[str, Any],
    encounter: PendingEncounter,
) -> str | None:
    choice = choices.get(encounter.player_id)
    if isinstance(choice, dict):
        choice = choice.get("ambient" if encounter.is_ambient else "primary")
    return choice if isinstance(choice, str) else None


class WorldTick:
    def __init__(
        self,
        seed: int = 7,
        *,
        tactical_option_policy: str | None = None,
    ) -> None:
        self.seed = seed
        self.tactical_option_policy = tactical_option_policy
        self.last_period_outcome: dict[str, Any] = {}
        self.last_policy_day_outcomes: list[dict[str, Any]] = []
        self.last_period_state_hashes: list[str] = []
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
        world, _ = self._run_period(
            world_state,
            event_queue,
            period_days=1,
            interactive=False,
            room_state=room_state,
            create_period_summary=False,
        )
        return world

    def advance_period_until_interrupt(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        *,
        hours: int = 24,
        room_state: dict[str, Any] | None = None,
    ) -> tuple[WorldState, PausedRun | None]:
        return self._run_period(
            world_state,
            event_queue,
            period_days=max(1, hours // 24),
            interactive=True,
            room_state=room_state,
            create_period_summary=True,
        )

    def resume_period(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        paused_run: PausedRun | dict[str, Any],
        choices: dict[str, Any] | None = None,
        *,
        room_state: dict[str, Any] | None = None,
    ) -> tuple[WorldState, PausedRun | None]:
        """Resume a pause using per-player choices.

        A string choice keyed by player id applies to both primary and ambient
        encounters. Use {player_id: {"primary": ..., "ambient": ...}} to
        choose separately for each pending item.
        """
        paused = (
            PausedRun.from_dict(paused_run)
            if isinstance(paused_run, dict)
            else PausedRun.from_dict(paused_run.to_dict())
        )
        return self._run_period(
            world_state,
            event_queue,
            period_days=paused.period_days,
            interactive=True,
            room_state=room_state,
            create_period_summary=True,
            paused_run=paused,
            choices=choices or {},
        )

    def _run_period(
        self,
        world_state: WorldState | dict[str, Any],
        event_queue: WorldEventQueue,
        *,
        period_days: int,
        interactive: bool,
        room_state: dict[str, Any] | None,
        create_period_summary: bool,
        paused_run: PausedRun | None = None,
        choices: dict[str, Any] | None = None,
    ) -> tuple[WorldState, PausedRun | None]:
        world = WorldState.from_dict(
            copy.deepcopy(
                world_state.to_dict()
                if hasattr(world_state, "to_dict")
                else world_state
            )
        )
        period_days = max(1, int(period_days))
        decisions = copy.deepcopy(choices or {})
        if paused_run is None:
            world.currentTime = TIME_SLOTS[0][1]
            days_resolved = 0
            per_day_results: list[dict[str, Any]] = []
            current_day_results: list[dict[str, Any]] = []
            period_summary_inputs: list[dict[str, Any]] = []
            daily_state_hashes: list[str] = []
            version = 1
            run_id = self._make_run_id(world, period_days)
            prior_active_events = self._event_signatures(world.activeEvents)
            prior_internal_events = self._event_signatures(world.internalEvents)
            current_slot_results: list[dict[str, Any]] = []
        else:
            if paused_run.seed != self.seed:
                raise ValueError("PausedRun seed does not match this WorldTick seed")
            if paused_run.period_days != period_days:
                raise ValueError("PausedRun period length does not match the resume request")
            if world.currentDay != paused_run.day:
                raise ValueError("World day does not match the paused run")
            if self.slot_name_for_time(world.currentTime) != paused_run.slot:
                raise ValueError("World slot does not match the paused run")
            if paused_run.resume_cursor != self._next_slot_name(paused_run.slot):
                raise ValueError("PausedRun resume cursor does not follow its paused slot")
            world.currentDay = paused_run.day
            world.currentTime = self.slot_time_for_name(paused_run.slot)
            event_queue.replace_events(paused_run.queued_events)
            days_resolved = paused_run.days_resolved
            per_day_results = copy.deepcopy(paused_run.per_day_results)
            current_day_results = copy.deepcopy(paused_run.current_day_results)
            current_slot_results = copy.deepcopy(paused_run.current_slot_results)
            period_summary_inputs = copy.deepcopy(paused_run.period_summary_inputs)
            daily_state_hashes = list(paused_run.daily_state_hashes)
            prior_active_events = set(paused_run.prior_active_events)
            prior_internal_events = set(paused_run.prior_internal_events)
            version = paused_run.version + 1
            run_id = paused_run.run_id
            decisions = _merge_pause_decisions(
                paused_run.decisions_collected,
                decisions,
            )
            world = self._resolve_pending_encounters(
                world,
                paused_run.pending_encounters,
                current_slot_results,
                decisions,
                use_bot_policy=False,
            )
            decisions = {}
            current_day_results.extend(current_slot_results)
            current_slot_results = []
            old_day = world.currentDay
            world = self.advance_slot(
                world,
                event_queue,
                run_autopilot=False,
                room_state=room_state,
            )
            if world.currentDay > old_day:
                self._finish_policy_day(
                    world,
                    old_day,
                    current_day_results,
                    per_day_results,
                    period_summary_inputs,
                    room_state,
                    prior_active_events,
                    prior_internal_events,
                )
                days_resolved += 1
                daily_state_hashes.append(self._world_state_hash(world))
                current_day_results = []
                prior_active_events = self._event_signatures(world.activeEvents)
                prior_internal_events = self._event_signatures(world.internalEvents)

        self.last_policy_day_outcomes = []
        self.last_period_state_hashes = list(daily_state_hashes)
        if days_resolved:
            self.last_policy_day_outcomes = copy.deepcopy(
                per_day_results[-1]["results"]
            )

        while days_resolved < period_days:
            slot_name = self.slot_name_for_time(world.currentTime)
            world, slot_records, pending = self._process_policy_slot(
                world,
                slot_name,
                room_state,
            )
            if interactive and pending:
                self.last_policy_day_outcomes = copy.deepcopy(
                    current_day_results + slot_records
                )
                return world, PausedRun(
                    run_id=run_id,
                    day=world.currentDay,
                    slot=slot_name,
                    resume_cursor=self._next_slot_name(slot_name),
                    pending_encounters=pending,
                    period_days=period_days,
                    remaining_days=period_days - days_resolved,
                    days_resolved=days_resolved,
                    decisions_collected=decisions,
                    version=version,
                    per_day_results=per_day_results,
                    current_day_results=current_day_results,
                    current_slot_results=slot_records,
                    period_summary_inputs=period_summary_inputs,
                    queued_events=event_queue.to_list(),
                    prior_active_events=sorted(prior_active_events),
                    prior_internal_events=sorted(prior_internal_events),
                    daily_state_hashes=daily_state_hashes,
                    seed=self.seed,
                )

            world = self._resolve_pending_encounters(
                world,
                pending,
                slot_records,
                decisions,
                use_bot_policy=not interactive,
            )
            current_day_results.extend(slot_records)
            old_day = world.currentDay
            world = self.advance_slot(
                world,
                event_queue,
                run_autopilot=False,
                room_state=room_state,
            )
            if world.currentDay > old_day:
                self._finish_policy_day(
                    world,
                    old_day,
                    current_day_results,
                    per_day_results,
                    period_summary_inputs,
                    room_state,
                    prior_active_events,
                    prior_internal_events,
                )
                self.last_policy_day_outcomes = copy.deepcopy(current_day_results)
                days_resolved += 1
                daily_state_hashes.append(self._world_state_hash(world))
                self.last_period_state_hashes = list(daily_state_hashes)
                current_day_results = []
                prior_active_events = self._event_signatures(world.activeEvents)
                prior_internal_events = self._event_signatures(world.internalEvents)

        if create_period_summary:
            if period_summary_inputs:
                self.last_period_outcome = self._roll_up_period(
                    period_summary_inputs,
                    period_days,
                )
                world.activeEvents.append({
                    "type": "strategic_period_outcome",
                    "day": world.currentDay,
                    "summary": (
                        f"Strategic period resolved at "
                        f"{self.last_period_outcome['outcomeTier']}."
                    ),
                    "payload": copy.deepcopy(self.last_period_outcome),
                })
            else:
                self.last_period_outcome = {}
        return world, None

    @staticmethod
    def _world_state_hash(world: WorldState) -> str:
        serialized = json.dumps(
            world.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_signatures(events: list[Any]) -> set[str]:
        return {
            WorldTick._thread_event_signature(event)
            for event in events
            if isinstance(event, dict)
        }

    @staticmethod
    def _next_slot_name(slot_name: str) -> str:
        names = [name for name, _ in TIME_SLOTS]
        index = names.index(slot_name)
        return names[(index + 1) % len(names)]

    def _make_run_id(self, world: WorldState, period_days: int) -> str:
        serialized = json.dumps(
            world.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        digest = hashlib.sha256(
            f"{self.seed}:{period_days}:{serialized}".encode("utf-8")
        ).hexdigest()
        return digest[:24]

    def _finish_policy_day(
        self,
        world: WorldState,
        day: int,
        day_results: list[dict[str, Any]],
        per_day_results: list[dict[str, Any]],
        period_summary_inputs: list[dict[str, Any]],
        room_state: dict[str, Any] | None,
        prior_active_events: set[str],
        prior_internal_events: set[str],
    ) -> None:
        per_day_results.append({
            "day": day,
            "results": copy.deepcopy(day_results),
        })
        period_summary_inputs.extend(copy.deepcopy(day_results))
        self._sync_threads_for_new_world_events(
            world,
            room_state,
            prior_active_events,
            prior_internal_events,
        )

    def _process_policy_slot(
        self,
        world: WorldState,
        slot_name: str,
        room_state: dict[str, Any] | None,
    ) -> tuple[WorldState, list[dict[str, Any]], list[PendingEncounter]]:
        slot_time = self.slot_time_for_name(slot_name)
        world.currentTime = slot_time
        slot_day = world.currentDay
        engine = SimulationEngine(seed=self.seed)
        library = get_library()
        slot_records: list[dict[str, Any]] = []
        pending: list[PendingEncounter] = []
        player_ids = sorted(
            str(player.get("id"))
            for player in world.players
            if isinstance(player, dict) and player.get("id") is not None
        )

        for actor_id in player_ids:
            player = next(
                (
                    item for item in world.players
                    if isinstance(item, dict) and str(item.get("id")) == actor_id
                ),
                None,
            )
            if not isinstance(player, dict):
                continue
            policy = player.get("currentPolicy")
            if (
                isinstance(policy, dict)
                and self._plan_slot(policy) == slot_name
                and player.get("alive", True) is not False
                and player.get("health", 100) > 0
            ):
                status_before = len(player.get("statusEffects", []))
                result = engine.auto_resolve_policy_day(
                    world,
                    actor_id,
                    policy,
                    world.currentDay,
                )
                action = result.resolvedAction
                world = self._commit_policy_result(
                    world,
                    result,
                    action,
                    actor_id,
                    room_state,
                )
                record = self._record_slot_result(
                    world,
                    result,
                    actor_id,
                    slot_name,
                    slot_day,
                    slot_time,
                    status_before,
                )
                record_index = len(slot_records)
                slot_records.append(record)
                if result.encounterOccurred:
                    if action is None:
                        raise RuntimeError("Triggered encounter is missing its resolved action")
                    pending.append(self._pending_encounter(
                        result,
                        action,
                        actor_id,
                        slot_day,
                        slot_time,
                        False,
                        record_index,
                    ))

            if slot_name != "night" or library.ambient_night is None or not library.ambient_night.enabled:
                continue
            player = next(
                (
                    item for item in world.players
                    if isinstance(item, dict) and str(item.get("id")) == actor_id
                ),
                None,
            )
            if (
                not isinstance(player, dict)
                or player.get("alive", True) is False
                or player.get("health", 100) <= 0
                or not self._is_outdoors(world, player)
            ):
                continue
            location_id = resolve_location_id(world.to_dict(), player.get("location"))
            if location_id is None:
                continue
            status_before = len(player.get("statusEffects", []))
            ambient_action = StructuredAction(
                actionId=f"ambient-{actor_id}-{world.currentDay}-night",
                actionType="ambient_noise",
                actor=actor_id,
                target=location_id,
                parameters={
                    "seed": self.seed,
                    "chainTemplate": library.ambient_night.chain_template,
                },
                timeCost=0,
            )
            result = engine.apply(world, ambient_action, rules="current")
            world = self._commit_policy_result(
                world,
                result,
                ambient_action,
                actor_id,
                room_state,
            )
            record = self._record_slot_result(
                world,
                result,
                actor_id,
                slot_name,
                slot_day,
                slot_time,
                status_before,
            )
            record["isAmbient"] = True
            record_index = len(slot_records)
            slot_records.append(record)
            if result.encounterOccurred:
                pending.append(self._pending_encounter(
                    result,
                    ambient_action,
                    actor_id,
                    slot_day,
                    slot_time,
                    True,
                    record_index,
                ))
        return world, slot_records, pending

    def _commit_policy_result(
        self,
        world: WorldState,
        result: Any,
        action: StructuredAction | None,
        actor_id: str,
        room_state: dict[str, Any] | None,
    ) -> WorldState:
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
        if action is not None and action.actionType != "ambient_noise":
            _information_layer.record_action_discoveries(
                result.updatedWorldState,
                action_type=action.actionType,
                actor_id=actor_id,
                target=action.target,
                parameters=action.parameters,
                outcome_tier=result.outcomeTier,
            )
        world = WorldState.from_dict(result.updatedWorldState)
        if result.actionType != "ambient_noise":
            self._apply_tactical_side_effects(world, result, actor_id, room_state)
        return world

    def _pending_encounter(
        self,
        result: Any,
        action: StructuredAction,
        actor_id: str,
        day: int,
        time: str,
        is_ambient: bool,
        record_index: int,
    ) -> PendingEncounter:
        roll_log = [
            step.to_dict() if hasattr(step, "to_dict") else copy.deepcopy(step)
            for step in result.rollLog
        ]
        encounter_index = next(
            (
                index for index, step in enumerate(roll_log)
                if step.get("kind") == "encounter"
                and step.get("triggered") is True
            ),
            None,
        )
        if encounter_index is None:
            raise RuntimeError("Encounter result has no triggered encounter step")
        seed = action.parameters.get("seed", self.seed)
        try:
            seed = int(seed)
        except (TypeError, ValueError) as exc:
            raise ValueError("Encounter action seed must be an integer") from exc
        return PendingEncounter(
            player_id=actor_id,
            action_id=action.actionId,
            action_type=action.actionType,
            target=action.target,
            parameters=copy.deepcopy(action.parameters),
            day=day,
            time=time,
            seed=seed,
            plan_quality=float(result.planQuality),
            encounter_step=str(roll_log[encounter_index]["name"]),
            step_index=encounter_index,
            roll_log=roll_log,
            is_ambient=is_ambient,
            outcome_tier=str(result.outcomeTier),
            record_index=record_index,
        )

    def _resolve_pending_encounters(
        self,
        world: WorldState,
        pending: list[PendingEncounter],
        slot_records: list[dict[str, Any]],
        choices: dict[str, Any],
        *,
        use_bot_policy: bool,
    ) -> WorldState:
        for item in sorted(pending, key=lambda encounter: encounter.player_id):
            choice = _choice_for_pending(choices, item)
            if choice is None:
                choice = (
                    self._choose_tactical_option_for_bot(item, world)
                    if use_bot_policy
                    else get_default_tactical_option_id()
                )
            outcome, reason = resolve_pending_encounter(
                item.to_dict(),
                world,
                chosen_option=choice,
            )
            record = slot_records[item.record_index]
            if outcome is None:
                record["encounterOutcome"] = {
                    "skipped": True,
                    "reason": reason or "resolution_skipped",
                }
                record["encounterResolutionSkipped"] = reason or "resolution_skipped"
                continue
            updated_state, apply_reason = apply_pending_encounter(
                world.to_dict(),
                item.player_id,
                outcome,
            )
            if apply_reason is not None:
                record["encounterOutcome"] = {
                    "skipped": True,
                    "reason": apply_reason,
                }
                record["encounterResolutionSkipped"] = apply_reason
                continue
            world = WorldState.from_dict(updated_state)
            outcome_record = outcome.to_dict()
            outcome_record["slot"] = {
                "day": item.day,
                "name": self.slot_name_for_time(item.time),
            }
            record["encounterOutcome"] = outcome_record
            record["chosenOption"] = choice
            record["injuries"] += int(outcome.status_effect is not None)
            event = {
                "type": "encounter_resolution",
                "day": item.day,
                "slot": self.slot_name_for_time(item.time),
                "summary": f"Encounter resolved as {outcome.outcome}.",
                "payload": outcome.to_dict(),
            }
            world.activeEvents.append(copy.deepcopy(event))
        return world

    def _choose_tactical_option_for_bot(
        self,
        item: PendingEncounter,
        world: WorldState,
    ) -> str:
        available = get_available_tactical_options(item.to_dict(), world)
        option_ids = sorted(option["id"] for option in available)
        if not option_ids:
            return get_default_tactical_option_id()
        priorities = {
            "careful": ("hide", "flee", "barricade", "hold"),
            "forager": ("eat", "investigate", "hide", "hold"),
            "builder": ("barricade", "hide", "hold"),
            "diplomat": ("investigate", "hide", "hold"),
            "aggressive": ("ambush", "fight", "hold"),
            "idle": ("hold",),
        }
        if self.tactical_option_policy == "random":
            digest = hashlib.sha256(
                f"{item.seed}:{item.action_id}:{item.day}:{item.time}:tactical_bot".encode(
                    "utf-8"
                )
            ).digest()
            return option_ids[int.from_bytes(digest[:8], "big") % len(option_ids)]
        for option_id in priorities.get(self.tactical_option_policy or "", ()):
            if option_id in option_ids:
                return option_id
        return get_default_tactical_option_id()

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
        world, _ = self._run_period(
            world,
            event_queue,
            period_days=max(1, hours // 24),
            interactive=False,
            room_state=room_state,
            create_period_summary=True,
        )
        return world

    def resolve_encounter_after_action(
        self,
        state: WorldState | dict[str, Any],
        result: Any,
        action: Any,
        *,
        seed: int | None = None,
        chosen_option: str | None = None,
    ) -> Any:
        if not result.encounterOccurred:
            return result
        state_dict = state.to_dict() if hasattr(state, "to_dict") else state
        roll_log = [
            step.to_dict() if hasattr(step, "to_dict") else copy.deepcopy(step)
            for step in result.rollLog
        ]
        step_index = next(
            (
                index for index, step in enumerate(roll_log)
                if step.get("kind") == "encounter"
                and step.get("triggered") is True
            ),
            None,
        )
        if step_index is None:
            raise RuntimeError("Encounter result has no triggered encounter step")
        pending = {
            "playerId": str(action.actor),
            "actionId": str(action.actionId),
            "actionType": str(action.actionType),
            "target": str(action.target),
            "parameters": copy.deepcopy(action.parameters),
            "day": int(state_dict.get("currentDay", 1)),
            "time": str(state_dict.get("currentTime", "00:00")),
            "seed": self.seed if seed is None else int(seed),
            "planQuality": float(result.planQuality),
            "encounterStep": str(roll_log[step_index]["name"]),
            "stepIndex": step_index,
            "rollLog": roll_log,
        }
        resolution_state = (
            result.updatedWorldState
            if chosen_option is not None
            else state
        )
        outcome, reason = resolve_pending_encounter(
            pending,
            resolution_state,
            chosen_option=chosen_option,
        )
        if outcome is None:
            skip = {"skipped": True, "reason": reason or "resolution_skipped"}
            result.encounterOutcome = skip
            result.encounterDetails = {
                **(result.encounterDetails if isinstance(result.encounterDetails, dict) else {}),
                "resolution": skip,
            }
            event = {
                "type": "encounter_resolution_skipped",
                "day": pending["day"],
                "slot": self.slot_name_for_time(pending["time"]),
                "summary": f"Encounter resolution skipped: {skip['reason']}.",
            }
            result.worldEventsTriggered.append(event)
            updated_world = result.updatedWorldState.get(
                "world_state",
                result.updatedWorldState,
            )
            if isinstance(updated_world, dict):
                updated_world.setdefault("activeEvents", []).append(copy.deepcopy(event))
            return result

        updated_state, apply_reason = apply_pending_encounter(
            result.updatedWorldState,
            str(action.actor),
            outcome,
        )
        if apply_reason is not None:
            skip = {"skipped": True, "reason": apply_reason}
            result.encounterOutcome = skip
            result.encounterDetails = {
                **(result.encounterDetails if isinstance(result.encounterDetails, dict) else {}),
                "resolution": skip,
            }
            event = {
                "type": "encounter_resolution_skipped",
                "day": pending["day"],
                "slot": self.slot_name_for_time(pending["time"]),
                "summary": f"Encounter resolution skipped: {apply_reason}.",
            }
            result.worldEventsTriggered.append(event)
            updated_world = result.updatedWorldState.get(
                "world_state",
                result.updatedWorldState,
            )
            if isinstance(updated_world, dict):
                updated_world.setdefault("activeEvents", []).append(copy.deepcopy(event))
            return result
        result.updatedWorldState = updated_state
        slot = self.slot_name_for_time(pending["time"])
        outcome_record = outcome.to_dict()
        outcome_record["slot"] = {
            "day": pending["day"],
            "name": slot,
        }
        result.encounterOutcome = outcome_record
        result.encounterDetails = {
            **(result.encounterDetails if isinstance(result.encounterDetails, dict) else {}),
            "resolution": outcome_record,
        }
        event = {
            "type": "encounter_resolution",
            "day": pending["day"],
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
        faction_rules = load_world_content().faction_rules
        weekly_due = world.currentDay % faction_rules.decisions.interval_days == 0
        monthly_due = world.currentDay % faction_rules.stances.monthly_interval_days == 0
        if not weekly_due and not monthly_due:
            return

        factions = sorted(
            (
                faction for faction in world.factions
                if isinstance(faction, dict)
            ),
            key=lambda faction: str(faction.get("id", "")),
        )
        for faction in factions:
            self._normalize_faction(faction)
        if weekly_due:
            for faction in factions:
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
        if monthly_due:
            self._update_faction_stances(world, event_queue)

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
        faction.setdefault("stances", {})
        faction.setdefault("lastAggressionByFaction", {})

    def _derive_faction_stance(
        self,
        disposition: int,
        current_stance: str | None,
        recent_aggression: bool,
    ) -> str:
        rules = load_world_content().faction_rules.stances
        if disposition >= rules.allied_min:
            candidate = "allied"
        elif disposition >= rules.friendly_min:
            candidate = "friendly"
        elif disposition >= rules.neutral_min:
            candidate = "neutral"
        elif disposition >= rules.wary_min:
            candidate = "wary"
        elif disposition >= rules.hostile_min:
            candidate = "hostile"
        elif disposition <= rules.war_max and recent_aggression:
            candidate = "war"
        else:
            candidate = "hostile"

        stance_order = ("war", "hostile", "wary", "neutral", "friendly", "allied")
        if current_stance not in stance_order or current_stance == candidate:
            return candidate

        current_index = stance_order.index(current_stance)
        candidate_index = stance_order.index(candidate)
        if abs(candidate_index - current_index) == 1:
            boundaries = (
                rules.war_max,
                rules.wary_min,
                rules.neutral_min,
                rules.friendly_min,
                rules.allied_min,
            )
            boundary = boundaries[min(current_index, candidate_index)]
            margin = rules.hysteresis_margin
            if candidate_index > current_index and disposition < boundary + margin:
                candidate = current_stance
            elif candidate_index < current_index and disposition > boundary - margin:
                candidate = current_stance

        if candidate == "war" and not recent_aggression:
            return "hostile"
        return candidate

    @staticmethod
    def _has_recent_pair_aggression(
        first: dict[str, Any],
        second: dict[str, Any],
        *,
        current_day: int,
        window_days: int,
    ) -> bool:
        first_id = str(first.get("id", ""))
        second_id = str(second.get("id", ""))
        for faction, target_id in ((first, second_id), (second, first_id)):
            history = faction.get("lastAggressionByFaction", {})
            if not isinstance(history, dict):
                continue
            day = history.get(target_id)
            if isinstance(day, int) and 0 <= current_day - day <= window_days:
                return True
        return False

    def _update_faction_stances(
        self,
        world: WorldState,
        event_queue: WorldEventQueue | None = None,
    ) -> None:
        faction_rules = load_world_content().faction_rules
        stance_rules = faction_rules.stances
        factions = sorted(
            (item for item in world.factions if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        )
        for faction in factions:
            self._normalize_faction(faction)
        for index, first in enumerate(factions):
            first_id = str(first.get("id", ""))
            first_relationships = first.get("relationships", {})
            if not isinstance(first_relationships, dict):
                first_relationships = {}
                first["relationships"] = first_relationships
            first_stances = first.get("stances", {})
            if not isinstance(first_stances, dict):
                first_stances = {}
                first["stances"] = first_stances
            for second in factions[index + 1:]:
                second_id = str(second.get("id", ""))
                second_relationships = second.get("relationships", {})
                if not isinstance(second_relationships, dict):
                    second_relationships = {}
                    second["relationships"] = second_relationships
                second_stances = second.get("stances", {})
                if not isinstance(second_stances, dict):
                    second_stances = {}
                    second["stances"] = second_stances

                try:
                    first_disposition = max(
                        -100,
                        min(100, int(first_relationships.get(second_id, 0))),
                    )
                    second_disposition = max(
                        -100,
                        min(100, int(second_relationships.get(first_id, 0))),
                    )
                except (TypeError, ValueError):
                    first_disposition = second_disposition = 0
                pair_disposition = int((first_disposition + second_disposition) / 2)
                recent_aggression = self._has_recent_pair_aggression(
                    first,
                    second,
                    current_day=world.currentDay,
                    window_days=stance_rules.war_requires_aggression_days,
                )
                previous_first = first_stances.get(second_id)
                previous_second = second_stances.get(first_id)
                current_stance = (
                    previous_first
                    if previous_first == previous_second
                    else None
                )
                stance = self._derive_faction_stance(
                    pair_disposition,
                    current_stance,
                    recent_aggression,
                )
                if previous_first == stance and previous_second == stance:
                    continue

                first_stances[second_id] = stance
                second_stances[first_id] = stance
                first_name = str(first.get("name", first_id))
                second_name = str(second.get("name", second_id))
                summary = (
                    f"{first_name} and {second_name} shifted their relationship "
                    f"to {stance}."
                )
                reasons = [
                    "The monthly stance was derived from the pair's current dispositions."
                ]
                if stance == "war":
                    reasons.append(
                        "A recent attack or raid satisfied the configured war requirement."
                    )
                event = {
                    "type": "faction_stance_change",
                    "day": world.currentDay,
                    "actors": [first_id, second_id],
                    "factionIds": [first_id, second_id],
                    "previousStances": {
                        first_id: previous_first,
                        second_id: previous_second,
                    },
                    "stance": stance,
                    "dispositions": {
                        first_id: first_disposition,
                        second_id: second_disposition,
                    },
                    "reasons": reasons,
                }
                world.internalEvents.append(copy.deepcopy(event))
                if faction_rules.surface_faction_events_to_active_events:
                    world.activeEvents.append({
                        "type": "faction_action",
                        "action": "faction_stance_change",
                        "day": world.currentDay,
                        "factionId": first_id,
                        "target": {"factionId": second_id},
                        "summary": summary,
                    })
                if event_queue is not None:
                    event_queue.add(
                        WorldEvent(
                            day=world.currentDay,
                            summary=summary,
                            event_type="faction_stance_change",
                            payload=copy.deepcopy(event),
                        )
                    )

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

        if action in {"attack", "raid_settlements"} and target is not None:
            target_id = str(target.get("id", ""))
            faction_id = str(faction.get("id", ""))
            if target_id and target_id != faction_id:
                self._record_faction_aggression(
                    world,
                    faction_id,
                    target_id,
                )

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

    @staticmethod
    def _record_faction_aggression(
        world: WorldState,
        aggressor_id: str,
        target_id: str,
    ) -> None:
        for faction in world.factions:
            if not isinstance(faction, dict):
                continue
            faction_id = str(faction.get("id", ""))
            counterpart_id = (
                target_id if faction_id == aggressor_id else
                aggressor_id if faction_id == target_id else None
            )
            if counterpart_id is None:
                continue
            history = faction.get("lastAggressionByFaction", {})
            if not isinstance(history, dict):
                history = {}
                faction["lastAggressionByFaction"] = history
            history[counterpart_id] = world.currentDay

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
