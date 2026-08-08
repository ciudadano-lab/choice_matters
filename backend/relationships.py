from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PersonalityProfile:
    courage: float = 0.5
    paranoia: float = 0.5
    aggression: float = 0.5
    altruism: float = 0.5
    ambition: float = 0.5


class RelationshipSystem:
    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    def apply_event(self, world_state: dict[str, Any] | Any, actor_id: str, target_id: str, event_type: str, *, severity: int = 1) -> dict[str, Any]:
        state = world_state
        if isinstance(state, dict) and "world_state" in state:
            state = state["world_state"]
        elif hasattr(state, "players"):
            state = state
        elif hasattr(state, "to_dict"):
            state = state.to_dict()

        actor = self._get_player(state, actor_id)
        target = self._get_player(state, target_id)
        if actor is None or target is None:
            return {}

        relationship = target.setdefault("trustRelationships", {}).setdefault(actor_id, {
            "trust": 0.5,
            "respect": 0.5,
            "loyalty": 0.5,
            "fear": 0.2,
            "resentment": 0.0,
            "affection": 0.5,
        })

        personality = self._get_personality(target)
        delta = self._delta_for_event(event_type, severity, personality)
        self._apply_delta(relationship, delta)

        self._maybe_react(target, actor, relationship, event_type)
        return relationship

    def _delta_for_event(self, event_type: str, severity: int, personality: PersonalityProfile) -> dict[str, float]:
        if event_type == "mission_risk":
            return {
                "trust": -0.08 * severity - personality.paranoia * 0.05,
                "resentment": 0.08 * severity + personality.paranoia * 0.03,
                "fear": 0.04 * severity + personality.paranoia * 0.03,
                "respect": -0.03 * severity,
            }
        if event_type == "save":
            return {
                "trust": 0.1 * severity + personality.altruism * 0.05,
                "loyalty": 0.08 * severity + personality.altruism * 0.04,
                "affection": 0.04 * severity,
                "respect": 0.05 * severity,
            }
        if event_type == "order":
            return {
                "trust": -0.03 * severity,
                "respect": -0.02 * severity,
                "fear": 0.02 * severity,
            }
        if event_type == "betray":
            return {
                "trust": -0.2 * severity,
                "resentment": 0.15 * severity,
                "fear": 0.05 * severity,
            }
        return {}

    def _apply_delta(self, relationship: dict[str, Any], delta: dict[str, float]) -> None:
        for key, value in delta.items():
            current = relationship.get(key, 0.0)
            relationship[key] = max(0.0, min(1.0, current + value))

    def _maybe_react(self, target: dict[str, Any], actor: dict[str, Any], relationship: dict[str, Any], event_type: str) -> None:
        if event_type == "mission_risk" and relationship["resentment"] > 0.6:
            self._set_state(target, "currentActivity", "refusing_orders")
        if event_type == "save" and relationship["trust"] > 0.7:
            self._set_state(target, "currentActivity", "forming_alliance")
        if event_type == "betray" and relationship["fear"] > 0.6:
            self._set_state(target, "currentActivity", "secretly_communicating")
        if event_type == "order" and relationship["trust"] < 0.3:
            self._set_state(target, "currentActivity", "disagreeing")

    def _get_player(self, state: dict[str, Any] | Any, player_id: str) -> dict[str, Any] | None:
        if isinstance(state, dict):
            players = state.get("players", [])
        elif hasattr(state, "players"):
            players = state.players
        else:
            return None

        for player in players:
            if player.get("id") == player_id:
                return player
        return None

    def _get_personality(self, player: dict[str, Any]) -> PersonalityProfile:
        raw = player.get("personality", {})
        return PersonalityProfile(
            courage=float(raw.get("courage", 0.5)),
            paranoia=float(raw.get("paranoia", 0.5)),
            aggression=float(raw.get("aggression", 0.5)),
            altruism=float(raw.get("altruism", 0.5)),
            ambition=float(raw.get("ambition", 0.5)),
        )

    def _set_state(self, player: dict[str, Any], key: str, value: Any) -> None:
        player[key] = value
