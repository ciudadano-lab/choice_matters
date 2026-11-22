from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)


BACKEND_DIR = Path(__file__).resolve().parent
CONTENT_DIR = BACKEND_DIR / "content"

SettlementStatus = Literal[
    "growing",
    "stable",
    "strained",
    "starving",
    "collapsing",
    "abandoned",
]
FactionStance = Literal["allied", "friendly", "neutral", "wary", "hostile", "war"]
NpcStatus = Literal["alive", "missing", "dead"]


class StrictContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactionSeed(StrictContentModel):
    id: str
    name: str
    legacy_ideology: str
    ideology_tags: tuple[str, ...]
    goals: tuple[str, ...]
    strength: StrictInt = Field(ge=0)
    population: StrictInt = Field(gt=0)
    military_strength: StrictInt = Field(ge=0)
    resources: dict[str, StrictInt]
    territory_types: tuple[str, ...]
    initial_dispositions: dict[str, StrictInt]
    initial_stances: dict[str, FactionStance]
    player_disposition: StrictInt = Field(ge=-100, le=100)
    leader_npc_id: str
    council_seats: StrictInt = Field(ge=0)


class FactionsContent(StrictContentModel):
    factions: tuple[FactionSeed, ...]


class SettlementDefaults(StrictContentModel):
    morale: StrictInt = Field(ge=0, le=100)
    unrest: StrictInt = Field(ge=0, le=100)
    defense: StrictInt = Field(ge=0, le=100)
    stores: dict[str, StrictInt]
    coordinates: dict[str, StrictInt]
    status: SettlementStatus
    needs_thresholds: dict[str, StrictInt]


class SettlementSeed(StrictContentModel):
    id: str
    name: str
    location_type: str
    faction_id: str
    leader_npc_id: str
    population: StrictInt = Field(ge=0)
    morale: StrictInt = Field(ge=0, le=100)
    unrest: StrictInt = Field(ge=0, le=100)
    defense: StrictInt = Field(ge=0, le=100)
    stores: dict[str, StrictInt]
    status: SettlementStatus
    founded_day: StrictInt = Field(ge=1)


class SettlementsContent(StrictContentModel):
    defaults: SettlementDefaults
    settlements: tuple[SettlementSeed, ...]


class NpcSeed(StrictContentModel):
    id: str
    name: str
    role: str
    faction_id: str
    settlement_id: str
    traits: tuple[str, ...]
    loyalty: StrictInt = Field(ge=0, le=100)
    status: NpcStatus
    arc_id: str | None = None
    arc_stage: StrictInt = Field(ge=0)


class NpcsContent(StrictContentModel):
    npcs: tuple[NpcSeed, ...]


class FactionRules(StrictContentModel):
    goal_vocabulary: tuple[str, ...]
    faction_disposition_scale_factor: StrictInt = Field(gt=0)
    surface_faction_events_to_active_events: bool = True


class WorldContent(StrictContentModel):
    factions: FactionsContent
    settlements: SettlementsContent
    npcs: NpcsContent
    faction_rules: FactionRules

    @model_validator(mode="after")
    def validate_references(self) -> WorldContent:
        faction_ids = {faction.id for faction in self.factions.factions}
        settlement_ids = {
            settlement.id for settlement in self.settlements.settlements
        }
        npc_ids = {npc.id for npc in self.npcs.npcs}
        goal_ids = set(self.faction_rules.goal_vocabulary)

        if len(faction_ids) != len(self.factions.factions):
            raise ValueError("Faction ids must be unique")
        if len(settlement_ids) != len(self.settlements.settlements):
            raise ValueError("Settlement ids must be unique")
        if len(npc_ids) != len(self.npcs.npcs):
            raise ValueError("NPC ids must be unique")

        for faction in self.factions.factions:
            if faction.leader_npc_id not in npc_ids:
                raise ValueError(
                    f"{faction.id}: leader_npc_id references an unknown NPC"
                )
            if not set(faction.goals).issubset(goal_ids):
                raise ValueError(f"{faction.id}: goal is not in goal_vocabulary")
            other_ids = faction_ids - {faction.id}
            if set(faction.initial_dispositions) != other_ids:
                raise ValueError(
                    f"{faction.id}: initial_dispositions must cover every other faction"
                )
            if set(faction.initial_stances) != other_ids:
                raise ValueError(
                    f"{faction.id}: initial_stances must cover every other faction"
                )
            if any(not -100 <= score <= 100 for score in faction.initial_dispositions.values()):
                raise ValueError(f"{faction.id}: faction dispositions must be between -100 and 100")

        for settlement in self.settlements.settlements:
            if settlement.faction_id not in faction_ids:
                raise ValueError(
                    f"{settlement.id}: references an unknown faction"
                )
            if settlement.leader_npc_id not in npc_ids:
                raise ValueError(
                    f"{settlement.id}: leader_npc_id references an unknown NPC"
                )

        for npc in self.npcs.npcs:
            if npc.faction_id not in faction_ids:
                raise ValueError(f"{npc.id}: references an unknown faction")
            if npc.settlement_id not in settlement_ids:
                raise ValueError(f"{npc.id}: references an unknown settlement")

        return self


def _read_model(path: Path, model: type[BaseModel]) -> BaseModel:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"Invalid world content in {path}: {error}") from error


@lru_cache(maxsize=4)
def _load_world_content_cached(directory: str) -> WorldContent:
    content_dir = Path(directory)
    content = WorldContent(
        factions=_read_model(content_dir / "factions.json", FactionsContent),
        settlements=_read_model(content_dir / "settlements.json", SettlementsContent),
        npcs=_read_model(content_dir / "npcs.json", NpcsContent),
        faction_rules=_read_model(content_dir / "faction_rules.json", FactionRules),
    )
    return content


def load_world_content(directory: str | Path | None = None) -> WorldContent:
    content_dir = Path(directory) if directory is not None else CONTENT_DIR
    return _load_world_content_cached(str(content_dir.resolve()))
