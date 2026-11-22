from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any

from factor_library import FactorLibrary, get_library
from probability_config import (
    CHANCE_MAX,
    CHANCE_MIN,
    DEFAULT_PLAN_QUALITY,
    LEGACY_PLAN_MODIFIER_CAP,
    PLAN_MODIFIER_CAP,
    PLAN_QUALITY_MAX,
    PLAN_QUALITY_MIN,
    ROLL_HASH_BASE,
    ROLL_HASH_PREFIX_HEX_LENGTH,
    ROLL_VALUE_SCALE,
    DEFAULT_CURRENT_DAY,
    DEFAULT_CURRENT_TIME,
)
from plan_features import PlanFeatures


@dataclass
class ProbabilityStep:
    name: str
    kind: str
    interrupt: bool
    base: float
    modifiers: list[dict[str, Any]]
    counters: list[dict[str, Any]]
    chance: float
    roll: float | None
    triggered: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "interrupt": self.interrupt,
            "base": round(self.base, 4),
            "modifiers": [
                {**item, "delta": round(item["delta"], 4)}
                for item in self.modifiers
            ],
            "counters": [
                {**item, "delta": round(item["delta"], 4)}
                for item in self.counters
            ],
            "chance": round(self.chance, 4),
            "roll": round(self.roll, 6) if self.roll is not None else None,
            "triggered": self.triggered,
            "status": self.status,
        }


@dataclass
class ChainResult:
    steps: list[ProbabilityStep]
    interrupt_step: str | None
    encounter_probability: float
    encounter_occurred: bool
    encounter_roll: float | None
    plan_quality: float = 0.0
    plan_reasons: list[dict[str, str]] = field(default_factory=list)


def roll_value(
    seed: int,
    action_id: str,
    day: int,
    time_str: str,
    step_name: str,
    step_index: int,
) -> float:
    material = f"{seed}:{action_id}:{day}:{time_str}:{step_name}:{step_index}"
    digest_prefix = hashlib.sha256(material.encode("utf-8")).hexdigest()[
        :ROLL_HASH_PREFIX_HEX_LENGTH
    ]
    return int(digest_prefix, ROLL_HASH_BASE) / ROLL_VALUE_SCALE


def run_chain(
    state: dict[str, Any],
    action: Any,
    seed: int,
    plan_quality: float = DEFAULT_PLAN_QUALITY,
    library: FactorLibrary | None = None,
    plan_features: PlanFeatures | None = None,
) -> ChainResult:
    active_library = library if library is not None else _DEFAULT_LIBRARY
    template = active_library.template_for(action.actionType)
    if template is None:
        return ChainResult([], None, 0.0, False, None)

    day = state.get("currentDay", DEFAULT_CURRENT_DAY)
    time_str = state.get("currentTime", DEFAULT_CURRENT_TIME)
    if not isinstance(time_str, str):
        time_str = DEFAULT_CURRENT_TIME
    clean_plan_features = PlanFeatures.from_untrusted(plan_features)
    legacy_quality = _bounded_plan_quality(plan_quality)
    context = active_library.make_context(state, action, clean_plan_features)

    steps: list[ProbabilityStep] = []
    triggered_by_name: dict[str, bool] = {}
    plan_reasons: list[dict[str, str]] = []
    seen_plan_factor_ids: set[str] = set()
    interrupt_step = None
    encounter_probability = 0.0
    encounter_occurred = False
    encounter_roll = None
    derived_plan_quality = 0.0

    for step_index, spec in enumerate(template.steps):
        modifiers = []
        counters = []
        plan_modifiers: list[dict[str, Any]] = []
        plan_counters: list[dict[str, Any]] = []
        for factor, delta in active_library.evaluate_factors(
            state,
            action,
            context,
            spec.kind,
        ):
            entry = {
                "source": factor.log_source or factor.id,
                "delta": delta,
            }
            if factor.role == "modifier":
                modifiers.append(entry)
                if factor.kind == "plan":
                    plan_modifiers.append(entry)
            else:
                counters.append(entry)
                if factor.kind == "plan":
                    plan_counters.append(entry)
            if (
                factor.kind == "plan"
                and factor.id not in seen_plan_factor_ids
            ):
                direction = "helps" if factor.role == "counter" else "hurts"
                plan_reasons.append({
                    "factor": factor.id,
                    "effect": direction,
                    "explanation": factor.explanation,
                })
                seen_plan_factor_ids.add(factor.id)

        step_plan_effect = (
            sum(entry["delta"] for entry in plan_modifiers)
            - sum(entry["delta"] for entry in plan_counters)
        )
        if clean_plan_features is not None:
            if step_plan_effect > PLAN_MODIFIER_CAP:
                counters.append({
                    "source": "plan_cap",
                    "delta": step_plan_effect - PLAN_MODIFIER_CAP,
                })
                step_plan_effect = PLAN_MODIFIER_CAP
            elif step_plan_effect < -PLAN_MODIFIER_CAP:
                modifiers.append({
                    "source": "plan_cap",
                    "delta": -PLAN_MODIFIER_CAP - step_plan_effect,
                })
                step_plan_effect = -PLAN_MODIFIER_CAP
            if spec.kind == "encounter" and PLAN_MODIFIER_CAP > 0.0:
                derived_plan_quality = max(
                    PLAN_QUALITY_MIN,
                    min(PLAN_QUALITY_MAX, -step_plan_effect / PLAN_MODIFIER_CAP),
                )
        elif legacy_quality != 0.0:
            modifiers.append({
                "source": "plan",
                "delta": -legacy_quality * LEGACY_PLAN_MODIFIER_CAP,
            })

        chance = _clamp_chance(
            spec.base
            + sum(item["delta"] for item in modifiers)
            - sum(item["delta"] for item in counters)
        )
        skipped = (
            spec.requires is not None
            and not triggered_by_name.get(spec.requires, False)
        )
        roll = None if skipped else roll_value(
            seed,
            str(action.actionId),
            day,
            time_str,
            spec.name,
            step_index,
        )
        triggered = False if skipped else bool(roll is not None and roll < chance)
        step = ProbabilityStep(
            name=spec.name,
            kind=spec.kind,
            interrupt=spec.interrupt,
            base=spec.base,
            modifiers=modifiers,
            counters=counters,
            chance=chance,
            roll=roll,
            triggered=triggered,
            status="skipped" if skipped else "rolled",
        )
        steps.append(step)
        triggered_by_name[spec.name] = triggered

        if triggered and step.interrupt and interrupt_step is None:
            interrupt_step = step.name
        if spec.kind == "encounter" and not skipped:
            encounter_probability = chance
            encounter_roll = roll
            encounter_occurred = triggered

    return ChainResult(
        steps=steps,
        interrupt_step=interrupt_step,
        encounter_probability=encounter_probability,
        encounter_occurred=encounter_occurred,
        encounter_roll=encounter_roll,
        plan_quality=derived_plan_quality,
        plan_reasons=plan_reasons,
    )


def _bounded_plan_quality(value: float) -> float:
    try:
        quality = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(quality):
        return 0.0
    return max(PLAN_QUALITY_MIN, min(PLAN_QUALITY_MAX, quality))


def _clamp_chance(chance: float) -> float:
    return max(CHANCE_MIN, min(CHANCE_MAX, chance))


_DEFAULT_LIBRARY = get_library()
