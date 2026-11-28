from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from probability_config import PLAN_MODIFIER_CAP, clamp_player_stat
from probability_engine import roll_value


_OUTCOME_ORDER = ("evaded", "close_call", "hurt", "serious")
_CONTENT_PATH = Path(__file__).resolve().parent / "content" / "encounter_outcomes.json"


@dataclass(frozen=True)
class EncounterOutcome:
    outcome: str
    roll: float
    probabilities: dict[str, float]
    modifiers: list[dict[str, Any]]
    reasons: list[dict[str, str]]
    player_changes: dict[str, int]
    status_effect: dict[str, Any] | None
    resource_changes: dict[str, int]
    lost_item_index: int | None = None
    lost_item_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "roll": round(self.roll, 6),
            "probabilities": dict(self.probabilities),
            "modifiers": copy.deepcopy(self.modifiers),
            "reasons": copy.deepcopy(self.reasons),
            "effects": {
                "player_changes": dict(self.player_changes),
                "status_effect": copy.deepcopy(self.status_effect),
                "resource_changes": dict(self.resource_changes),
                "lost_item_name": self.lost_item_name,
            },
        }


@lru_cache(maxsize=1)
def _load_content() -> dict[str, Any]:
    with _CONTENT_PATH.open("r", encoding="utf-8") as handle:
        content = json.load(handle)
    probabilities = content["base_probabilities"]
    if set(probabilities) != set(_OUTCOME_ORDER):
        raise ValueError(f"{_CONTENT_PATH}: base_probabilities must define all outcomes")
    if any(
        not isinstance(probabilities[name], (int, float))
        or not math.isfinite(float(probabilities[name]))
        or probabilities[name] < 0
        for name in _OUTCOME_ORDER
    ):
        raise ValueError(f"{_CONTENT_PATH}: base probabilities must be finite and non-negative")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-9):
        raise ValueError(f"{_CONTENT_PATH}: base probabilities must sum to 1")
    return content


def _state_dict(state: Any) -> dict[str, Any]:
    if hasattr(state, "to_dict"):
        state = state.to_dict()
    if isinstance(state, dict) and isinstance(state.get("world_state"), dict):
        return state["world_state"]
    return state if isinstance(state, dict) else {}


def _numeric(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _player_value(player: dict[str, Any], name: str, default: float) -> float:
    return _numeric(player.get(name), default)


def _location_for(state: dict[str, Any], player: dict[str, Any], action: Any) -> dict[str, Any]:
    locations = state.get("locations", [])
    target = getattr(action, "target", None)
    actor_location = player.get("location")
    candidates = (target, actor_location)
    if isinstance(locations, dict):
        for location_id in candidates:
            location = locations.get(location_id)
            if isinstance(location, dict):
                return location
            for record in locations.values():
                if isinstance(record, dict) and record.get("id") == location_id:
                    return record
    elif isinstance(locations, list):
        for location_id in candidates:
            location = next(
                (
                    record for record in locations
                    if isinstance(record, dict) and record.get("id") == location_id
                ),
                None,
            )
            if location is not None:
                return location
    return {}


def _triggered_encounter(chain_result: Any) -> bool:
    if (
        getattr(chain_result, "encounterOccurred", False)
        or getattr(chain_result, "encounter_occurred", False)
    ):
        return True
    steps = getattr(chain_result, "steps", None)
    if steps is None:
        steps = getattr(chain_result, "rollLog", [])
    return any(
        (
            step.get("kind") == "encounter" and step.get("triggered") is True
            if isinstance(step, dict)
            else getattr(step, "kind", None) == "encounter"
            and getattr(step, "triggered", False) is True
        )
        for step in steps
    )


def _plan_quality(chain_result: Any) -> float:
    value = getattr(chain_result, "plan_quality", None)
    if value is None:
        value = getattr(chain_result, "planQuality", 0.0)
    return max(-1.0, min(1.0, _numeric(value)))


def _modifier_value(
    modifier: dict[str, Any],
    player: dict[str, Any],
    location: dict[str, Any],
    plan_quality: float,
) -> float:
    input_name = modifier["input"]
    if input_name == "player.companion_count":
        entities = player.get("entities", {})
        companions = entities.get("companions", []) if isinstance(entities, dict) else []
        value = sum(item is not None for item in companions) if isinstance(companions, list) else 0
    elif input_name.startswith("player."):
        value = _player_value(player, input_name.removeprefix("player."), 0.0)
    elif input_name == "plan_quality":
        value = plan_quality
    elif input_name == "location.dangerLevel":
        value = _numeric(location.get("dangerLevel"), 0.0)
    else:
        raise ValueError(f"{_CONTENT_PATH}: unsupported modifier input {input_name!r}")

    if "condition" in modifier:
        threshold = float(modifier["threshold"])
        if modifier["condition"] == "lte":
            return float(modifier["delta"]) if value <= threshold else 0.0
        if modifier["condition"] == "gte":
            return float(modifier["delta"]) if value >= threshold else 0.0
        raise ValueError(f"{_CONTENT_PATH}: unsupported condition {modifier['condition']!r}")

    if input_name == "plan_quality":
        return value * float(modifier["per_unit"])
    units = min(max(0.0, value), float(modifier.get("max_units", math.inf)))
    return units * float(modifier["per_unit"])


def _effect_for(
    outcome: str,
    config: dict[str, Any],
    state: dict[str, Any],
    player: dict[str, Any],
    action: Any,
    roll: float,
    day: int,
    seed: int,
) -> tuple[dict[str, int], dict[str, Any] | None, dict[str, int], int | None, str | None]:
    effect = config["effects"][outcome]
    player_changes = {
        key: int(value)
        for key, value in effect.items()
        if key in {"health", "stamina", "stress", "hunger"}
    }
    status_effect = None
    injury_name = effect.get("injury")
    if isinstance(injury_name, str):
        action_id = str(getattr(action, "actionId", "encounter"))
        digest = hashlib.sha256(
            f"{seed}:{action_id}:{day}:encounter_outcome:{roll:.12f}".encode("utf-8")
        ).hexdigest()
        status_effect = {
            "id": f"effect_{digest[:16]}",
            "name": injury_name,
            "appliedDay": day,
            "expiresDay": day + int(effect["expires_days"]),
            "mechanicalEffect": copy.deepcopy(effect["mechanical_effect"]),
        }

    resource_changes: dict[str, int] = {}
    lost_item_index = None
    lost_item_name = None
    if outcome == "serious":
        resources = state.get("resources", {})
        if isinstance(resources, dict):
            for resource in ("food", "water"):
                if _numeric(resources.get(resource)) > 0:
                    resource_changes[resource] = -int(effect["resource_loss"])
                    break
        if not resource_changes:
            inventory = player.get("inventory", [])
            if isinstance(inventory, list) and inventory:
                lost_item_index = int(roll * len(inventory)) % len(inventory)
                item = inventory[lost_item_index]
                if isinstance(item, dict):
                    lost_item_name = str(
                        item.get("name", item.get("id", item.get("itemId", "item")))
                    )
                else:
                    lost_item_name = str(item)

    return (
        player_changes,
        status_effect,
        resource_changes,
        lost_item_index,
        lost_item_name,
    )


def resolve_encounter(
    state: Any,
    player: dict[str, Any],
    action: Any,
    chain_result: Any,
    seed: int,
) -> EncounterOutcome:
    if not _triggered_encounter(chain_result):
        raise ValueError("resolve_encounter requires a triggered encounter step")
    if not isinstance(player, dict):
        raise TypeError("player must be a dictionary")

    world = _state_dict(state)
    config = _load_content()
    day_value = world.get("currentDay", 1)
    day = int(_numeric(day_value, 1))
    time_value = world.get("currentTime", "00:00")
    time_str = time_value if isinstance(time_value, str) else "00:00"
    action_id = str(getattr(action, "actionId", ""))
    roll = roll_value(
        seed,
        action_id,
        day,
        time_str,
        "encounter_outcome",
        0,
    )
    location = _location_for(world, player, action)
    quality = _plan_quality(chain_result)
    modifiers: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    total_modifier = 0.0
    for modifier in config["modifiers"]:
        delta = _modifier_value(modifier, player, location, quality)
        if delta == 0.0:
            continue
        total_modifier += delta
        modifiers.append({
            "source": modifier["id"],
            "delta": delta,
        })
        reasons.append({
            "factor": modifier["id"],
            "effect": "hurts" if delta > 0.0 else "helps",
            "explanation": modifier["explanation"],
        })

    modifier_cap = min(
        PLAN_MODIFIER_CAP,
        max(0.0, float(config["modifier_cap"])),
    )
    capped_modifier = max(-modifier_cap, min(modifier_cap, total_modifier))
    if capped_modifier != total_modifier:
        modifiers.append({
            "source": "encounter_modifier_cap",
            "delta": capped_modifier - total_modifier,
        })
        reasons.append({
            "factor": "encounter_modifier_cap",
            "effect": "hurts" if capped_modifier > 0.0 else "helps",
            "explanation": "The combined encounter modifiers were capped.",
        })

    probabilities = {
        name: float(config["base_probabilities"][name])
        for name in _OUTCOME_ORDER
    }
    if capped_modifier >= 0.0:
        shift = min(capped_modifier, probabilities["close_call"])
        probabilities["close_call"] -= shift
        probabilities["hurt"] += shift
    else:
        shift = min(-capped_modifier, probabilities["hurt"])
        probabilities["hurt"] -= shift
        probabilities["close_call"] += shift

    threshold = 0.0
    outcome = _OUTCOME_ORDER[-1]
    for name in _OUTCOME_ORDER:
        threshold += probabilities[name]
        if roll < threshold:
            outcome = name
            break

    effects = _effect_for(
        outcome,
        config,
        world,
        player,
        action,
        roll,
        day,
        seed,
    )
    return EncounterOutcome(
        outcome=outcome,
        roll=roll,
        probabilities=probabilities,
        modifiers=modifiers,
        reasons=reasons,
        player_changes=effects[0],
        status_effect=effects[1],
        resource_changes=effects[2],
        lost_item_index=effects[3],
        lost_item_name=effects[4],
    )


def resolve_pending_encounter(
    pending: dict[str, Any],
    current_state: Any,
    *,
    chosen_option: str | None = None,
) -> tuple[EncounterOutcome | None, str | None]:
    world = copy.deepcopy(_state_dict(current_state))
    player_id = str(pending.get("playerId", ""))
    player = _player_from(world, player_id)
    if player is None:
        return None, "actor_missing"
    if player.get("alive", True) is False or _numeric(player.get("health"), 100) <= 0:
        return None, "actor_dead"

    world["currentDay"] = int(pending["day"])
    world["currentTime"] = str(pending["time"])
    action = SimpleNamespace(
        actionId=str(pending["actionId"]),
        actionType=str(pending["actionType"]),
        actor=player_id,
        target=str(pending.get("target", "")),
        parameters=copy.deepcopy(pending.get("parameters", {})),
    )
    chain_result = SimpleNamespace(
        encounterOccurred=True,
        planQuality=float(pending.get("planQuality", 0.0)),
    )
    # Tactical option effects are introduced in Stage B; Stage A preserves the choice.
    _ = chosen_option
    return (
        resolve_encounter(
            world,
            player,
            action,
            chain_result,
            int(pending["seed"]),
        ),
        None,
    )


def apply_pending_encounter(
    current_state: dict[str, Any],
    player_id: str,
    outcome: EncounterOutcome,
) -> tuple[dict[str, Any], str | None]:
    world = _state_dict(current_state)
    player = _player_from(world, player_id)
    if player is None:
        return copy.deepcopy(current_state), "actor_missing"
    if player.get("alive", True) is False or _numeric(player.get("health"), 100) <= 0:
        return copy.deepcopy(current_state), "actor_dead"
    return apply_encounter_outcome(current_state, player_id, outcome), None


def apply_encounter_outcome(
    state: dict[str, Any],
    player_id: str,
    outcome: EncounterOutcome,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    world = _state_dict(updated)
    player = _player_from(world, player_id)
    if not isinstance(player, dict):
        raise ValueError(f"Encounter actor {player_id!r} is missing from the result state")

    for name, delta in outcome.player_changes.items():
        player[name] = clamp_player_stat(_numeric(player.get(name), 0.0) + delta)
    if outcome.status_effect is not None:
        player.setdefault("statusEffects", []).append(copy.deepcopy(outcome.status_effect))
    if outcome.lost_item_index is not None:
        inventory = player.get("inventory", [])
        if isinstance(inventory, list) and outcome.lost_item_index < len(inventory):
            inventory.pop(outcome.lost_item_index)

    if outcome.resource_changes:
        resources = world.setdefault("resources", {})
        for name, delta in outcome.resource_changes.items():
            resources[name] = max(0, int(_numeric(resources.get(name))) + delta)
    return updated


def _player_from(world: dict[str, Any], player_id: str) -> dict[str, Any] | None:
    players = world.get("players", [])
    if isinstance(players, dict):
        player = players.get(player_id)
    elif isinstance(players, list):
        player = next(
            (
                record for record in players
                if isinstance(record, dict) and str(record.get("id")) == player_id
            ),
            None,
        )
    else:
        player = None
    return player if isinstance(player, dict) else None
