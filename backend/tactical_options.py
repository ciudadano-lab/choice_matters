from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from action_registry import EffectRecord


CONTENT_PATH = Path(__file__).resolve().parent / "content" / "tactical_options.json"
_TACTICAL_EFFECT_TYPES = frozenset({
    "change_location_stat",
    "change_resource",
    "change_stat",
    "discover_information",
    "remove_item",
    "set_flag",
})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TacticalModifier(StrictModel):
    id: str
    input: str | None = None
    per_unit: float | None = None
    max_units: float | None = Field(default=None, ge=0)
    condition: Literal["lte", "gte"] | None = None
    threshold: float | None = None
    delta: float | None = None
    explanation: str

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str | None) -> str | None:
        allowed = {
            "plan_quality",
            "location.dangerLevel",
            "location.noise",
            "player.companion_count",
            "player.health",
            "player.hunger",
            "player.stamina",
            "player.stress",
            "player.thirst",
        }
        if value is not None and value not in allowed:
            raise ValueError(f"unsupported tactical modifier input {value!r}")
        return value

    @field_validator("per_unit", "max_units", "threshold", "delta")
    @classmethod
    def validate_finite_number(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("modifier values must be finite")
        return value

    @model_validator(mode="after")
    def validate_modifier(self) -> TacticalModifier:
        if self.condition is not None:
            if self.input is None or self.threshold is None or self.delta is None:
                raise ValueError("conditional modifiers require input, threshold, and delta")
        elif self.input is not None and self.per_unit is None:
            raise ValueError("per-unit modifiers require input and per_unit")
        elif self.input is None and self.delta is None:
            raise ValueError("modifiers require delta or input and per_unit")
        return self


class TacticalRequirements(StrictModel):
    items: tuple[str, ...] = ()
    stat_minimums: dict[str, StrictInt] = Field(default_factory=dict)
    location_flags: tuple[str, ...] = ()


class TacticalCosts(StrictModel):
    stamina: StrictInt = Field(default=0, ge=0)
    items: tuple[str, ...] = ()
    noise: StrictInt = Field(default=0, ge=0)


class TacticalOption(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    description: str
    requirements: TacticalRequirements
    costs: TacticalCosts
    modifiers: tuple[TacticalModifier, ...] = ()
    follow_up_effects: tuple[EffectRecord, ...] = ()
    status: Literal["live", "draft"]

    @model_validator(mode="after")
    def validate_effects(self) -> TacticalOption:
        unknown = {
            effect.type
            for effect in self.follow_up_effects
            if effect.type not in _TACTICAL_EFFECT_TYPES
        }
        if unknown:
            raise ValueError(
                f"unsupported tactical follow-up effects: {', '.join(sorted(unknown))}"
            )
        return self


class TacticalOptionsContent(StrictModel):
    default_option: str
    max_location_noise: StrictInt = Field(gt=0)
    options: tuple[TacticalOption, ...]

    @model_validator(mode="after")
    def validate_options(self) -> TacticalOptionsContent:
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("tactical option ids must be unique")
        options_by_id = {option.id: option for option in self.options}
        default = options_by_id.get(self.default_option)
        if default is None or default.status != "live":
            raise ValueError("default_option must name a live tactical option")
        if default.requirements != TacticalRequirements() or default.costs != TacticalCosts():
            raise ValueError("default tactical option must be available without cost")
        return self


@lru_cache(maxsize=1)
def load_tactical_options() -> TacticalOptionsContent:
    with CONTENT_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return TacticalOptionsContent.model_validate(payload)
