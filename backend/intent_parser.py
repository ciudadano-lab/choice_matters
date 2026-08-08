from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from simulation_engine import StructuredAction
from world_state import WorldState


@dataclass
class ParsedIntent:
    actions: list[StructuredAction]
    error: str | None = None


class IntentParser:
    def __init__(self) -> None:
        self._action_patterns = {
            "reinforce": re.compile(r"\breinforce\b|\bfortify\b|\bboard up\b", re.IGNORECASE),
            "search": re.compile(r"\bsearch\b|\bscout\b|\bcheck\b", re.IGNORECASE),
            "gather": re.compile(r"\bgather\b|\bcollect\b", re.IGNORECASE),
            "build": re.compile(r"\bbuild\b|\bconstruct\b", re.IGNORECASE),
            "travel": re.compile(r"\btravel\b|\bgo to\b|\bmove to\b", re.IGNORECASE),
            "rest": re.compile(r"\brest\b|\bsleep\b", re.IGNORECASE),
            "communicate": re.compile(r"\bcommunicate\b|\bmessage\b|\btalk\b", re.IGNORECASE),
            "negotiate": re.compile(r"\bnegotiate\b|\bparley\b", re.IGNORECASE),
            "attack": re.compile(r"\battack\b|\bassault\b", re.IGNORECASE),
            "defend": re.compile(r"\bdefend\b|\bhold\b", re.IGNORECASE),
            "trade": re.compile(r"\btrade\b|\bbarter\b", re.IGNORECASE),
            "recruit": re.compile(r"\brecruit\b|\bconvince\b", re.IGNORECASE),
            "send_mission": re.compile(r"\bsend\b.*\bplayer\b|\bassign\b.*\bmission\b", re.IGNORECASE),
            "establish_settlement": re.compile(r"\bestablish\b.*\bsettlement\b|\bfound\b.*\bsettlement\b", re.IGNORECASE),
        }

    def parse(self, text: str, *, actor: str, world_state: WorldState | dict[str, Any]) -> ParsedIntent:
        normalized = world_state.to_dict() if hasattr(world_state, "to_dict") else world_state
        actions: list[StructuredAction] = []
        lowered = text.lower()

        clauses = self._split_clauses(text)
        for clause in clauses:
            clause_lower = clause.lower()
            if self._action_patterns["reinforce"].search(clause_lower):
                target = self._extract_target(clause, default="school")
                validation_error = self._validate_action("reinforce", actor, target, normalized)
                if validation_error:
                    return ParsedIntent([], validation_error)
                actions.append(
                    StructuredAction(
                        actionId=f"reinforce-{len(actions)+1}",
                        actionType="reinforce",
                        actor=actor,
                        target=target,
                        parameters={"materials": "desks", "target": "windows"},
                        timeCost=1,
                        resourceCost={"water": 1},
                        risks=["exposure"],
                        prerequisites=["can_reinforce"],
                    )
                )
            if self._action_patterns["search"].search(clause_lower):
                target = self._extract_target(clause, default="pharmacy")
                actor_id = actor
                if self._mentions_player_two(clause):
                    actor_id = "player_2"
                validation_error = self._validate_action("search", actor_id, target, normalized)
                if validation_error:
                    return ParsedIntent([], validation_error)
                actions.append(
                    StructuredAction(
                        actionId=f"search-{len(actions)+1}",
                        actionType="search",
                        actor=actor_id,
                        target=target,
                        parameters={"target": target},
                        timeCost=1,
                        resourceCost={},
                        risks=["zombie_encounter"],
                        prerequisites=["can_search"],
                    )
                )

        if not actions:
            return ParsedIntent([], "I couldn't understand that command.")
        return ParsedIntent(actions)

    def _split_clauses(self, text: str) -> list[str]:
        clauses = re.split(r"\s+and\s+", text, flags=re.IGNORECASE)
        return [clause.strip() for clause in clauses if clause.strip()]

    def _extract_target(self, text: str, *, default: str) -> str:
        cleaned = text.strip().lower()
        if "the school" in cleaned:
            return "school"
        if "the pharmacy" in cleaned:
            return "pharmacy"
        match = re.search(r"the\s+([a-zA-Z0-9_\- ]+)", text)
        if match:
            return match.group(1).strip().lower().replace(" ", "_")
        return default

    def _mentions_player_two(self, text: str) -> bool:
        return "player 2" in text.lower() or "player2" in text.lower()

    def _validate_action(self, action_type: str, actor: str, target: str, world_state: dict[str, Any]) -> str | None:
        if not self._has_location(world_state, target):
            return f"Unknown location: {target}."

        actor_player = self._find_player(world_state, actor)
        location = self._find_location(world_state, target)
        actor_location = actor_player.get("location") if actor_player else None

        if action_type == "search" and actor_location != target:
            distance = self._distance(actor_location, target, world_state)
            if distance > 10:
                return "Action requires travel first.\nEstimated travel time: 2 days."
        return None

    def _has_location(self, world_state: dict[str, Any], target: str) -> bool:
        return any(item.get("id") == target for item in world_state.get("locations", []))

    def _find_player(self, world_state: dict[str, Any], actor_id: str) -> dict[str, Any] | None:
        for player in world_state.get("players", []):
            if player.get("id") == actor_id:
                return player
        return None

    def _find_location(self, world_state: dict[str, Any], target: str) -> dict[str, Any] | None:
        for location in world_state.get("locations", []):
            if location.get("id") == target:
                return location
        return None

    def _distance(self, actor_location: str | None, target: str, world_state: dict[str, Any]) -> int:
        if not actor_location or actor_location == target:
            return 0
        actor_location_data = self._find_location(world_state, actor_location)
        target_location_data = self._find_location(world_state, target)
        if not actor_location_data or not target_location_data:
            return 0
        actor_coords = actor_location_data.get("coordinates", {})
        target_coords = target_location_data.get("coordinates", {})
        return abs(int(actor_coords.get("x", 0)) - int(target_coords.get("x", 0))) + abs(int(actor_coords.get("y", 0)) - int(target_coords.get("y", 0)))


def parse_player_intent(text: str, *, actor: str, world_state: WorldState | dict[str, Any]) -> tuple[list[StructuredAction], str | None]:
    parser = IntentParser()
    parsed = parser.parse(text, actor=actor, world_state=world_state)
    return parsed.actions, parsed.error
