from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


_TIMINGS = frozenset({"dawn", "day", "dusk", "night", "unspecified"})
_APPROACHES = frozenset({
    "stealth",
    "force",
    "speed",
    "deception",
    "diplomacy",
    "unspecified",
})


def _item_key(value: str) -> str:
    return " ".join(value.replace("_", " ").casefold().split())


def _clean_strings(
    value: Any,
    *,
    max_items: int,
    max_length: int,
    item_names: bool = False,
) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in values:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if not text or len(text) > max_length:
            continue
        key = _item_key(text) if item_names else text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) == max_items:
            break
    return cleaned


class PlanFeatures(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    targetLocationIds: list[str] = Field(default_factory=list, max_length=5)
    peopleMentioned: list[str] = Field(default_factory=list, max_length=5)
    itemsPlanned: list[str] = Field(default_factory=list, max_length=6)
    timing: Literal["dawn", "day", "dusk", "night", "unspecified"] = "unspecified"
    approach: Literal[
        "stealth", "force", "speed", "deception", "diplomacy", "unspecified"
    ] = "unspecified"
    countermeasures: list[str] = Field(default_factory=list, max_length=4)
    hasFallback: bool = False

    @field_validator("targetLocationIds", mode="before")
    @classmethod
    def clean_target_locations(cls, value: Any) -> list[str]:
        return _clean_strings(value, max_items=5, max_length=80)

    @field_validator("peopleMentioned", mode="before")
    @classmethod
    def clean_people(cls, value: Any) -> list[str]:
        return _clean_strings(value, max_items=5, max_length=60)

    @field_validator("itemsPlanned", mode="before")
    @classmethod
    def clean_items(cls, value: Any) -> list[str]:
        return _clean_strings(
            value,
            max_items=6,
            max_length=40,
            item_names=True,
        )

    @field_validator("countermeasures", mode="before")
    @classmethod
    def clean_countermeasures(cls, value: Any) -> list[str]:
        return _clean_strings(value, max_items=4, max_length=40)

    @field_validator("timing", mode="before")
    @classmethod
    def normalize_timing(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "unspecified"
        timing = value.strip().casefold()
        return timing if timing in _TIMINGS else "unspecified"

    @field_validator("approach", mode="before")
    @classmethod
    def normalize_approach(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "unspecified"
        approach = value.strip().casefold()
        return approach if approach in _APPROACHES else "unspecified"

    @field_validator("hasFallback", mode="before")
    @classmethod
    def require_json_boolean(cls, value: Any) -> bool:
        return value is True

    @classmethod
    def from_untrusted(cls, value: Any) -> PlanFeatures | None:
        if isinstance(value, cls):
            value = value.to_dict()
        if not isinstance(value, dict):
            return None
        try:
            features = cls.model_validate(value)
        except (ValidationError, TypeError, ValueError):
            return None
        if not (
            features.targetLocationIds
            or features.peopleMentioned
            or features.itemsPlanned
            or features.timing != "unspecified"
            or features.approach != "unspecified"
            or features.countermeasures
            or features.hasFallback
        ):
            return None
        return features

    def to_dict(self) -> dict[str, Any]:
        return {
            "targetLocationIds": list(self.targetLocationIds),
            "peopleMentioned": list(self.peopleMentioned),
            "itemsPlanned": list(self.itemsPlanned),
            "timing": self.timing,
            "approach": self.approach,
            "countermeasures": list(self.countermeasures),
            "hasFallback": self.hasFallback,
        }
