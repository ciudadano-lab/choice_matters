from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


CONTENT_PATH = Path(__file__).resolve().parent / "content" / "world_rules.json"


class WorldRuleExample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: Literal["grounded", "impossible"]
    request: str
    explanation: str


class ImpossibleCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    match_terms: tuple[str, ...]


class WorldRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    physics_statement: str
    impossible_categories: tuple[ImpossibleCategory, ...]
    examples: tuple[WorldRuleExample, ...]

    def matches_impossible_claim(self, text: str) -> bool:
        normalized = " ".join(text.casefold().split())
        return any(
            term.casefold() in normalized
            for category in self.impossible_categories
            for term in category.match_terms
        )


@lru_cache(maxsize=1)
def load_world_rules() -> WorldRules:
    try:
        with CONTENT_PATH.open("r", encoding="utf-8") as handle:
            return WorldRules.model_validate(json.load(handle))
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid world rules content in {CONTENT_PATH}: {error}") from error


def render_world_rules(rules: WorldRules | None = None) -> str:
    world_rules = rules or load_world_rules()
    categories = "\n".join(
        f"- {category.description}"
        for category in world_rules.impossible_categories
    )
    examples = "\n".join(
        f"- {example.classification.title()}: {example.request} — {example.explanation}"
        for example in world_rules.examples
    )
    return (
        f"WORLD PHYSICS: {world_rules.physics_statement}\n"
        f"Impossible categories:\n{categories}\n"
        f"Grounded-versus-impossible examples:\n{examples}"
    )
