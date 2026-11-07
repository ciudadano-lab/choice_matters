from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from game_data import TACTICAL_ACTION_TYPES
from simulation_engine import StructuredAction
from world_state import WorldState


@dataclass
class ParsedIntent:
    actions: list[StructuredAction]
    error: str | None = None


class IntentParser:
    _TACTICAL_ACTION_TYPES = frozenset(TACTICAL_ACTION_TYPES)
    _SPECIAL_ACTION_TYPES = frozenset({"reinforce", "search"})

    def __init__(self) -> None:
        self._action_patterns = {
            "reinforce": re.compile(r"\breinforce\b|\bfortify\b|\bboard up\b", re.IGNORECASE),
            "search": re.compile(r"\bsearch\b|\bscout\b|\bcheck\b", re.IGNORECASE),
            "scavenge": re.compile(r"\bscavenge\b|\bloot\b|\bsalvage\b|\braid\b.*\bsupplies\b", re.IGNORECASE),
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
            matched_builtin = False
            if self._action_patterns["reinforce"].search(clause_lower):
                matched_builtin = True
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
                matched_builtin = True
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

            if not matched_builtin:
                generic_action = self._match_generic_action(clause, clause_lower, actor, normalized, len(actions) + 1)
                if generic_action is not None:
                    actions.append(generic_action)

        if not actions:
            return ParsedIntent([], "I couldn't understand that command.")
        return ParsedIntent(actions)

    # Action types beyond reinforce/search that the rule-based fallback
    # can still resolve when the AI JSON parse fails. Each entry mirrors
    # the style of the reinforce/search handling above: a default target,
    # whether the target must be a real location id, and the flavor cost.
    _GENERIC_ACTION_CONFIG: dict[str, dict[str, Any]] = {
        "travel": {"default_target": "school", "needs_location": True, "timeCost": 2, "resourceCost": {}, "risks": ["zombie_encounter"], "prerequisites": ["can_travel"]},
        "scavenge": {"default_target": "supermarket", "needs_location": True, "timeCost": 1, "resourceCost": {}, "risks": ["zombie_encounter"], "prerequisites": []},
        "gather": {"default_target": "supermarket", "needs_location": False, "timeCost": 1, "resourceCost": {}, "risks": ["exposure"], "prerequisites": []},
        "build": {"default_target": "shelter", "needs_location": False, "timeCost": 2, "resourceCost": {"food": 1}, "risks": ["exposure"], "prerequisites": []},
        "rest": {"default_target": "shelter", "needs_location": False, "timeCost": 1, "resourceCost": {}, "risks": [], "prerequisites": []},
        "communicate": {"default_target": "camp", "needs_location": False, "timeCost": 1, "resourceCost": {}, "risks": [], "prerequisites": []},
        "negotiate": {"default_target": "faction_survivors", "needs_location": False, "timeCost": 1, "resourceCost": {}, "risks": [], "prerequisites": []},
        "attack": {"default_target": "school", "needs_location": False, "timeCost": 1, "resourceCost": {"food": 1}, "risks": ["injury", "retaliation"], "prerequisites": []},
        "defend": {"default_target": "shelter", "needs_location": False, "timeCost": 1, "resourceCost": {}, "risks": ["injury"], "prerequisites": []},
        "trade": {"default_target": "faction_survivors", "needs_location": False, "timeCost": 1, "resourceCost": {"water": 1}, "risks": [], "prerequisites": []},
        "recruit": {"default_target": "faction_survivors", "needs_location": False, "timeCost": 1, "resourceCost": {"food": 1}, "risks": [], "prerequisites": []},
        "send_mission": {"default_target": "player_2", "needs_location": False, "timeCost": 2, "resourceCost": {"water": 1}, "risks": ["unknown"], "prerequisites": []},
        "establish_settlement": {"default_target": "school", "needs_location": True, "timeCost": 3, "resourceCost": {"food": 2, "water": 1}, "risks": ["exposure"], "prerequisites": ["can_settle"]},
    }

    def _match_generic_action(self, clause: str, clause_lower: str, actor: str, world_state: dict[str, Any], next_index: int) -> StructuredAction | None:
        for action_type, config in self._GENERIC_ACTION_CONFIG.items():
            if not self._action_patterns[action_type].search(clause_lower):
                continue

            if action_type == "send_mission":
                target = self._extract_target_player(clause) or config["default_target"]
            else:
                target = self._extract_target(clause, default=config["default_target"])

            if config["needs_location"] and not self._has_location(world_state, target):
                # Rather than hard-failing the whole command, fall back to
                # the default target so an unrecognized location phrase
                # doesn't silently drop the action.
                target = config["default_target"] if self._has_location(world_state, config["default_target"]) else target

            return StructuredAction(
                actionId=f"{action_type}-{next_index}",
                actionType=action_type,
                actor=actor,
                target=target,
                parameters={"target": target},
                timeCost=config["timeCost"],
                resourceCost=dict(config["resourceCost"]),
                risks=list(config["risks"]),
                prerequisites=list(config["prerequisites"]),
            )
        return None

    def _extract_target_player(self, text: str) -> str | None:
        match = re.search(r"player\s*#?\s*(\d+)", text, re.IGNORECASE)
        return f"player_{match.group(1)}" if match else None

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
