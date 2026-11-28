from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import multiprocessing
import random
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from factor_library import (
    FactorLibrary,
    get_library,
    known_location_ids as factor_known_location_ids,
    owned_item_ids as factor_owned_item_ids,
)
from economy import get_starting_resources
from probability_config import CHANCE_MAX, CHANCE_MIN, TIME_SLOTS
from probability_engine import run_chain
from plan_features import PlanFeatures
from simulation_engine import StructuredAction
from world_state import WorldState, generate_initial_world_state
from world_tick import WorldEventQueue, WorldTick
from world_content import load_world_content


TARGET_MEAN_DAYS_BETWEEN_WORLD_PAUSES = (5.0, 10.0)
HURT_SERIOUS_TARGET = (0.25, 0.40)
PLANNED_HURT_SERIOUS_MAX_RATIO = 0.85
EARLY_DEATH_RATE_MAX = 0.05
ORIGINAL_BOT_NAMES = ("careful", "forager", "builder", "diplomat", "aggressive", "random")
PLAN_VARIANT_BOTS = ("careful_planned", "careful_sloppy")
BOT_NAMES = ORIGINAL_BOT_NAMES + PLAN_VARIANT_BOTS + ("idle",)
WORLD_SIM_SCENARIOS = ("baseline", "depleted_stores", "mild_shortage", "high_unrest")
FACTION_STANCES = ("allied", "friendly", "neutral", "wary", "hostile", "war")
WINDOW_DAYS = 10
PLAYER_STATS = ("health", "stamina", "stress", "hunger", "thirst")
DEATH_CAUSES = ("starvation", "dehydration", "combat/injury", "other")
CSV_FIELDS = (
    "seed",
    "bot",
    "K",
    "day",
    "slot",
    "slot_name",
    "action_type",
    "gate_chance",
    "encounter_chance",
    "gate_triggered",
    "interrupt",
    "health",
    "stamina",
    "stress",
    "hunger",
    "thirst",
)

_CAREFUL_POLICIES = (
    ("personal_development", "rest and recover, stealth"),
    ("resource_policy", "scout quietly, stealth"),
)
_RANDOM_POLICIES = (
    *_CAREFUL_POLICIES,
    ("resource_policy", "scavenge supplies"),
    ("infrastructure_investment", "build and fortify the base"),
    ("diplomatic_stance", "negotiate an alliance"),
    ("infrastructure_investment", "aggressive attack on rivals"),
)


def _world_state_value(world: WorldState | dict[str, Any], key: str) -> Any:
    return getattr(world, key) if isinstance(world, WorldState) else world.get(key)


def _remove_road_network(world: WorldState, lite: bool) -> None:
    if lite:
        world.cityLayout.pop("roadNetwork", None)


def _mark_lite_road_network(state: WorldState | dict[str, Any], lite: bool) -> None:
    if not lite:
        return
    # WorldState.from_dict rebuilds a missing network; the engine and tick do not read it.
    city_layout = state.cityLayout if isinstance(state, WorldState) else state.setdefault("cityLayout", {})
    city_layout["roadNetwork"] = {"version": 2}


def _is_alive(player: dict[str, Any]) -> bool:
    try:
        health = float(player.get("health", 100))
    except (TypeError, ValueError):
        health = 100.0
    return player.get("alive", True) is not False and health > 0


def _death_cause(
    before: dict[str, Any],
    after: dict[str, Any],
    result: Any | None = None,
) -> str:
    cause = after.get("deathCause")
    if cause in DEATH_CAUSES:
        return cause
    try:
        health_loss = float(before.get("health", 100)) > float(after.get("health", 100))
    except (TypeError, ValueError):
        health_loss = False
    if result is not None and (
        isinstance(getattr(result, "encounterOutcome", None), dict)
        or getattr(result, "outcomeTier", "success") in {
            "partial_success", "failure", "critical_failure",
        }
    ):
        return "combat/injury"
    if health_loss and _finite_value(after.get("hunger", 100)) <= 0:
        return "starvation"
    if health_loss and _finite_value(after.get("thirst", 100)) <= 0:
        return "dehydration"
    if health_loss and _status_effect_count(after) > _status_effect_count(before):
        return "combat/injury"
    return "other"


def _players_by_id(world: WorldState | dict[str, Any]) -> dict[str, dict[str, Any]]:
    players = _world_state_value(world, "players") or []
    return {
        str(player.get("id")): player
        for player in players
        if isinstance(player, dict) and player.get("id") is not None
    }


def _policy_for(
    bot: str,
    day_index: int,
    current_day: int,
    replan: int,
    random_source: random.Random,
    world: dict[str, Any],
    player: dict[str, Any],
) -> dict[str, Any]:
    if bot == "careful" or bot in PLAN_VARIANT_BOTS:
        action_type, focus = _CAREFUL_POLICIES[(day_index // replan) % len(_CAREFUL_POLICIES)]
    elif bot == "forager":
        action_type, focus = "resource_policy", "scavenge supplies"
    elif bot == "builder":
        if (day_index // replan) % 2:
            action_type, focus = "resource_policy", "search supplies"
        else:
            action_type, focus = "infrastructure_investment", "build and fortify the base"
    elif bot == "diplomat":
        action_type, focus = "diplomatic_stance", "negotiate an alliance"
    elif bot == "aggressive":
        action_type, focus = "infrastructure_investment", "aggressive attack on rivals"
    elif bot == "random":
        action_type, focus = random_source.choice(_RANDOM_POLICIES)
    elif bot == "idle":
        action_type, focus = "personal_development", "rest and recover"
    else:
        raise ValueError(f"Unknown bot {bot!r}")

    policy: dict[str, Any] = {
        "actionType": action_type,
        "focus": focus,
        "commitmentDays": replan,
        "startDay": current_day,
        "pivotalTiming": "culmination",
    }
    if bot in PLAN_VARIANT_BOTS:
        policy["planFeatures"] = _plan_features_for(bot, world, player)
    return policy


def _location_records(world: dict[str, Any] | WorldState) -> list[dict[str, Any]]:
    locations = _world_state_value(world, "locations") or []
    if isinstance(locations, dict):
        return [
            {"id": location_id, **location}
            for location_id, location in locations.items()
            if isinstance(location, dict)
        ]
    if isinstance(locations, list):
        return [location for location in locations if isinstance(location, dict)]
    return []


def _known_location_ids(
    world: dict[str, Any] | WorldState,
    player: dict[str, Any],
) -> list[str]:
    return sorted(factor_known_location_ids(world, player))


def _owned_item_ids(
    world: dict[str, Any] | WorldState,
    player: dict[str, Any],
) -> list[str]:
    return sorted(factor_owned_item_ids(world, player))


def _plan_features_for(
    bot: str,
    world: dict[str, Any] | WorldState,
    player: dict[str, Any],
) -> dict[str, Any]:
    if bot == "careful_planned":
        return {
            "targetLocationIds": _known_location_ids(world, player)[:5],
            "itemsPlanned": _owned_item_ids(world, player)[:6],
            "hasFallback": True,
            "countermeasures": ["alternate route", "scout first"],
            "approach": "stealth",
            "timing": "night",
        }
    if bot == "careful_sloppy":
        locations = _location_records(world)
        known_ids = set(_known_location_ids(world, player))
        target_id = next(
            (
                location["id"]
                for location in locations
                if isinstance(location.get("id"), str)
                and location.get("discovered") is False
                and location["id"] not in known_ids
            ),
            None,
        )
        if target_id is None:
            location_ids = {
                location.get("id")
                for location in locations
                if isinstance(location.get("id"), str)
            }
            target_id = "missing_plan_target"
            while target_id in location_ids:
                target_id += "_missing"
        owned_items = {item.casefold() for item in _owned_item_ids(world, player)}
        unowned_item = "rocket launcher"
        while unowned_item.casefold() in owned_items:
            unowned_item += " (unowned)"
        return {
            "targetLocationIds": [target_id],
            "itemsPlanned": [unowned_item],
            "hasFallback": False,
            "countermeasures": [],
            "approach": "force",
            "timing": "night",
        }
    raise ValueError(f"Unknown plan-feature bot {bot!r}")


def _known_log_sources(library: FactorLibrary) -> set[str]:
    return {
        factor.log_source or factor.id
        for factor in library.factors
    }


def _validate_result(
    result: Any,
    *,
    seed: int,
    day: int,
    bot: str,
    library: FactorLibrary,
) -> None:
    context = f"seed={seed}, day={day}, bot={bot}"
    known_sources = _known_log_sources(library)
    roll_log = result.get("rollLog", []) if isinstance(result, dict) else result.rollLog
    for step in roll_log:
        chance = step.get("chance")
        if not isinstance(chance, (int, float)) or not math.isfinite(chance) or not CHANCE_MIN <= chance <= CHANCE_MAX:
            raise AssertionError(f"Invalid chance {chance!r} in rollLog ({context})")
        for field in ("modifiers", "counters"):
            for entry in step.get(field, []):
                source = entry.get("source")
                if source not in {"plan", "plan_cap"} and source not in known_sources:
                    raise AssertionError(
                        f"Unknown rollLog factor source {source!r} ({context})"
                    )


def _status_effect_count(player: dict[str, Any]) -> int:
    effects = player.get("statusEffects", [])
    return len(effects) if isinstance(effects, list) else 0


def _finite_player_stats(
    world: WorldState | dict[str, Any],
    *,
    seed: int,
    day: int,
    bot: str,
) -> None:
    for player in _world_state_value(world, "players") or []:
        for stat in PLAYER_STATS:
            value = player.get(stat, 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise AssertionError(
                    f"Non-finite player {stat}={value!r} (seed={seed}, day={day}, bot={bot})"
                )


def _run_game(payload: dict[str, Any]) -> dict[str, Any]:
    seed = int(payload["seed"])
    bot = str(payload["bot"])
    player_count = int(payload["players"])
    days = int(payload["days"])
    replan = int(payload["replan"])
    lite = bool(payload["lite"])
    if bot not in BOT_NAMES:
        raise ValueError(f"Unknown bot {bot!r}")
    if not 1 <= player_count <= 3:
        raise ValueError(f"Player count must be between 1 and 3, got {player_count}")
    if days <= 0 or replan <= 0:
        raise ValueError("days and replan must be positive")

    world = generate_initial_world_state([], seed).to_dict()
    world["players"] = world["players"][:player_count]
    world["resources"] = get_starting_resources(player_count)
    if lite:
        world.get("cityLayout", {}).pop("roadNetwork", None)

    tick = WorldTick(seed=seed)
    library = get_library()
    random_source = random.Random(seed)
    player_ids = [str(player["id"]) for player in world["players"]]
    already_dead = {
        player_id
        for player_id, player in _players_by_id(world).items()
        if not _is_alive(player)
    }
    game_days: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    window_snapshots: list[dict[str, Any]] = []

    for day_index in range(days):
        simulated_day = day_index + 1
        day_start = int(_world_state_value(world, "currentDay"))
        players_before_tick = copy.deepcopy(_players_by_id(world))
        for player_id, player in _players_by_id(world).items():
            if not _is_alive(player):
                continue
            if day_index % replan == 0:
                player["currentPolicy"] = _policy_for(
                    bot,
                    day_index,
                    day_start,
                    replan,
                    random_source,
                    world,
                    player,
                )
            if not isinstance(player.get("currentPolicy"), dict):
                raise AssertionError(
                    f"Missing policy (seed={seed}, day={simulated_day}, bot={bot})"
                )

        _mark_lite_road_network(world, lite)
        world = tick.advance_policy_day(world, WorldEventQueue())
        _remove_road_network(world, lite)
        records = tick.last_policy_day_outcomes
        if world.currentDay != day_start + 1 or world.currentTime != "06:00":
            raise AssertionError(
                f"Policy day ended at {world.currentDay} {world.currentTime}; "
                f"expected {day_start + 1} 06:00 (seed={seed}, day={simulated_day}, bot={bot})"
            )

        day_pause_records = [
            record["pauseRecord"]
            for record in records
            if isinstance(record.get("pauseRecord"), dict)
        ]
        pause_slots = sorted({record["slot"] for record in day_pause_records})
        day_gate_count = 0
        day_gate_triggers = 0
        day_near_misses = 0
        day_encounters = 0
        day_injuries = sum(record.get("injuries", 0) for record in records)
        day_encounter_injuries = 0
        day_encounter_outcomes = {
            "evaded": 0,
            "close_call": 0,
            "hurt": 0,
            "serious": 0,
        }
        for record in records:
            _validate_result(
                record,
                seed=seed,
                day=simulated_day,
                bot=bot,
                library=library,
            )
            gates = [
                step for step in record["rollLog"]
                if step.get("kind") == "gate"
            ]
            encounters = [
                step for step in record["rollLog"]
                if step.get("kind") == "encounter"
            ]
            gate_triggered = any(step.get("triggered") is True for step in gates)
            encounter_triggered = any(step.get("triggered") is True for step in encounters)
            day_gate_count += len(gates)
            day_gate_triggers += int(gate_triggered)
            day_near_misses += int(gate_triggered and not encounter_triggered)
            day_encounters += int(encounter_triggered)
            outcome = record.get("encounterOutcome")
            if isinstance(outcome, dict):
                name = outcome.get("outcome")
                if name in day_encounter_outcomes:
                    day_encounter_outcomes[name] += 1
                    day_encounter_injuries += int(name in {"hurt", "serious"})

        day_death_causes = {cause: 0 for cause in DEATH_CAUSES}
        day_deaths = 0
        players_after_tick = _players_by_id(world)
        for player_id, player in players_after_tick.items():
            if player_id in already_dead or _is_alive(player):
                continue
            already_dead.add(player_id)
            day_deaths += 1
            day_death_causes[_death_cause(
                players_before_tick[player_id],
                player,
            )] += 1

        primary_records = {
            record["playerId"]: record
            for record in records
            if not record.get("isAmbient")
        }
        for slot, player_id in enumerate(player_ids, start=1):
            player_after_tick = players_after_tick[player_id]
            record = primary_records.get(player_id, {})
            gates = [
                step for step in record.get("rollLog", [])
                if step.get("kind") == "gate"
            ]
            encounters = [
                step for step in record.get("rollLog", [])
                if step.get("kind") == "encounter"
            ]
            daily_rows.append({
                "seed": seed,
                "bot": bot,
                "K": player_count,
                "day": simulated_day,
                "slot": slot,
                "slot_name": record.get("slot", ""),
                "action_type": record.get("actionType", ""),
                "gate_chance": gates[0]["chance"] if gates else 0.0,
                "encounter_chance": encounters[0]["chance"] if encounters else 0.0,
                "gate_triggered": any(step.get("triggered") is True for step in gates),
                "interrupt": record.get("interruptStep") is not None,
                **{
                    stat: player_after_tick.get(stat, 0)
                    for stat in PLAYER_STATS
                },
            })

        _finite_player_stats(world, seed=seed, day=simulated_day, bot=bot)
        game_days.append({
            "day": simulated_day,
            "pause": bool(pause_slots),
            "pause_slots": pause_slots,
            "pause_records": day_pause_records,
            "interrupts": len(day_pause_records),
            "active_players": len(primary_records),
            "gate_count": day_gate_count,
            "gate_triggers": day_gate_triggers,
            "near_misses": day_near_misses,
            "encounters": day_encounters,
            "encounter_injuries": day_encounter_injuries,
            "encounter_outcomes": day_encounter_outcomes,
            "injuries": day_injuries,
            "deaths": day_deaths,
            "death_causes": day_death_causes,
            "player_days": player_count,
            "food_zero_player_days": (
                player_count if world.resources.get("food", 0) <= 0 else 0
            ),
            "water_zero_player_days": (
                player_count if world.resources.get("water", 0) <= 0 else 0
            ),
            "resource_zero_player_days": (
                player_count
                if world.resources.get("food", 0) <= 0 or world.resources.get("water", 0) <= 0
                else 0
            ),
        })

        if simulated_day % WINDOW_DAYS == 0 or simulated_day == days:
            players_after_tick = list(players_after_tick.values())
            window_snapshots.append({
                "window": (simulated_day - 1) // WINDOW_DAYS + 1,
                "end_day": simulated_day,
                "health": _mean(player.get("health", 0) for player in players_after_tick),
                "stamina": _mean(player.get("stamina", 0) for player in players_after_tick),
                "stress": _mean(player.get("stress", 0) for player in players_after_tick),
                "hunger": _mean(player.get("hunger", 0) for player in players_after_tick),
                "thirst": _mean(player.get("thirst", 0) for player in players_after_tick),
                "food": _finite_value(world.resources.get("food", 0)),
                "water": _finite_value(world.resources.get("water", 0)),
            })

    return {
        "seed": seed,
        "bot": bot,
        "players": player_count,
        "days": game_days,
        "windows": window_snapshots,
        "per_day": daily_rows,
    }


def _finite_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _mean(values: Any) -> float:
    numbers = [_finite_value(value) for value in values]
    return statistics.fmean(numbers) if numbers else 0.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _pause_gaps(days: list[dict[str, Any]], start_day: int, end_day: int) -> list[int]:
    pause_days = [
        item["day"]
        for item in days
        if start_day <= item["day"] <= end_day and item["pause"]
    ]
    gaps: list[int] = []
    previous = start_day - 1
    for pause_day in pause_days:
        gaps.append(pause_day - previous)
        previous = pause_day
    return gaps


def _aggregate(
    game_results: list[dict[str, Any]],
    *,
    games: int,
    days: int,
    players: list[int],
    bots: list[str],
    replan: int,
    lite: bool,
) -> dict[str, Any]:
    ordered_results = sorted(
        game_results,
        key=lambda item: (item["bot"], item["players"], item["seed"]),
    )
    groups = []
    for bot in bots:
        for player_count in players:
            group_games = [
                game for game in ordered_results
                if game["bot"] == bot and game["players"] == player_count
            ]
            windows = []
            for window_number, start_day in enumerate(range(1, days + 1, WINDOW_DAYS), start=1):
                end_day = min(days, start_day + WINDOW_DAYS - 1)
                grouped_days = [
                    day
                    for game in group_games
                    for day in game["days"]
                    if start_day <= day["day"] <= end_day
                ]
                day_count = len(group_games) * (end_day - start_day + 1)
                world_pause_days = sum(bool(day["pause"]) for day in grouped_days)
                gate_count = sum(day["gate_count"] for day in grouped_days)
                gate_triggers = sum(day["gate_triggers"] for day in grouped_days)
                near_misses = sum(day["near_misses"] for day in grouped_days)
                gaps = [
                    gap
                    for game in group_games
                    for gap in _pause_gaps(game["days"], start_day, end_day)
                ]
                zero_pause_games = sum(
                    not any(
                        day["pause"]
                        for day in game["days"]
                        if start_day <= day["day"] <= end_day
                    )
                    for game in group_games
                )
                snapshots = [
                    snapshot
                    for game in group_games
                    for snapshot in game["windows"]
                    if snapshot["window"] == window_number
                ]
                encounter_outcome_counts = {
                    outcome: sum(
                        day.get("encounter_outcomes", {}).get(outcome, 0)
                        for day in grouped_days
                    )
                    for outcome in ("evaded", "close_call", "hurt", "serious")
                }
                window_encounters = sum(day["encounters"] for day in grouped_days)
                window_encounter_injuries = sum(
                    day.get("encounter_injuries", 0)
                    for day in grouped_days
                )
                slot_pause_rates = {
                    slot_name: (
                        sum(slot_name in day.get("pause_slots", []) for day in grouped_days)
                        / day_count
                        if day_count else 0.0
                    )
                    for slot_name, _ in TIME_SLOTS
                }
                windows.append({
                    "window": window_number,
                    "start_day": start_day,
                    "end_day": end_day,
                    "per_player_interrupt_rate": (
                        sum(day["interrupts"] for day in grouped_days)
                        / (day_count * player_count)
                        if day_count else 0.0
                    ),
                    "world_pause_rate": world_pause_days / day_count if day_count else 0.0,
                    "pause_rate_by_slot": slot_pause_rates,
                    "days_between_world_pauses": {
                        "mean": statistics.fmean(gaps) if gaps else None,
                        "median": statistics.median(gaps) if gaps else None,
                        "p10": _percentile(gaps, 0.1),
                        "p90": _percentile(gaps, 0.9),
                    },
                    "zero_pause_games_percent": (
                        100.0 * zero_pause_games / len(group_games) if group_games else 0.0
                    ),
                    "gate_trigger_rate": gate_triggers / gate_count if gate_count else 0.0,
                    "near_miss_rate": near_misses / gate_triggers if gate_triggers else 0.0,
                    "encounters": window_encounters,
                    "encounter_outcome_counts": encounter_outcome_counts,
                    "encounter_injuries": window_encounter_injuries,
                    "injuries_per_encounter": (
                        window_encounter_injuries / window_encounters
                        if window_encounters else 0.0
                    ),
                    "injuries": sum(day["injuries"] for day in grouped_days),
                    "deaths": sum(day["deaths"] for day in grouped_days),
                    "mean_end_stats": {
                        stat: _mean(snapshot[stat] for snapshot in snapshots)
                        for stat in (*PLAYER_STATS, "food", "water")
                    },
                    "games": len(group_games),
                })

            target_days = [
                day for game in group_games for day in game["days"]
                if day["day"] <= min(20, days)
            ]
            measured_target_days = min(20, days)
            target_gaps = [
                gap
                for game in group_games
                for gap in _pause_gaps(game["days"], 1, min(20, days))
            ]
            zero_pause_target_games = sum(
                not any(day["pause"] for day in game["days"] if day["day"] <= min(20, days))
                for game in group_games
            )
            target_mean = statistics.fmean(target_gaps) if target_gaps else None
            all_days = [day for game in group_games for day in game["days"]]
            encounter_outcome_counts = {
                outcome: sum(
                    day.get("encounter_outcomes", {}).get(outcome, 0)
                    for day in all_days
                )
                for outcome in ("evaded", "close_call", "hurt", "serious")
            }
            encounters_total = sum(day["encounters"] for day in all_days)
            encounter_injuries = sum(
                day.get("encounter_injuries", 0)
                for day in all_days
            )
            slot_pause_rates = {
                slot_name: (
                    sum(slot_name in day.get("pause_slots", []) for day in all_days)
                    / (len(group_games) * days)
                    if group_games and days else 0.0
                )
                for slot_name, _ in TIME_SLOTS
            }
            groups.append({
                "bot": bot,
                "players": player_count,
                "games": len(group_games),
                "encounters": encounters_total,
                "encounter_outcome_counts": encounter_outcome_counts,
                "encounter_injuries": encounter_injuries,
                "pause_rate_by_slot": slot_pause_rates,
                "injuries_per_encounter": (
                    encounter_injuries / encounters_total
                    if encounters_total else 0.0
                ),
                "deaths": sum(day["deaths"] for day in all_days),
                "death_causes": {
                    cause: sum(
                        day.get("death_causes", {}).get(cause, 0)
                        for day in all_days
                    )
                    for cause in DEATH_CAUSES
                },
                "player_days": sum(day.get("player_days", player_count) for day in all_days),
                "food_zero_player_days": sum(
                    day.get("food_zero_player_days", 0) for day in all_days
                ),
                "water_zero_player_days": sum(
                    day.get("water_zero_player_days", 0) for day in all_days
                ),
                "resource_zero_player_days": sum(
                    day.get("resource_zero_player_days", 0) for day in all_days
                ),
                "deaths_before_day_15": sum(
                    day["deaths"] for day in all_days if day["day"] < 15
                ),
                "target_days_1_20": {
                    "mean_days_between_world_pauses": target_mean,
                    "world_pause_rate": (
                        sum(bool(day["pause"]) for day in target_days)
                        / (len(group_games) * measured_target_days)
                        if group_games and measured_target_days else 0.0
                    ),
                    "zero_pause_games_percent": (
                        100.0 * zero_pause_target_games / len(group_games)
                        if group_games else 0.0
                    ),
                    "measured_days": measured_target_days,
                    "pause_days": sum(bool(day["pause"]) for day in target_days),
                },
                "windows": windows,
            })

    return {
        "configuration": {
            "games": games,
            "days": days,
            "players": players,
            "bots": bots,
            "replan": replan,
            "lite": lite,
            "window_days": WINDOW_DAYS,
        },
        "groups": groups,
    }


def run_play(
    *,
    games: int,
    days: int,
    players: list[int],
    bots: list[str],
    replan: int,
    workers: int = 1,
    lite: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if games <= 0 or days <= 0 or replan <= 0 or workers <= 0:
        raise ValueError("games, days, replan, and workers must be positive")
    if not players or any(player_count not in (1, 2, 3) for player_count in players):
        raise ValueError("players must contain values from 1 through 3")
    if not bots or any(bot not in BOT_NAMES for bot in bots):
        raise ValueError(f"bots must contain values from {', '.join(BOT_NAMES)}")

    payloads = [
        {
            "seed": seed,
            "bot": bot,
            "players": player_count,
            "days": days,
            "replan": replan,
            "lite": lite,
        }
        for bot in bots
        for player_count in players
        for seed in range(1, games + 1)
    ]
    if workers == 1:
        results = [_run_game(payload) for payload in payloads]
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            results = list(executor.map(_run_game, payloads))

    results.sort(key=lambda item: (item["bot"], item["players"], item["seed"]))
    report = _aggregate(
        results,
        games=games,
        days=days,
        players=players,
        bots=bots,
        replan=replan,
        lite=lite,
    )
    rows = [
        row
        for game in results
        for row in game["per_day"]
    ]
    rows.sort(key=lambda row: (row["bot"], row["K"], row["seed"], row["day"], row["slot"]))
    return report, rows


def _world_state_sha256(world: WorldState) -> str:
    canonical = json.dumps(
        world.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _at_bound(value: Any, lower: int, upper: int) -> bool:
    if isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return numeric <= lower or numeric >= upper


def _acceptance_snapshot(
    world: WorldState,
    *,
    elapsed_day: int,
    retention_caps: dict[str, int],
) -> dict[str, Any]:
    settlements = [
        {
            "id": str(settlement.get("id", settlement.get("location_id", ""))),
            "status": str(settlement.get("status", "unknown")),
            "population": int(settlement.get("population", 0)),
            "morale": settlement.get("morale"),
            "unrest": settlement.get("unrest"),
            "defense": settlement.get("defense"),
        }
        for settlement in world.settlements
        if isinstance(settlement, dict)
    ]
    stance_distribution = {stance: 0 for stance in FACTION_STANCES}
    for faction in world.factions:
        if not isinstance(faction, dict):
            continue
        stances = faction.get("stances", {})
        if not isinstance(stances, dict):
            continue
        for stance in stances.values():
            if stance in stance_distribution:
                stance_distribution[stance] += 1

    bound_observations = {
        "settlement_morale": {"at_bound": 0, "total": 0},
        "settlement_unrest": {"at_bound": 0, "total": 0},
        "settlement_defense": {"at_bound": 0, "total": 0},
        "faction_dispositions": {"at_bound": 0, "total": 0},
    }
    for settlement in world.settlements:
        if not isinstance(settlement, dict):
            continue
        for field, key in (
            ("morale", "settlement_morale"),
            ("unrest", "settlement_unrest"),
            ("defense", "settlement_defense"),
        ):
            value = settlement.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                counts = bound_observations[key]
                counts["total"] += 1
                counts["at_bound"] += int(_at_bound(value, 0, 100))

    for faction in world.factions:
        if not isinstance(faction, dict):
            continue
        for field in ("relationships", "player_dispositions"):
            dispositions = faction.get(field, {})
            if not isinstance(dispositions, dict):
                continue
            for value in dispositions.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    counts = bound_observations["faction_dispositions"]
                    counts["total"] += 1
                    counts["at_bound"] += int(_at_bound(value, -100, 100))

    current_day = world.currentDay

    def count_day_events(events: list[dict[str, Any]], *day_fields: str) -> int:
        total = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            event_day = next(
                (event[field] for field in day_fields if field in event),
                None,
            )
            if event_day == current_day or str(event_day) == str(current_day):
                total += 1
        return total

    active_count = count_day_events(world.activeEvents, "day", "timestamp")
    internal_count = count_day_events(world.internalEvents, "day", "timestamp")
    history_count = count_day_events(world.worldHistory, "day", "timestamp")
    retention_lengths = {
        "active_events": len(world.activeEvents),
        "world_history": len(world.worldHistory),
        "internal_events": len(world.internalEvents),
        "relationships": len(world.relationships),
    }
    return {
        "elapsed_day": elapsed_day,
        "settlements": settlements,
        "stance_distribution": stance_distribution,
        "bound_observations": bound_observations,
        "event_volume": active_count + internal_count + history_count,
        "event_log_counts": {
            "active_events": active_count,
            "internal_events": internal_count,
            "world_history": history_count,
        },
        "retention_lengths": retention_lengths,
        "retention_caps": dict(retention_caps),
        "state_hash": _world_state_sha256(world),
    }


def run_world_sim(
    *,
    days: int,
    seeds: int,
    scenario: str = "baseline",
    _acceptance_metrics: bool = False,
    _check_determinism: bool = True,
) -> dict[str, Any]:
    if days <= 0 or seeds <= 0:
        raise ValueError("days and seeds must be positive")
    if scenario not in WORLD_SIM_SCENARIOS:
        raise ValueError(f"Unknown world_sim scenario: {scenario}")

    runs: list[dict[str, Any]] = []
    for seed in range(1, seeds + 1):
        world = generate_initial_world_state([], seed)
        world.players = []
        faction_rules = load_world_content().faction_rules
        retention_caps = {
            "active_events": faction_rules.retention.active_events,
            "world_history": faction_rules.retention.world_history,
            "internal_events": faction_rules.retention.internal_events,
            "relationships": faction_rules.retention.relationships,
        }
        influence_factions = sorted(
            (item for item in world.factions if isinstance(item, dict)),
            key=lambda item: str(item.get("id", "")),
        )
        if influence_factions:
            influence_factions[0].setdefault("player_dispositions", {})[
                "player_1"
            ] = -faction_rules.max_player_disposition
        if len(influence_factions) > 1:
            influence_factions[1].setdefault("player_dispositions", {})[
                "player_1"
            ] = faction_rules.max_player_disposition
        if world.settlements:
            target_settlement = min(
                world.settlements,
                key=lambda item: str(item.get("id", "")),
            )
            target_settlement.setdefault("attention_by_player", {})[
                "player_1"
            ] = faction_rules.max_player_attention
        initial_dispositions = {
            str(source.get("id", "")): {
                str(target): int(score)
                for target, score in source.get("relationships", {}).items()
            }
            for source in world.factions
            if isinstance(source, dict)
        }
        if scenario == "depleted_stores":
            for settlement in world.settlements:
                settlement["stores"] = {
                    "food": 0,
                    "water": 0,
                    "building_materials": 0,
                    "medical_supplies": 0,
                }
                settlement["needs"] = ["food", "water", "building_materials", "medical_supplies"]
        elif scenario == "high_unrest":
            for settlement in world.settlements:
                settlement["unrest"] = 95
                settlement["morale"] = 10
        elif scenario == "mild_shortage":
            for index, settlement in enumerate(sorted(
                world.settlements,
                key=lambda item: str(item.get("id", "")),
            )):
                if index % 2 == 0:
                    settlement["stores"] = {
                        "food": 4,
                        "water": 4,
                        "building_materials": 1,
                        "medical_supplies": 1,
                    }
                else:
                    settlement["stores"] = {
                        "food": 18,
                        "water": 18,
                        "building_materials": 8,
                        "medical_supplies": 4,
                    }
        tick = WorldTick(seed=seed)
        daily: list[dict[str, Any]] = []
        action_totals: dict[str, int] = {}
        decision_influence_counts = {
            category: {"decisions": 0, "changed_decisions": 0}
            for category in (
                "hostile_disposition",
                "friendly_disposition",
                "attention_pressure",
            )
        }
        for elapsed_day in range(1, days + 1):
            world = tick.advance(
                world,
                WorldEventQueue(),
                hours=24,
                run_autopilot=False,
            )
            if world.currentDay % 7 == 0:
                for faction in world.factions:
                    if not isinstance(faction, dict):
                        continue
                    if faction.get("lastDecisionDay") == world.currentDay:
                        factors = faction.get("lastDecisionFactors", {})
                        influences = faction.get("lastDecisionInfluences", {})
                        for category, counts in decision_influence_counts.items():
                            if factors.get(category):
                                counts["decisions"] += 1
                            if influences.get(category):
                                counts["changed_decisions"] += 1
                    action = faction.get("lastAction")
                    if action is not None:
                        action_totals[str(action)] = action_totals.get(str(action), 0) + 1
            settlements = sorted(
                world.settlements,
                key=lambda settlement: str(settlement.get("id", "")),
            )
            status_distribution: dict[str, int] = {}
            for settlement in settlements:
                status = str(settlement.get("status", "unknown"))
                status_distribution[status] = status_distribution.get(status, 0) + 1
            resources = sorted({
                resource
                for settlement in settlements
                for resource in settlement.get("stores", {})
            })
            snapshot = {
                "day": world.currentDay,
                "faction_count": len(world.factions),
                "action_distribution": {
                    action: sum(
                        1
                        for faction in world.factions
                        if isinstance(faction, dict)
                        and faction.get("lastAction") == action
                    )
                    for action in sorted({
                        str(faction.get("lastAction"))
                        for faction in world.factions
                        if isinstance(faction, dict)
                        and faction.get("lastAction") is not None
                    })
                },
                "disposition_changed": any(
                    {
                        str(target): int(score)
                        for target, score in source.get("relationships", {}).items()
                    }
                    != initial_dispositions.get(str(source.get("id", "")), {})
                    for source in world.factions
                    if isinstance(source, dict)
                    and isinstance(source.get("relationships"), dict)
                ),
                "status_distribution": status_distribution,
                "population": sum(
                    int(settlement.get("population", 0))
                    for settlement in settlements
                ),
                "stores": {
                    resource: sum(
                        int(settlement.get("stores", {}).get(resource, 0))
                        for settlement in settlements
                    )
                    for resource in resources
                },
            }
            if _acceptance_metrics:
                snapshot.update(
                    _acceptance_snapshot(
                        world,
                        elapsed_day=elapsed_day,
                        retention_caps=retention_caps,
                    )
                )
            daily.append(snapshot)
        runs.append({
            "seed": seed,
            "daily": daily,
            "action_distribution": action_totals,
            "decision_influence": decision_influence_counts,
            "faction_count_final": len(world.factions),
        })

    final_days = [run["daily"][-1] for run in runs]
    all_statuses = sorted({
        status
        for snapshot in final_days
        for status in snapshot["status_distribution"]
    })
    faction_counts = {
        day: {
            "mean": _mean(
                next(
                    snapshot["faction_count"]
                    for snapshot in run["daily"]
                    if snapshot["day"] == day
                )
                for run in runs
            ),
            "min": min(
                next(
                    snapshot["faction_count"]
                    for snapshot in run["daily"]
                    if snapshot["day"] == day
                )
                for run in runs
            ),
            "max": max(
                next(
                    snapshot["faction_count"]
                    for snapshot in run["daily"]
                    if snapshot["day"] == day
                )
                for run in runs
            ),
        }
        for day in (30, 60, 100)
        if days >= day
    }
    report = {
        "configuration": {"days": days, "seeds": seeds, "scenario": scenario},
        "runs": runs,
        "summary": {
            "action_distribution": {
                action: sum(
                    run["action_distribution"].get(action, 0)
                    for run in runs
                )
                for action in sorted({
                    action
                    for run in runs
                    for action in run["action_distribution"]
                })
            },
            "faction_counts": faction_counts,
            "disposition_change_seeds": sum(
                any(
                    snapshot["disposition_changed"]
                    for snapshot in run["daily"]
                )
                for run in runs
            ),
            "decision_influence": {
                category: {
                    field: sum(
                        run["decision_influence"][category][field]
                        for run in runs
                    )
                    for field in ("decisions", "changed_decisions")
                }
                for category in (
                    "hostile_disposition",
                    "friendly_disposition",
                    "attention_pressure",
                )
            },
            "faction_counts": faction_counts,
            "final_status_distribution": {
                status: sum(
                    snapshot["status_distribution"].get(status, 0)
                    for snapshot in final_days
                )
                for status in all_statuses
            },
            "mean_final_population": _mean(
                snapshot["population"] for snapshot in final_days
            ),
            "mean_final_stores": {
                resource: _mean(
                    snapshot["stores"].get(resource, 0)
                    for snapshot in final_days
                )
                for resource in sorted({
                    resource
                    for snapshot in final_days
                    for resource in snapshot["stores"]
                })
            },
        },
    }
    if _acceptance_metrics and _check_determinism:
        replay = run_world_sim(
            days=days,
            seeds=min(2, seeds),
            scenario=scenario,
            _acceptance_metrics=True,
            _check_determinism=False,
        )
        matched_days = 0
        checked_days = 0
        hash_comparisons: dict[str, list[dict[str, Any]]] = {}
        replay_by_seed = {run["seed"]: run for run in replay["runs"]}
        for original in runs[:2]:
            repeated = replay_by_seed.get(original["seed"])
            if repeated is None:
                continue
            comparisons = []
            for first_day, repeated_day in zip(original["daily"], repeated["daily"]):
                first_hash = first_day["state_hash"]
                repeated_hash = repeated_day["state_hash"]
                checked_days += 1
                is_match = first_hash == repeated_hash
                matched_days += int(is_match)
                comparisons.append({
                    "elapsed_day": first_day["elapsed_day"],
                    "first_run": first_hash,
                    "repeat_run": repeated_hash,
                    "match": is_match,
                })
            hash_comparisons[str(original["seed"])] = comparisons
        report["summary"]["determinism"] = {
            "seeds_checked": sorted(int(seed) for seed in hash_comparisons),
            "days_checked": checked_days,
            "matching_days": matched_days,
            "identical_histories": checked_days > 0 and matched_days == checked_days,
            "state_hashes_by_seed": hash_comparisons,
        }
    return report


def run_world_sim_scenarios(
    *,
    days: int,
    seeds: int,
    _acceptance_metrics: bool = False,
) -> dict[str, Any]:
    reports = {
        scenario: run_world_sim(
            days=days,
            seeds=seeds,
            scenario=scenario,
            _acceptance_metrics=_acceptance_metrics,
        )
        for scenario in WORLD_SIM_SCENARIOS
    }
    return {
        "configuration": {"days": days, "seeds": seeds},
        "scenarios": {
            scenario: report["summary"]["final_status_distribution"]
            for scenario, report in reports.items()
        },
        "reports": reports,
    }


def _world_sim_acceptance_rows(
    scenarios: dict[str, dict[str, Any]],
    *,
    days: int,
    retention_caps: dict[str, int],
    event_cap_per_day: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def add_row(
        scenario: str,
        target: str,
        observed: str,
        criterion: str,
        status: str,
    ) -> None:
        rows.append({
            "scenario": scenario,
            "target": target,
            "observed": observed,
            "criterion": criterion,
            "status": status,
        })

    for scenario in WORLD_SIM_SCENARIOS:
        report = scenarios[scenario]
        runs = report["runs"]
        day_100_runs = [
            (run, next(
                (
                    snapshot for snapshot in run["daily"]
                    if snapshot.get("elapsed_day") == 100
                ),
                None,
            ))
            for run in runs
        ]
        completed_day_100 = [(run, snapshot) for run, snapshot in day_100_runs if snapshot]
        if completed_day_100:
            alive_seeds = sum(
                any(
                    settlement["population"] > 0
                    and settlement["status"] != "abandoned"
                    for settlement in snapshot["settlements"]
                )
                for _, snapshot in completed_day_100
            )
            alive_rate = alive_seeds / len(completed_day_100)
            alive_observed = f"{alive_seeds}/{len(completed_day_100)} seeds ({alive_rate:.1%})"
            alive_status = "PASS" if alive_rate > 0.5 else "WARN"
        else:
            alive_observed = "not reached"
            alive_status = "WARN"
        add_row(
            scenario,
            "Settlement alive at day 100",
            alive_observed,
            "Most seeds (>50%) retain at least one populated, non-abandoned settlement",
            alive_status,
        )

        collapsed = 0
        settlement_total = 0
        for run in runs:
            first_snapshot = run["daily"][0] if run["daily"] else None
            if first_snapshot is None:
                continue
            initial_ids = {
                settlement["id"]
                for settlement in first_snapshot["settlements"]
            }
            settlement_total += len(initial_ids)
            failed_ids = {
                settlement["id"]
                for snapshot in run["daily"]
                if snapshot.get("elapsed_day", days + 1) < 40
                for settlement in snapshot["settlements"]
                if settlement["id"] in initial_ids
                and settlement["status"] in {"collapsing", "abandoned"}
            }
            collapsed += len(failed_ids)
        early_collapse_rate = collapsed / settlement_total if settlement_total else 0.0
        add_row(
            scenario,
            "Settlements collapsing/abandoned before day 40",
            f"{collapsed}/{settlement_total} settlements ({early_collapse_rate:.1%})",
            "Rare: <=10% of settlements",
            "PASS" if early_collapse_rate <= 0.10 else "WARN",
        )

        for checkpoint_day in (30, 60, 100):
            if checkpoint_day > days:
                continue
            stance_counts = {stance: 0 for stance in FACTION_STANCES}
            for run in runs:
                snapshot = next(
                    (
                        item for item in run["daily"]
                        if item.get("elapsed_day") == checkpoint_day
                    ),
                    None,
                )
                if snapshot is None:
                    continue
                for stance, count in snapshot["stance_distribution"].items():
                    stance_counts[stance] += count
            active_stances = sum(count > 0 for count in stance_counts.values())
            stance_display = ", ".join(
                f"{stance}={stance_counts[stance]}"
                for stance in FACTION_STANCES
            )
            add_row(
                scenario,
                f"Faction stances at day {checkpoint_day}",
                f"{stance_display}; distinct={active_stances}",
                "More than one stance represented",
                "PASS" if active_stances > 1 else "WARN",
            )

        bound_totals = {
            stat: {"at_bound": 0, "total": 0}
            for stat in (
                "settlement_morale",
                "settlement_unrest",
                "settlement_defense",
                "faction_dispositions",
            )
        }
        event_volumes: list[int] = []
        cap_hits = {key: 0 for key in retention_caps}
        tick_observations = 0
        for run in runs:
            for snapshot in run["daily"]:
                tick_observations += 1
                event_volumes.append(snapshot["event_volume"])
                for stat, counts in snapshot["bound_observations"].items():
                    bound_totals[stat]["at_bound"] += counts["at_bound"]
                    bound_totals[stat]["total"] += counts["total"]
                for collection, cap in retention_caps.items():
                    if snapshot["retention_lengths"][collection] >= cap:
                        cap_hits[collection] += 1

        for stat, counts in bound_totals.items():
            rate = counts["at_bound"] / counts["total"] if counts["total"] else 0.0
            add_row(
                scenario,
                f"Pinned stat: {stat}",
                f"{counts['at_bound']}/{counts['total']} observations ({rate:.1%})",
                "WARN above 50% of entity-ticks at a bound",
                "WARN" if rate > 0.5 else "PASS",
            )

        maximum_events = max(event_volumes, default=0)
        mean_events = _mean(event_volumes)
        add_row(
            scenario,
            "Events per tick (active + internal + history)",
            f"max={maximum_events}; mean={mean_events:.2f}",
            f"Maximum <= configured settlement event cap ({event_cap_per_day}/day)",
            "PASS" if maximum_events <= event_cap_per_day else "WARN",
        )
        for collection, cap in retention_caps.items():
            hit_rate = cap_hits[collection] / tick_observations if tick_observations else 0.0
            add_row(
                scenario,
                f"Retention cap: {collection}",
                f"cap={cap}; hit={cap_hits[collection]}/{tick_observations} ticks ({hit_rate:.1%})",
                "WARN if the cap is hit on >50% of ticks",
                "WARN" if hit_rate > 0.5 else "PASS",
            )

        determinism = report["summary"].get("determinism", {})
        checked = determinism.get("days_checked", 0)
        matching = determinism.get("matching_days", 0)
        deterministic = determinism.get("identical_histories", False)
        add_row(
            scenario,
            "Determinism: two seeds replayed",
            f"{matching}/{checked} daily state hashes match",
            "All per-day hashes match for both replayed seeds",
            "PASS" if deterministic else "WARN",
        )

    return rows


def run_world_sim_acceptance(*, days: int = 100, seeds: int = 30) -> dict[str, Any]:
    scenarios = run_world_sim_scenarios(
        days=days,
        seeds=seeds,
        _acceptance_metrics=True,
    )
    faction_rules = load_world_content().faction_rules
    retention_caps = {
        "active_events": faction_rules.retention.active_events,
        "world_history": faction_rules.retention.world_history,
        "internal_events": faction_rules.retention.internal_events,
        "relationships": faction_rules.retention.relationships,
    }
    event_cap_per_day = load_world_content().settlements.defaults.max_events_per_day
    return {
        "configuration": {
            "days": days,
            "seeds": seeds,
            "scenarios": list(WORLD_SIM_SCENARIOS),
        },
        "measurement_notes": {
            "event_volume": "New entries for the current tick in activeEvents, internalEvents, and worldHistory.",
            "faction_dispositions": "Both faction-to-faction relationships and faction-to-player dispositions.",
            "settlement_alive": "Population > 0 and status is not abandoned.",
            "early_collapse": "Each initial settlement is counted once if it reaches collapsing or abandoned before elapsed day 40.",
            "determinism": "Canonical SHA-256 of the full world state after each tick; seeds 1 and 2 are replayed once.",
        },
        "retention_caps": retention_caps,
        "event_cap_per_day": event_cap_per_day,
        "summary_table": _world_sim_acceptance_rows(
            scenarios["reports"],
            days=days,
            retention_caps=retention_caps,
            event_cap_per_day=event_cap_per_day,
        ),
        "scenarios": scenarios["reports"],
    }


def _write_world_sim_acceptance(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / "world_sim_acceptance.csv"
    fields = ("scenario", "target", "observed", "criterion", "status")
    with table_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["summary_table"])
    with (out_dir / "world_sim_acceptance.json").open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _print_world_sim_acceptance(report: dict[str, Any]) -> None:
    config = report["configuration"]
    print(
        f"World_sim acceptance: {config['days']} days x {config['seeds']} seeds "
        f"per scenario"
    )
    print(f"{'SCENARIO':<18} | {'TARGET':<44} | {'OBSERVED':<76} | STATUS")
    print("-" * 155)
    for row in report["summary_table"]:
        print(
            f"{row['scenario']:<18} | {row['target']:<44} | "
            f"{row['observed']:<76} | {row['status']}"
        )


def _write_world_sim_report(out_dir: Path, report: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "world_sim.json").open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _check_determinism(
    *,
    games: int,
    days: int,
    players: list[int],
    bots: list[str],
    replan: int,
    workers: int,
    lite: bool,
) -> None:
    payloads = [
        {
            "seed": seed,
            "bot": bot,
            "players": player_count,
            "days": days,
            "replan": replan,
            "lite": lite,
        }
        for bot in bots
        for player_count in players
        for seed in range(1, min(games, 3) + 1)
    ]
    if workers == 1:
        first = [_run_game(payload)["per_day"] for payload in payloads]
        second = [_run_game(payload)["per_day"] for payload in payloads]
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            first = [result["per_day"] for result in executor.map(_run_game, payloads)]
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        ) as executor:
            second = [result["per_day"] for result in executor.map(_run_game, payloads)]
    if first != second:
        raise AssertionError("Per-day records differ between repeated seeded runs.")


def _write_play_outputs(
    out_dir: Path,
    report: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "report.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    with (out_dir / "per_day.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _print_play_report(report: dict[str, Any], elapsed: float, game_count: int) -> None:
    print("bot        K  window  world_pause  player_interrupt  pause_gap(mean/median/p10/p90)  zero_pause  gate  near_miss  encounters  injuries  deaths  health/stamina/stress/hunger/thirst  food/water")
    for group in report["groups"]:
        for window in group["windows"]:
            gaps = window["days_between_world_pauses"]
            gap_text = "/".join(
                "-" if gaps[key] is None else f"{gaps[key]:.1f}"
                for key in ("mean", "median", "p10", "p90")
            )
            stats = window["mean_end_stats"]
            print(
                f"{group['bot']:<10} {group['players']}  "
                f"{window['start_day']:02d}-{window['end_day']:02d}  "
                f"{window['world_pause_rate']:.3f}        "
                f"{window['per_player_interrupt_rate']:.3f}            "
                f"{gap_text:<29} "
                f"{window['zero_pause_games_percent']:5.1f}%      "
                f"{window['gate_trigger_rate']:.3f}  "
                f"{window['near_miss_rate']:.3f}       "
                f"{window['encounters']:>5}       {window['injuries']:>4}     "
                f"{window['deaths']:>4}    "
                f"{stats['health']:.1f}/{stats['stamina']:.1f}/{stats['stress']:.1f}/{stats['hunger']:.1f}/{stats['thirst']:.1f} "
                f"{stats['food']:.1f}/{stats['water']:.1f}"
            )

    low, high = TARGET_MEAN_DAYS_BETWEEN_WORLD_PAUSES
    print("\nTarget check (mean world-pause gap over days 1-20):")
    for group in report["groups"]:
        if group["bot"] not in ORIGINAL_BOT_NAMES:
            continue
        target = group["target_days_1_20"]
        mean_gap = target["mean_days_between_world_pauses"]
        if mean_gap is None:
            status = "WARN"
            measured = "no pauses observed"
        else:
            status = "PASS" if low <= mean_gap <= high else "WARN"
            measured = f"{mean_gap:.2f} days"
        if target["measured_days"] < 20:
            measured += f" (only {target['measured_days']} days measured)"
        print(
            f"{status} {group['bot']} K={group['players']}: {measured}; "
            f"{target['zero_pause_games_percent']:.1f}% of games had no pauses"
        )
    _print_impact_checks(report)
    print("\nWorld pause rate by slot:")
    print("bot              K dawn    day   dusk  night")
    for group in report["groups"]:
        rates = group.get("pause_rate_by_slot", {})
        print(
            f"{group['bot']:<16} {group['players']} "
            f"{rates.get('dawn', 0.0):5.3f} "
            f"{rates.get('day', 0.0):5.3f} "
            f"{rates.get('dusk', 0.0):5.3f} "
            f"{rates.get('night', 0.0):5.3f}"
        )
    _print_encounter_outcomes(report)
    _print_economy_health(report)
    print(f"\n{game_count} games in {elapsed:.3f}s: {game_count / elapsed if elapsed else 0.0:.2f} games/second")


def _print_economy_health(report: dict[str, Any]) -> None:
    print("\nDeaths and resource-zero player-days by bot:")
    print(
        "bot              K deaths starvation dehydration combat/injury other "
        "food==0% water==0% either==0%"
    )
    for group in report["groups"]:
        player_days = int(group.get("player_days", 0))
        zero_percent = {
            name: (
                100.0 * group.get(field, 0) / player_days
                if player_days else 0.0
            )
            for name, field in (
                ("food", "food_zero_player_days"),
                ("water", "water_zero_player_days"),
                ("either", "resource_zero_player_days"),
            )
        }
        causes = group.get("death_causes", {})
        print(
            f"{group['bot']:<16} {group['players']} "
            f"{group.get('deaths', 0):>6} "
            f"{causes.get('starvation', 0):>10} "
            f"{causes.get('dehydration', 0):>11} "
            f"{causes.get('combat/injury', 0):>13} "
            f"{causes.get('other', 0):>5} "
            f"{zero_percent['food']:8.1f} "
            f"{zero_percent['water']:9.1f} "
            f"{zero_percent['either']:10.1f}"
        )


def _print_encounter_outcomes(report: dict[str, Any]) -> None:
    print("\nEncounter outcomes by bot (hurt/serious are injury encounters):")
    print(
        "bot              K encounters evaded% close% hurt% serious% "
        "hurt+serious% injuries/encounter deaths deaths<15"
    )
    for group in report["groups"]:
        counts = group.get("encounter_outcome_counts", {})
        encounters = int(group.get("encounters", 0))
        percentages = {
            outcome: (
                100.0 * counts.get(outcome, 0) / encounters
                if encounters else 0.0
            )
            for outcome in ("evaded", "close_call", "hurt", "serious")
        }
        hurt_serious = percentages["hurt"] + percentages["serious"]
        print(
            f"{group['bot']:<16} {group['players']} {encounters:>10} "
            f"{percentages['evaded']:7.1f} {percentages['close_call']:6.1f} "
            f"{percentages['hurt']:5.1f} {percentages['serious']:8.1f} "
            f"{hurt_serious:13.1f} "
            f"{group.get('injuries_per_encounter', 0.0):17.3f} "
            f"{group.get('deaths', 0):>6} "
            f"{group.get('deaths_before_day_15', 0):>9}"
        )
    print("\nEncounter target checks (early deaths are measured against starting players):")
    groups = {
        (group["bot"], group["players"]): group
        for group in report["groups"]
    }
    for group in report["groups"]:
        bot = group["bot"]
        players = group["players"]
        encounters = int(group.get("encounters", 0))
        counts = group.get("encounter_outcome_counts", {})
        injury_rate = (
            (counts.get("hurt", 0) + counts.get("serious", 0)) / encounters
            if encounters else None
        )
        if bot == "careful_planned":
            baseline = groups.get(("careful", players))
            baseline_counts = baseline.get("encounter_outcome_counts", {}) if baseline else {}
            baseline_encounters = int(baseline.get("encounters", 0)) if baseline else 0
            baseline_rate = (
                (baseline_counts.get("hurt", 0) + baseline_counts.get("serious", 0))
                / baseline_encounters
                if baseline_encounters else None
            )
            planned_status = (
                "PASS"
                if injury_rate is not None
                and baseline_rate is not None
                and injury_rate <= baseline_rate * PLANNED_HURT_SERIOUS_MAX_RATIO
                else "WARN"
            )
            planned_detail = (
                f"{injury_rate:.1%} vs careful {baseline_rate:.1%}"
                if injury_rate is not None and baseline_rate is not None
                else "insufficient encounter data"
            )
        else:
            low, high = HURT_SERIOUS_TARGET
            planned_status = (
                "PASS"
                if injury_rate is not None and low <= injury_rate <= high
                else "WARN"
            )
            planned_detail = (
                f"{injury_rate:.1%} target {low:.0%}-{high:.0%}"
                if injury_rate is not None
                else "no encounters observed"
            )

        starting_players = int(group.get("games", 0)) * players
        early_deaths = int(group.get("deaths_before_day_15", 0))
        early_death_rate = (
            early_deaths / starting_players if starting_players else None
        )
        death_status = (
            "PASS"
            if early_death_rate is not None and early_death_rate <= EARLY_DEATH_RATE_MAX
            else "WARN"
        )
        death_detail = (
            f"{early_deaths}/{starting_players} ({early_death_rate:.1%}; "
            f"limit {EARLY_DEATH_RATE_MAX:.0%})"
            if early_death_rate is not None
            else "no starting players"
        )
        print(
            f"{bot} K={players}: hurt+serious {planned_status} ({planned_detail}); "
            f"deaths before day 15 {death_status} ({death_detail})"
        )


def _print_impact_checks(report: dict[str, Any]) -> None:
    groups = {
        (group["bot"], group["players"]): group
        for group in report["groups"]
    }
    variants = [
        bot
        for bot in PLAN_VARIANT_BOTS
        if any(group["bot"] == bot for group in report["groups"])
    ]
    if not variants:
        return

    print("\nPlan impact check (pause-rate ratio vs careful; days 1-20):")
    target_bands = {
        "careful_planned": (0.25, 0.60),
        "careful_sloppy": (1.70, 3.50),
    }
    player_counts = sorted({
        group["players"]
        for group in report["groups"]
        if group["bot"] in PLAN_VARIANT_BOTS
    })
    for player_count in player_counts:
        baseline = groups.get(("careful", player_count))
        baseline_target = baseline.get("target_days_1_20", {}) if baseline else {}
        baseline_rate = baseline_target.get("world_pause_rate")
        rates = []
        for bot in ("careful", *PLAN_VARIANT_BOTS):
            group = groups.get((bot, player_count))
            rate = (
                group.get("target_days_1_20", {}).get("world_pause_rate")
                if group is not None
                else None
            )
            rates.append(f"{bot}={rate:.3f}" if rate is not None else f"{bot}=n/a")
        print(f"K={player_count} world_pause_rate: " + "  ".join(rates))
        for bot in variants:
            variant = groups.get((bot, player_count))
            if variant is None:
                continue
            target = variant.get("target_days_1_20", {})
            variant_rate = target.get("world_pause_rate")
            games = int(variant.get("games", 0))
            sample_note = " (small sample)" if games < 20 else ""
            lower, upper = target_bands[bot]

            if isinstance(variant_rate, (int, float)) and variant_rate == 0.0:
                status = "WARN"
                impact_text = "no pauses observed"
            elif (
                not isinstance(variant_rate, (int, float))
                or not isinstance(baseline_rate, (int, float))
                or baseline_rate <= 0.0
            ):
                status = "WARN"
                impact_text = "ratio unavailable"
            else:
                ratio = variant_rate / baseline_rate
                status = "PASS" if lower <= ratio <= upper else "WARN"
                impact_text = f"{ratio:.2f}x"
            rate_text = (
                f"{variant_rate:.3f} vs {baseline_rate:.3f}"
                if isinstance(variant_rate, (int, float))
                and isinstance(baseline_rate, (int, float))
                else "unavailable"
            )
            print(
                f"{status} {bot} K={player_count}: pause-rate ratio {impact_text} "
                f"(target {lower:.2f}-{upper:.2f}x); world_pause_rate "
                f"{rate_text}{sample_note}"
            )


def _interrupt_probability(
    template: Any,
    logged_steps: list[dict[str, Any]],
    action_type: str,
) -> float:
    interrupt_steps = [step for step in template.steps if step.interrupt]
    if len(interrupt_steps) != 1:
        raise ValueError(
            f"Action {action_type!r} template must have exactly one interrupt step; "
            f"found {len(interrupt_steps)}"
        )
    by_name = {step.name: step for step in template.steps}
    path: set[str] = set()
    current = interrupt_steps[0]
    while True:
        path.add(current.name)
        if current.requires is None:
            break
        current = by_name[current.requires]
    chances = {
        step["name"]: float(step["chance"])
        for step in logged_steps
    }
    return math.prod(
        chances[step.name]
        for step in template.steps
        if step.name in path
    )


def build_sweep_rows(library: FactorLibrary | None = None) -> list[dict[str, Any]]:
    active_library = library if library is not None else get_library()
    rows = []
    for action_type in sorted(active_library.actions):
        template_name = active_library.actions[action_type]
        template = active_library.templates[template_name]
        for danger in range(6):
            for current_time in ("12:00", "23:00"):
                for weather in ("clear", "storm"):
                    for plan_quality in (-1, 0, 1):
                        for stealth in (False, True):
                            focus = "quiet stealth" if stealth else ""
                            state = {
                                "currentDay": 1,
                                "currentTime": current_time,
                                "weather": weather,
                                "players": [{
                                    "id": "player_1",
                                    "location": "sweep-location",
                                    "stress": 0,
                                    "statusEffects": [],
                                    "entities": {"companions": []},
                                }],
                                "locations": [{
                                    "id": "sweep-location",
                                    "name": "Sweep Location",
                                    "dangerLevel": danger,
                                    "zombiePopulation": 0,
                                    "discovered": True,
                                }],
                            }
                            action = StructuredAction(
                                actionId="harness-sweep",
                                actionType=action_type,
                                actor="player_1",
                                target="sweep-location",
                                parameters={"policy": {"focus": focus}},
                            )
                            result = run_chain(
                                state,
                                action,
                                seed=1,
                                plan_quality=plan_quality,
                                library=active_library,
                            )
                            logged_steps = [step.to_dict() for step in result.steps]
                            gate_steps = [step for step in logged_steps if step["kind"] == "gate"]
                            encounter_steps = [
                                step for step in logged_steps if step["kind"] == "encounter"
                            ]
                            p_interrupt = _interrupt_probability(
                                template,
                                logged_steps,
                                action_type,
                            )
                            rows.append({
                                "action_type": action_type,
                                "danger_level": danger,
                                "time": current_time,
                                "weather": weather,
                                "plan_quality": plan_quality,
                                "stealth": stealth,
                                "gate_chance": gate_steps[0]["chance"] if gate_steps else 0.0,
                                "encounter_chance": (
                                    encounter_steps[0]["chance"] if encounter_steps else 0.0
                                ),
                                "p_interrupt": p_interrupt,
                                "expected_days_between_interrupts": 1.0 / p_interrupt,
                            })
    rows.sort(key=lambda row: (
        row["action_type"],
        row["danger_level"],
        row["time"],
        row["weather"],
        row["plan_quality"],
        row["stealth"],
    ))
    return rows


def _print_pivot(
    rows: list[dict[str, Any]],
    *,
    title: str,
    action_types: list[str],
    plan_quality: int,
) -> None:
    print(title)
    print("action_type " + " ".join(f"D{danger:>7}" for danger in range(6)))
    for action_type in action_types:
        values = []
        for danger in range(6):
            row = next(
                item for item in rows
                if item["action_type"] == action_type
                and item["danger_level"] == danger
                and item["time"] == "12:00"
                and item["weather"] == "clear"
                and item["plan_quality"] == plan_quality
                and item["stealth"] is False
            )
            values.append(f"{row['expected_days_between_interrupts']:8.2f}")
        print(f"{action_type:<12} " + " ".join(values))


def _print_search_plan_pivot(rows: list[dict[str, Any]]) -> None:
    print("Search expected days between interrupts by danger (day, clear, no stealth)")
    print("danger  plan=-1  plan=+1")
    for danger in range(6):
        values = []
        for plan_quality in (-1, 1):
            row = next(
                item for item in rows
                if item["action_type"] == "search"
                and item["danger_level"] == danger
                and item["time"] == "12:00"
                and item["weather"] == "clear"
                and item["plan_quality"] == plan_quality
                and item["stealth"] is False
            )
            values.append(f"{row['expected_days_between_interrupts']:8.2f}")
        print(f"{danger:>6}  " + "  ".join(values))


def build_plan_sweep_rows(
    library: FactorLibrary | None = None,
) -> list[dict[str, Any]]:
    active_library = library if library is not None else get_library()
    template = active_library.template_for("search")
    if template is None:
        raise ValueError("The search action must have a chain template")

    scenarios = (
        ("none", None),
        ("good plan", {
            "targetLocationIds": ["sweep-location"],
            "itemsPlanned": ["medical supplies", "water"],
            "timing": "night",
            "approach": "stealth",
            "countermeasures": ["alternate route", "scout first"],
            "hasFallback": True,
        }),
        ("bad plan", {
            "targetLocationIds": ["unknown-location"],
            "itemsPlanned": ["rocket launcher"],
            "timing": "night",
            "approach": "force",
            "countermeasures": [],
            "hasFallback": False,
        }),
    )
    rows = []
    for danger in range(6):
        state = {
            "currentDay": 1,
            "currentTime": "12:00",
            "weather": "clear",
            "players": [{
                "id": "player_1",
                "location": "sweep-location",
                "inventory": ["medical_supplies"],
                "statusEffects": [],
                "entities": {"companions": [], "possessions": []},
            }],
            "locations": [{
                "id": "sweep-location",
                "name": "Sweep Location",
                "dangerLevel": danger,
                "zombiePopulation": 0,
                "discovered": True,
            }],
            "playerKnownWorld": {"locations": [{"id": "sweep-location"}]},
            "resources": {"water": 1},
        }
        for scenario, raw_features in scenarios:
            result = run_chain(
                state,
                StructuredAction(
                    actionId="harness-plan-sweep",
                    actionType="search",
                    actor="player_1",
                    target="sweep-location",
                ),
                seed=1,
                library=active_library,
                plan_features=PlanFeatures.from_untrusted(raw_features),
            )
            logged_steps = [step.to_dict() for step in result.steps]
            p_interrupt = _interrupt_probability(
                template,
                logged_steps,
                "search",
            )
            rows.append({
                "action_type": "search",
                "danger_level": danger,
                "scenario": scenario,
                "p_interrupt": p_interrupt,
                "expected_days_between_interrupts": 1.0 / p_interrupt,
            })
    return rows


def _print_plan_feature_pivot(rows: list[dict[str, Any]]) -> None:
    print("Search expected days between interrupts by danger (day, clear)")
    print("danger  none  good plan  bad plan")
    for danger in range(6):
        values = []
        for scenario in ("none", "good plan", "bad plan"):
            row = next(
                item for item in rows
                if item["action_type"] == "search"
                and item["danger_level"] == danger
                and item["scenario"] == scenario
            )
            values.append(f"{row['expected_days_between_interrupts']:8.2f}")
        print(f"{danger:>6}  " + "  ".join(values))


def _write_sweep(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = (
        "action_type",
        "danger_level",
        "time",
        "weather",
        "plan_quality",
        "stealth",
        "gate_chance",
        "encounter_chance",
        "p_interrupt",
        "expected_days_between_interrupts",
    )
    with (out_dir / "sweep.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _parse_csv_values(value: str, *, cast: Any, option: str) -> list[Any]:
    try:
        result = [cast(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid {option}: {value!r}") from exc
    if not result:
        raise argparse.ArgumentTypeError(f"{option} must not be empty")
    if len(set(result)) != len(result):
        raise argparse.ArgumentTypeError(f"{option} values must not repeat")
    return result


def _default_out() -> Path:
    return BACKEND_DIR / "tools" / "harness_out"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic scripted survival games.")
    commands = parser.add_subparsers(dest="command", required=True)

    play = commands.add_parser("play", help="Play seeded games with scripted bots.")
    play.add_argument("--games", type=int, default=100)
    play.add_argument("--days", type=int, default=60)
    play.add_argument("--players", default="1,2,3")
    play.add_argument("--bots", default=",".join(BOT_NAMES))
    play.add_argument("--replan", type=int, default=7)
    play.add_argument("--workers", type=int, default=1)
    play.add_argument("--out", type=Path, default=_default_out())
    lite_group = play.add_mutually_exclusive_group()
    lite_group.add_argument("--lite", dest="lite", action="store_true", default=True)
    lite_group.add_argument("--no-lite", dest="lite", action="store_false")
    play.add_argument("--check-determinism", action="store_true")

    sweep = commands.add_parser("sweep", help="Run the analytic probability-factor sweep.")
    sweep.add_argument("--out", type=Path, default=_default_out())

    world_sim = commands.add_parser(
        "world_sim",
        help="Simulate settlements and factions without player actions.",
    )
    world_sim.add_argument("--days", type=int, default=100)
    world_sim.add_argument("--seeds", type=int, default=30)
    world_sim.add_argument(
        "--scenario",
        choices=("baseline", "depleted_stores", "mild_shortage", "high_unrest", "all"),
        default="all",
    )
    world_sim.add_argument(
        "--out",
        type=Path,
        default=_default_out() / "world_sim",
    )
    world_sim.add_argument(
        "--acceptance",
        action="store_true",
        help="Run the PASS/WARN acceptance report across all world_sim scenarios.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "sweep":
        rows = build_sweep_rows()
        _write_sweep(args.out, rows)
        print(f"Wrote {len(rows)} analytic rows to {args.out / 'sweep.csv'}")
        actions = sorted({row["action_type"] for row in rows})
        _print_pivot(
            rows,
            title="Expected days between interrupts by action and danger (day, clear, plan 0, no stealth)",
            action_types=actions,
            plan_quality=0,
        )
        _print_search_plan_pivot(rows)
        _print_plan_feature_pivot(build_plan_sweep_rows())
        return 0

    if args.command == "world_sim":
        if args.days <= 0 or args.seeds <= 0:
            parser.error("--days and --seeds must be positive")
        if args.acceptance:
            if args.scenario != "all":
                parser.error("--acceptance always includes all world_sim scenarios")
            report = run_world_sim_acceptance(days=args.days, seeds=args.seeds)
            _write_world_sim_acceptance(args.out, report)
            _print_world_sim_acceptance(report)
            print(f"Wrote {args.out / 'world_sim_acceptance.csv'}")
            print(f"Wrote {args.out / 'world_sim_acceptance.json'}")
            return 0
        if args.scenario == "all":
            report = run_world_sim_scenarios(days=args.days, seeds=args.seeds)
            args.out.mkdir(parents=True, exist_ok=True)
            with (args.out / "world_sim.json").open(
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
            print("World simulation status distribution by scenario:")
            print(
                f"days={args.days} seeds={args.seeds} "
                f"scenarios={report['scenarios']}"
            )
        else:
            report = run_world_sim(
                days=args.days,
                seeds=args.seeds,
                scenario=args.scenario,
            )
            _write_world_sim_report(args.out, report)
            print("World simulation (final status distribution and population/stores):")
            print(
                f"days={args.days} seeds={args.seeds} "
                f"scenario={args.scenario} "
                f"statuses={report['summary']['final_status_distribution']} "
                f"mean_population={report['summary']['mean_final_population']:.1f} "
                f"mean_stores={report['summary']['mean_final_stores']}"
            )
            print("\nPlayer-influence decision changes:")
            print("influence              decisions  changed")
            for category, counts in report["summary"]["decision_influence"].items():
                print(
                    f"{category:<22} {counts['decisions']:>9} "
                    f"{counts['changed_decisions']:>8}"
                )
        print(f"Wrote {args.out / 'world_sim.json'}")
        return 0

    players = _parse_csv_values(args.players, cast=int, option="players")
    bots = _parse_csv_values(args.bots, cast=str, option="bots")
    if any(player not in (1, 2, 3) for player in players):
        parser.error("--players values must be 1, 2, or 3")
    if any(bot not in BOT_NAMES for bot in bots):
        parser.error(f"--bots values must be {', '.join(BOT_NAMES)}")
    if args.games <= 0 or args.days <= 0 or args.replan <= 0 or args.workers <= 0:
        parser.error("--games, --days, --replan, and --workers must be positive")

    if args.check_determinism:
        _check_determinism(
            games=args.games,
            days=args.days,
            players=players,
            bots=bots,
            replan=args.replan,
            workers=args.workers,
            lite=args.lite,
        )
        print(f"PASS --check-determinism ({min(args.games, 3)} seeded games per group)")

    start = time.perf_counter()
    report, rows = run_play(
        games=args.games,
        days=args.days,
        players=players,
        bots=bots,
        replan=args.replan,
        workers=args.workers,
        lite=args.lite,
    )
    elapsed = time.perf_counter() - start
    _write_play_outputs(args.out, report, rows)
    _print_play_report(report, elapsed, args.games * len(players) * len(bots))
    print(f"Wrote {args.out / 'report.json'} and {args.out / 'per_day.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
