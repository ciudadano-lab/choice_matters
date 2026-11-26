from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

BACKEND_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BACKEND_DIR / "content"
ACTIONS_DIR = CONTENT_DIR / "actions"
EFFECT_HANDLER_NAMES = {
    "change_resource": "_effect_change_resource",
    "change_stat": "_effect_change_stat",
    "move": "_effect_move",
    "add_status": "_effect_status",
    "remove_status": "_effect_status",
    "add_item": "_effect_inventory",
    "remove_item": "_effect_inventory",
    "set_flag": "_effect_set_flag",
    "change_faction_disposition": "_effect_change_faction_disposition",
    "change_settlement_stat": "_effect_change_settlement_stat",
    "change_npc_loyalty": "_effect_change_npc_loyalty",
}
VALID_EFFECT_TYPES = frozenset(EFFECT_HANDLER_NAMES)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EffectRecord(StrictModel):
    type: str
    rules: Literal["all", "legacy", "current"] = "all"
    target: str | None = None
    resource: str | None = None
    stat: str | None = None
    amount: int | float | None = None
    value: int | float | None = None
    status: str | None = None
    item: str | None = None
    flag: str | None = None
    location: str | None = None
    faction_id: str | None = None
    player_id: str | None = None
    settlement_id: str | None = None
    npc_id: str | None = None
    duration_days: StrictInt | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in VALID_EFFECT_TYPES:
            raise ValueError(f"Unsupported effect type: {value}")
        return value

    @model_validator(mode="after")
    def validate_duration(self) -> EffectRecord:
        if self.duration_days is not None:
            if self.type != "set_flag":
                raise ValueError("duration_days is only valid for set_flag effects")
            if self.duration_days <= 0:
                raise ValueError("duration_days must be positive")
            if self.target != "location":
                raise ValueError("duration_days requires a location flag target")
        return self


class ActionDefinition(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    action_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    category: Literal["policy", "tactical", "social", "utility"]
    chain_template: str
    time_cost: StrictInt = 1
    required_inputs: tuple[str, ...] = ()
    effects: tuple[EffectRecord, ...] = ()
    status: Literal["live", "draft"]
    source: str = "legacy"
    notes: str = ""

    @property
    def action_id(self) -> str:
        return self.id


def _iter_action_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"))


def load_action_registry(directory: str | Path | None = None) -> dict[str, ActionDefinition]:
    action_dir = Path(directory) if directory is not None else ACTIONS_DIR
    entries: dict[str, ActionDefinition] = {}
    for path in _iter_action_files(action_dir):
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        definition = ActionDefinition.model_validate(payload)
        entries[definition.id] = definition
    return entries


def get_live_actions(directory: str | Path | None = None) -> dict[str, ActionDefinition]:
    registry = load_action_registry(directory)
    return {key: value for key, value in registry.items() if value.status == "live"}


def get_live_action_ids(*, category: str | None = None, directory: str | Path | None = None, priority: list[str] | None = None) -> list[str]:
    actions = get_live_actions(directory)
    items = list(actions.values())
    if category is not None:
        items = [item for item in items if item.category == category]
    ordered = [item.id for item in items]
    if priority:
        ordered = [item for item in priority if item in ordered]
        ordered.extend(item for item in [candidate.id for candidate in items] if item not in ordered)
    return ordered


def get_action_chain_map(directory: str | Path | None = None) -> dict[str, str]:
    registry = get_live_actions(directory)
    return {
        item.id: item.chain_template
        for item in registry.values()
        if item.category != "policy"
    }


def get_prompt_action_types(directory: str | Path | None = None, *, include_policy_only: bool = False) -> str:
    category = "policy" if include_policy_only else None
    priority = [
        "resource_policy",
        "diplomatic_stance",
        "infrastructure_investment",
        "population_policy",
        "personal_development",
    ] if include_policy_only else None
    return "|".join(get_live_action_ids(category=category, directory=directory, priority=priority))


def validate_registry(directory: str | Path | None = None) -> dict[str, ActionDefinition]:
    return load_action_registry(directory)
