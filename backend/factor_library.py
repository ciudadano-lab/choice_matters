from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from action_registry import get_action_chain_map
from probability_config import (
    APPROACH_FIT_BANDS,
    DEFAULT_CURRENT_DAY,
    DEFAULT_CURRENT_TIME,
    TIME_BANDS,
)
from location_lookup import resolve_location_id
from plan_features import PlanFeatures


CONTENT_DIR = Path(__file__).resolve().parent / "content"
STEP_KINDS = frozenset({"gate", "encounter"})
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LinearCalc(StrictModel):
    type: Literal["linear"]
    input: str
    per_unit: float
    unit_size: float
    floor: StrictBool
    clamp_min: float | None = None
    max_units: float | None = None

    @model_validator(mode="after")
    def validate_numbers(self) -> LinearCalc:
        _require_finite("per_unit", self.per_unit)
        if self.per_unit < 0.0:
            raise ValueError("per_unit must be non-negative")
        _require_finite("unit_size", self.unit_size)
        if self.unit_size <= 0.0:
            raise ValueError("unit_size must be greater than zero")
        if self.floor and int(self.unit_size) <= 0:
            raise ValueError("unit_size must be at least one when floor is true")
        if self.clamp_min is not None:
            _require_finite("clamp_min", self.clamp_min)
        if self.max_units is not None:
            _require_finite("max_units", self.max_units)
            if self.max_units < 0.0:
                raise ValueError("max_units must be non-negative")
        return self


class ThresholdCalc(StrictModel):
    type: Literal["threshold"]
    input: str
    op: Literal["eq", "in", "gte", "lte", "gt", "lt"]
    value: Any
    delta: float

    @field_validator("value", mode="before")
    @classmethod
    def freeze_value_list(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_numbers_and_shape(self) -> ThresholdCalc:
        _require_finite("delta", self.delta)
        if self.delta < 0.0:
            raise ValueError("delta must be non-negative")
        if self.op == "in":
            if not isinstance(self.value, tuple):
                raise ValueError("value must be a list when op is 'in'")
            for item in self.value:
                _validate_finite_scalar(item, "in value")
        elif self.op in {"gte", "lte", "gt", "lt"}:
            _validate_finite_number(self.value, "comparison value")
        elif isinstance(self.value, (dict, list)):
            raise ValueError("value for 'eq' must be a scalar")
        else:
            _validate_finite_scalar(self.value, "comparison value")
        return self


class FlagCalc(StrictModel):
    type: Literal["flag"]

    @model_validator(mode="after")
    def unsupported(self) -> FlagCalc:
        raise NotImplementedError("flag calc is not yet supported")


class TableCalc(StrictModel):
    type: Literal["table"]

    @model_validator(mode="after")
    def unsupported(self) -> TableCalc:
        raise NotImplementedError("table calc is not yet supported")


Calc = Annotated[
    Union[LinearCalc, ThresholdCalc, FlagCalc, TableCalc],
    Field(discriminator="type"),
]


class AppliesTo(StrictModel):
    step_kinds: tuple[Literal["gate", "encounter"], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_steps(self) -> AppliesTo:
        if len(set(self.step_kinds)) != len(self.step_kinds):
            raise ValueError("step_kinds must not contain duplicates")
        return self


class Factor(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    log_source: str | None = None
    order: StrictInt
    label: str = Field(min_length=1)
    category: str
    kind: Literal["situation", "plan"]
    role: Literal["modifier", "counter"]
    applies_to: AppliesTo
    calc: Calc
    cap: float | None = None
    explanation: str = Field(min_length=1)
    status: Literal["live", "draft"]
    source: Literal["human", "adjudicator", "playtest"]
    notes: str = ""

    @model_validator(mode="after")
    def validate_values(self) -> Factor:
        if not _SNAKE_CASE.fullmatch(self.id):
            raise ValueError("id must be snake_case")
        if not self.label.strip():
            raise ValueError("label must be non-empty")
        if not self.explanation.strip():
            raise ValueError("explanation must be non-empty")
        if self.log_source is not None and not self.log_source.strip():
            raise ValueError("log_source must be non-empty when supplied")
        if self.cap is not None:
            _require_finite("cap", self.cap)
            if self.cap < 0.0:
                raise ValueError("cap must be non-negative")
        return self


class ChainStep(StrictModel):
    name: str = Field(min_length=1)
    kind: Literal["gate", "encounter"]
    base: float
    interrupt: StrictBool
    requires: str | None

    @model_validator(mode="after")
    def validate_base(self) -> ChainStep:
        _require_finite("base", self.base)
        if not 0.0 < self.base < 1:
            raise ValueError("base must be between zero and one")
        return self


class ChainTemplate(StrictModel):
    steps: tuple[ChainStep, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_step_order(self) -> ChainTemplate:
        seen: set[str] = set()
        for step in self.steps:
            if step.name in seen:
                raise ValueError(f"duplicate step name {step.name!r}")
            if step.requires is not None and step.requires not in seen:
                raise ValueError(
                    f"step {step.name!r} requires unknown or later step {step.requires!r}"
                )
            seen.add(step.name)
        return self


class AmbientNightConfig(StrictModel):
    enabled: StrictBool = True
    chain_template: str


class ChainFile(StrictModel):
    templates: dict[str, ChainTemplate]
    actions: dict[str, str] = Field(default_factory=dict)
    ambient_night: AmbientNightConfig | None = None


@dataclass(frozen=True)
class InputContext:
    actor: dict[str, Any] | None
    location: dict[str, Any] | None
    day: Any
    time_str: str
    plan_features: PlanFeatures | None = None


InputFunction = Callable[[dict[str, Any], Any, InputContext], int | float | str | bool | None]


def _finite_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _get_player(players: Any, actor_id: str) -> dict[str, Any] | None:
    if isinstance(players, dict):
        player = players.get(actor_id)
        return player if isinstance(player, dict) else None
    if isinstance(players, list):
        return next(
            (
                player
                for player in players
                if isinstance(player, dict) and player.get("id") == actor_id
            ),
            None,
        )
    return None


def _get_action_location(
    locations: Any,
    target: Any,
    actor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    state = {"locations": locations}
    location_id = resolve_location_id(state, target)
    if location_id is None and actor is not None:
        location_id = resolve_location_id(state, actor.get("location"))
    if location_id is None:
        return None
    if isinstance(locations, list):
        return next(
            (
                item for item in locations
                if isinstance(item, dict) and item.get("id") == location_id
            ),
            None,
        )
    if isinstance(locations, dict):
        location = locations.get(location_id)
        if isinstance(location, dict):
            return location
        return next(
            (
                item for item in locations.values()
                if isinstance(item, dict) and item.get("id") == location_id
            ),
            None,
        )
    return None


def _is_active_status_effect(effect: Any, day: Any) -> bool:
    if not isinstance(effect, dict):
        return False
    expires_day = effect.get("expiresDay")
    if expires_day is None:
        return True
    try:
        return int(expires_day) > int(day)
    except (TypeError, ValueError):
        return True


def _input_location_danger(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    if ctx.location is None:
        return None
    return _finite_number(ctx.location.get("dangerLevel", 0))


def _input_location_fortified(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    if ctx.location is None:
        return None
    flags = ctx.location.get("flags", {})
    if not isinstance(flags, dict) or flags.get("fortified") not in (True, 1):
        return 0.0
    expiries = ctx.location.get("flagExpiries", {})
    expires_day = expiries.get("fortified") if isinstance(expiries, dict) else None
    try:
        if expires_day is not None and int(ctx.day) >= int(expires_day):
            return 0.0
    except (TypeError, ValueError):
        pass
    return 1.0


def _input_zombie_population(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int | None:
    if ctx.location is None:
        return None
    return int(_finite_number(ctx.location.get("zombiePopulation", 0)))


def _input_clock_hour(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int | None:
    # Missing time historically defaults to midnight, which is part of the golden behavior.
    time_str = state.get("currentTime", DEFAULT_CURRENT_TIME)
    if not isinstance(time_str, str):
        return None
    hour_text, separator, _ = time_str.partition(":")
    try:
        return int(hour_text)
    except ValueError:
        return None


def _input_weather(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> str | None:
    weather = state.get("weather", "clear")
    return weather if isinstance(weather, str) else None


def _input_action_type(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> str:
    return action.actionType


def _input_stealth_intent(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> bool:
    parameters = action.parameters if isinstance(action.parameters, dict) else {}
    policy = parameters.get("policy", {})
    focus = policy.get("focus", "") if isinstance(policy, dict) else ""
    return isinstance(focus, str) and any(
        phrase in focus.casefold() for phrase in ("stealth", "sneak", "quiet")
    )


def _state_value(state: Any, key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def known_location_ids(
    state: Any,
    actor: dict[str, Any] | None,
) -> set[str]:
    locations = _state_value(state, "locations", [])
    if isinstance(locations, dict):
        records = [
            {"id": location_id, **location}
            for location_id, location in locations.items()
            if isinstance(location, dict)
        ]
    elif isinstance(locations, list):
        records = [location for location in locations if isinstance(location, dict)]
    else:
        records = []

    valid_ids = {
        record["id"].strip().casefold()
        for record in records
        if isinstance(record.get("id"), str)
    }
    known_ids: set[str] = set()
    known_ids.update(
        record["id"].strip().casefold()
        for record in records
        if isinstance(record.get("id"), str)
        and record.get("discovered") is True
    )
    known_world = _state_value(state, "playerKnownWorld")
    known_locations = (
        known_world.get("locations", [])
        if isinstance(known_world, dict)
        else []
    )
    if isinstance(known_locations, list):
        known_ids.update(
            location["id"].strip().casefold()
            for location in known_locations
            if isinstance(location, dict) and isinstance(location.get("id"), str)
        )

    return known_ids & valid_ids


def owned_item_ids(
    state: Any,
    actor: dict[str, Any] | None,
) -> set[str]:
    owned: set[str] = set()

    def add_item(name: Any) -> None:
        if (
            isinstance(name, str)
            and name.strip()
        ):
            owned.add(" ".join(name.replace("_", " ").casefold().split()))

    def add_entries(entries: Any) -> None:
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str):
                    add_item(entry)
                elif isinstance(entry, dict):
                    add_item(entry.get("name"))

    if actor is not None:
        add_entries(actor.get("inventory", []))
        entities = actor.get("entities")
        if isinstance(entities, dict):
            add_entries(entities.get("possessions", []))

    resources = _state_value(state, "resources")
    if isinstance(resources, dict):
        for name, quantity in resources.items():
            if (
                isinstance(quantity, (int, float))
                and not isinstance(quantity, bool)
                and math.isfinite(float(quantity))
                and quantity > 0
            ):
                add_item(name)
    return owned


def _plan_ratio(
    values: list[str] | None,
    qualifies: Callable[[Any], bool],
) -> float | None:
    if not values:
        return None
    return sum(qualifies(value) for value in values) / len(values)


def _input_plan_known_location_ratio(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    known_ids = known_location_ids(state, ctx.actor)
    return _plan_ratio(
        ctx.plan_features.targetLocationIds if ctx.plan_features else None,
        lambda value: value.casefold() in known_ids,
    )


def _input_plan_unknown_location_ratio(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    ratio = _input_plan_known_location_ratio(state, action, ctx)
    return 1.0 - ratio if ratio is not None else None


def _input_plan_owned_item_ratio(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    owned_items = owned_item_ids(state, ctx.actor)
    return _plan_ratio(
        ctx.plan_features.itemsPlanned if ctx.plan_features else None,
        lambda value: " ".join(
            value.replace("_", " ").casefold().split()
        ) in owned_items,
    )


def _input_plan_missing_item_ratio(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> float | None:
    ratio = _input_plan_owned_item_ratio(state, action, ctx)
    return 1.0 - ratio if ratio is not None else None


def _input_plan_has_fallback(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> bool | None:
    return ctx.plan_features.hasFallback if ctx.plan_features else None


def _input_plan_countermeasure_count(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int | None:
    return (
        len(ctx.plan_features.countermeasures)
        if ctx.plan_features is not None
        else None
    )


def _clock_time_band(time_value: Any) -> str | None:
    if not isinstance(time_value, str):
        return None
    parts = time_value.strip().split(":")
    if (
        len(parts) != 2
        or not parts[0].isdigit()
        or not parts[1].isdigit()
        or len(parts[1]) != 2
    ):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    minute_of_day = hour * 60 + minute
    for band, intervals in TIME_BANDS.items():
        if any(start <= minute_of_day <= end for start, end in intervals):
            return band
    return None


def _plan_time_band(state: dict[str, Any], ctx: InputContext) -> str | None:
    timing = ctx.plan_features.timing if ctx.plan_features is not None else None
    if timing is None or timing == "unspecified":
        return _clock_time_band(state.get("currentTime"))
    return timing if timing in TIME_BANDS else None


def _plan_approach_fit(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> bool | None:
    approach_name = (
        ctx.plan_features.approach
        if ctx.plan_features is not None
        else "unspecified"
    )
    if approach_name == "unspecified" or approach_name not in APPROACH_FIT_BANDS:
        return None

    timing_band = _plan_time_band(state, ctx)
    if timing_band is None:
        return None

    rules = APPROACH_FIT_BANDS.get(approach_name)
    if rules is None:
        return None
    weather = state.get("weather", "clear")
    weather_name = weather.strip().casefold() if isinstance(weather, str) else "clear"
    return (
        timing_band in rules["bands"]
        or weather_name in rules["extra_weather"]
    )


def _input_plan_approach_fits_conditions(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> bool | None:
    return _plan_approach_fit(state, action, ctx)


def _input_plan_approach_mismatch(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> bool | None:
    fits = _plan_approach_fit(state, action, ctx)
    return not fits if fits is not None else None


def _input_actor_stress(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int | None:
    if ctx.actor is None:
        return None
    return int(max(0, _finite_number(ctx.actor.get("stress", 0))))


def _input_active_status_effect_count(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int | None:
    if ctx.actor is None:
        return None
    effects = ctx.actor.get("statusEffects", [])
    if not isinstance(effects, list):
        return 0
    return sum(_is_active_status_effect(effect, ctx.day) for effect in effects)


def _input_companion_count(
    state: dict[str, Any], action: Any, ctx: InputContext
) -> int:
    entities = ctx.actor.get("entities", {}) if ctx.actor else {}
    companions = entities.get("companions", []) if isinstance(entities, dict) else []
    if not isinstance(companions, list):
        return 0
    return sum(companion is not None for companion in companions)


INPUT_REGISTRY: Mapping[str, InputFunction] = MappingProxyType({
    "location.dangerLevel": _input_location_danger,
    "location.fortified": _input_location_fortified,
    "location.zombiePopulation": _input_zombie_population,
    "clock.hour": _input_clock_hour,
    "world.weather": _input_weather,
    "action.type": _input_action_type,
    "action.stealth_intent": _input_stealth_intent,
    "plan.known_location_ratio": _input_plan_known_location_ratio,
    "plan.unknown_location_ratio": _input_plan_unknown_location_ratio,
    "plan.owned_item_ratio": _input_plan_owned_item_ratio,
    "plan.missing_item_ratio": _input_plan_missing_item_ratio,
    "plan.has_fallback": _input_plan_has_fallback,
    "plan.countermeasure_count": _input_plan_countermeasure_count,
    "plan.approach_fits_conditions": _input_plan_approach_fits_conditions,
    "plan.approach_mismatch": _input_plan_approach_mismatch,
    "actor.stress": _input_actor_stress,
    "actor.active_status_effect_count": _input_active_status_effect_count,
    "actor.companion_count": _input_companion_count,
})
_NUMERIC_INPUTS = frozenset({
    "location.dangerLevel",
    "location.fortified",
    "location.zombiePopulation",
    "clock.hour",
    "actor.stress",
    "actor.active_status_effect_count",
    "actor.companion_count",
    "plan.known_location_ratio",
    "plan.unknown_location_ratio",
    "plan.owned_item_ratio",
    "plan.missing_item_ratio",
    "plan.countermeasure_count",
})
_INPUT_TYPES = {
    **{name: "number" for name in _NUMERIC_INPUTS},
    "world.weather": "string",
    "action.type": "string",
    "action.stealth_intent": "bool",
    "plan.has_fallback": "bool",
    "plan.approach_fits_conditions": "bool",
    "plan.approach_mismatch": "bool",
}


@dataclass(frozen=True)
class FactorLibrary:
    factors: tuple[Factor, ...]
    templates: Mapping[str, ChainTemplate]
    actions: Mapping[str, str]
    ambient_night: AmbientNightConfig | None = None

    def template_for(self, action_type: str) -> ChainTemplate | None:
        template_name = self.actions.get(action_type)
        return self.templates.get(template_name) if template_name is not None else None

    def make_context(
        self,
        state: dict[str, Any],
        action: Any,
        plan_features: PlanFeatures | None = None,
    ) -> InputContext:
        players = state.get("players", [])
        actor = _get_player(players, action.actor)
        locations = state.get("locations", [])
        location = _get_action_location(locations, action.target, actor)
        day = state.get("currentDay", DEFAULT_CURRENT_DAY)
        time_value = state.get("currentTime", DEFAULT_CURRENT_TIME)
        time_str = time_value if isinstance(time_value, str) else DEFAULT_CURRENT_TIME
        return InputContext(
            actor=actor,
            location=location,
            day=day,
            time_str=time_str,
            plan_features=plan_features,
        )

    def evaluate_factors(
        self,
        state: dict[str, Any],
        action: Any,
        ctx: InputContext,
        step_kind: str,
    ) -> list[tuple[Factor, float]]:
        evaluated = []
        for factor in self.factors:
            if factor.status != "live" or step_kind not in factor.applies_to.step_kinds:
                continue
            if factor.kind == "plan" and ctx.plan_features is None:
                continue
            value = INPUT_REGISTRY[factor.calc.input](state, action, ctx)
            if value is None:
                continue
            delta = _evaluate_calc(factor, value)
            if delta != 0.0:
                evaluated.append((factor, delta))
        return evaluated


def _evaluate_calc(factor: Factor, value: int | float | str | bool) -> float:
    calc = factor.calc
    if isinstance(calc, LinearCalc):
        numeric_value = float(value)
        if calc.clamp_min is not None:
            numeric_value = max(numeric_value, calc.clamp_min)
        if calc.floor:
            units = int(numeric_value) // int(calc.unit_size)
        else:
            units = numeric_value / calc.unit_size
        if calc.max_units is not None:
            units = min(units, calc.max_units)
        delta = units * calc.per_unit
        if factor.cap is not None:
            delta = min(delta, factor.cap)
        return delta

    if isinstance(calc, ThresholdCalc):
        matches = _threshold_matches(value, calc.op, calc.value)
        return calc.delta if matches else 0.0

    raise NotImplementedError(f"{calc.type} calc is not yet supported")


def _threshold_matches(value: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return _compare_equal(value, expected)
    if op == "in":
        return any(_compare_equal(value, item) for item in expected)
    if op == "gte":
        return value >= expected
    if op == "lte":
        return value <= expected
    if op == "gt":
        return value > expected
    return value < expected


def _compare_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _validate_finite_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")


def _validate_finite_scalar(value: Any, label: str) -> None:
    if isinstance(value, bool | str):
        return
    if isinstance(value, (int, float)):
        _validate_finite_number(value, label)
        return
    raise ValueError(f"{label} must be a finite number, string, or boolean")


def _validate_factor_calc(factor: Factor, path: Path) -> None:
    calc = factor.calc
    if calc.input not in INPUT_REGISTRY:
        raise ValueError(f"{path}: factor {factor.id}: unknown input {calc.input!r}")
    if factor.kind == "plan" and not calc.input.startswith("plan."):
        raise ValueError(
            f"{path}: factor {factor.id}: plan factors must use plan.* inputs"
        )
    if factor.kind == "situation" and calc.input.startswith("plan."):
        raise ValueError(
            f"{path}: factor {factor.id}: situation factors must not use plan.* inputs"
        )
    if isinstance(calc, LinearCalc):
        if calc.input not in _NUMERIC_INPUTS:
            raise ValueError(
                f"{path}: factor {factor.id}: linear input {calc.input!r} is not numeric"
            )
        return
    if isinstance(calc, ThresholdCalc):
        input_type = _INPUT_TYPES[calc.input]
        values = calc.value if calc.op == "in" else [calc.value]
        for value in values:
            if input_type == "number":
                _validate_finite_number(
                    value,
                    f"{path}: factor {factor.id}: threshold value",
                )
            elif input_type == "string" and not isinstance(value, str):
                raise ValueError(
                    f"{path}: factor {factor.id}: threshold value must be a string"
                )
            elif input_type == "bool" and not isinstance(value, bool):
                raise ValueError(
                    f"{path}: factor {factor.id}: threshold value must be boolean"
                )


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def load_library(content_dir: str | Path) -> FactorLibrary:
    root = Path(content_dir).resolve()
    factor_dir = root / "factors"
    if not factor_dir.is_dir():
        raise ValueError(f"{factor_dir}: factors directory does not exist")

    factors = []
    factor_ids: set[str] = set()
    factor_files = sorted(factor_dir.glob("*.json"), key=lambda path: path.name)
    if not factor_files:
        raise ValueError(f"{factor_dir}: no factor files found")
    for path in factor_files:
        raw_factors = _read_json(path)
        if not isinstance(raw_factors, list):
            raise ValueError(f"{path}: expected a JSON array of factors")
        for raw_factor in raw_factors:
            factor_id = (
                raw_factor.get("id", "<missing id>")
                if isinstance(raw_factor, dict)
                else "<invalid factor>"
            )
            try:
                factor = Factor.model_validate(raw_factor)
            except NotImplementedError as exc:
                raise NotImplementedError(
                    f"{path}: factor {factor_id}: {exc}"
                ) from exc
            except ValidationError as exc:
                raise ValueError(
                    f"{path}: factor {factor_id}: {exc}"
                ) from exc
            if factor.id in factor_ids:
                raise ValueError(f"{path}: factor {factor.id}: duplicate factor id")
            factor_ids.add(factor.id)
            if factor.category != path.stem:
                raise ValueError(
                    f"{path}: factor {factor.id}: category must be {path.stem!r}"
                )
            _validate_factor_calc(factor, path)
            factors.append(factor)

    chains_path = root / "chains.json"
    raw_chains = _read_json(chains_path)
    try:
        chain_file = ChainFile.model_validate(raw_chains)
    except ValidationError as exc:
        chain_id = _chain_id_from_error(exc)
        raise ValueError(f"{chains_path}: chain {chain_id}: {exc}") from exc
    action_chain_map = (
        get_action_chain_map(root / "actions")
        if (root / "actions").is_dir()
        else chain_file.actions
    )
    for action_type, template_name in action_chain_map.items():
        if template_name not in chain_file.templates:
            raise ValueError(
                f"{chains_path}: action {action_type}: unknown chain template {template_name!r}"
            )
    if (
        chain_file.ambient_night is not None
        and chain_file.ambient_night.chain_template not in chain_file.templates
    ):
        raise ValueError(
            f"{chains_path}: ambient_night references unknown chain template "
            f"{chain_file.ambient_night.chain_template!r}"
        )

    ordered_factors = tuple(sorted(factors, key=lambda factor: (factor.order, factor.id)))
    templates = MappingProxyType(dict(chain_file.templates))
    actions = MappingProxyType(dict(action_chain_map))
    return FactorLibrary(
        factors=ordered_factors,
        templates=templates,
        actions=actions,
        ambient_night=chain_file.ambient_night,
    )


def _chain_id_from_error(exc: ValidationError) -> str:
    for error in exc.errors():
        location = error.get("loc", ())
        if len(location) >= 2 and location[0] == "templates":
            return str(location[1])
        if len(location) >= 2 and location[0] == "actions":
            return str(location[1])
    return "<chain definitions>"


@lru_cache(maxsize=8)
def _cached_library(content_path: str) -> FactorLibrary:
    return load_library(content_path)


def get_library(content_dir: str | Path | None = None) -> FactorLibrary:
    path = CONTENT_DIR if content_dir is None else Path(content_dir)
    return _cached_library(str(path.resolve()))

