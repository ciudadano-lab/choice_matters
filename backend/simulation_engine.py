from __future__ import annotations

import copy
import hashlib
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import materialize_poi


@dataclass(frozen=True)
class StructuredAction:
    actionId: str
    actionType: str
    actor: str
    target: str
    parameters: dict[str, Any] = field(default_factory=dict)
    timeCost: int = 1
    resourceCost: dict[str, int] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    actionId: str
    actionType: str
    timeConsumed: int
    resourceChanges: dict[str, int]
    playerStatusChanges: list[dict[str, Any]]
    locationChanges: list[dict[str, Any]]
    encounterProbability: float
    factionRelationshipChanges: list[dict[str, Any]]
    informationDiscovered: list[dict[str, Any]]
    worldEventsTriggered: list[dict[str, Any]]
    updatedWorldState: dict[str, Any]


class SimulationEngine:
    ACTIONS = {
        "travel",
        "search",
        "gather",
        "build",
        "reinforce",
        "rest",
        "communicate",
        "negotiate",
        "attack",
        "defend",
        "trade",
        "recruit",
        "send_mission",
        "establish_settlement",
    }

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed if seed is not None else 1337

    def apply(self, world_state: dict | Any, action: StructuredAction) -> SimulationResult:
        raw_state = copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        if "world_state" not in raw_state:
            raw_state = {"world_state": raw_state}
        state = raw_state["world_state"]

        if action.actionType not in self.ACTIONS:
            raise ValueError(f"Unsupported action type: {action.actionType}")

        deterministic_seed = self._stable_seed(action.actionId, action.parameters.get("seed", self.seed))
        rng = deterministic_seed

        if action.actionType == "travel":
            result = self._resolve_travel(state, action, rng)
        elif action.actionType == "search":
            result = self._resolve_search(state, action, rng)
        elif action.actionType == "gather":
            result = self._resolve_gather(state, action, rng)
        elif action.actionType == "build":
            result = self._resolve_build(state, action, rng)
        elif action.actionType == "reinforce":
            result = self._resolve_reinforce(state, action, rng)
        elif action.actionType == "rest":
            result = self._resolve_rest(state, action, rng)
        elif action.actionType == "communicate":
            result = self._resolve_communicate(state, action, rng)
        elif action.actionType == "negotiate":
            result = self._resolve_negotiate(state, action, rng)
        elif action.actionType == "attack":
            result = self._resolve_attack(state, action, rng)
        elif action.actionType == "defend":
            result = self._resolve_defend(state, action, rng)
        elif action.actionType == "trade":
            result = self._resolve_trade(state, action, rng)
        elif action.actionType == "recruit":
            result = self._resolve_recruit(state, action, rng)
        elif action.actionType == "send_mission":
            result = self._resolve_send_mission(state, action, rng)
        else:
            result = self._resolve_establish_settlement(state, action, rng)

        state["currentTime"] = self._advance_time(state.get("currentTime", "00:00"), result["timeConsumed"])
        state["currentDay"] = state.get("currentDay", 1)
        state["players"] = state.get("players", [])
        state["locations"] = state.get("locations", [])
        state["factions"] = state.get("factions", [])
        state["resources"] = {
            **state.get("resources", {}),
            **{k: state.get("resources", {}).get(k, 0) + v for k, v in result["resourceChanges"].items()},
        }
        state["activeEvents"] = state.get("activeEvents", []) + result["worldEventsTriggered"]
        state["discoveredInformation"] = state.get("discoveredInformation", []) + result["informationDiscovered"]
        state["relationships"] = state.get("relationships", []) + result["factionRelationshipChanges"]
        state["worldHistory"] = state.get("worldHistory", []) + [{"day": state.get("currentDay", 1), "summary": f"{action.actionType} resolved"}]

        for change in result["playerStatusChanges"]:
            player = next((item for item in state["players"] if item.get("id") == change.get("playerId")), None)
            if player is None:
                continue
            for key, value in change.items():
                if key == "playerId":
                    continue
                if isinstance(value, (int, float)) and key in {"health", "stamina", "hunger", "stress"}:
                    player[key] = player.get(key, 0) + value
                else:
                    player[key] = value

        for change in result["locationChanges"]:
            location = next((item for item in state["locations"] if item.get("id") == change.get("locationId")), None)
            if location is not None:
                location["visibleState"] = {"status": change.get("state", "visited")}

        return SimulationResult(
            actionId=action.actionId,
            actionType=action.actionType,
            timeConsumed=result["timeConsumed"],
            resourceChanges=result["resourceChanges"],
            playerStatusChanges=result["playerStatusChanges"],
            locationChanges=result["locationChanges"],
            encounterProbability=result["encounterProbability"],
            factionRelationshipChanges=result["factionRelationshipChanges"],
            informationDiscovered=result["informationDiscovered"],
            worldEventsTriggered=result["worldEventsTriggered"],
            updatedWorldState=state,
        )

    def _stable_seed(self, action_id: str, seed: int) -> int:
        digest = hashlib.sha256(f"{action_id}:{seed}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)

    def _resolve_travel(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        player = self._get_player(state, action.actor)
        if not player:
            return self._empty_result(action, 0, {})
        target = action.target
        location = next((item for item in state.get("locations", []) if item.get("id") == target), None)
        if location is None:
            return self._empty_result(action, 0, {})

        player["location"] = target
        self._apply_resource_cost(state, action.resourceCost)
        encounter_probability = self._clamp(0.15 + (rng % 10) / 100.0)
        event_type = "encounter" if encounter_probability >= 0.2 else "travel"
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": self._resource_delta(action.resourceCost),
            "playerStatusChanges": [{"playerId": action.actor, "location": target, "stamina": -1}],
            "locationChanges": [{"locationId": target, "state": "visited"}],
            "encounterProbability": encounter_probability,
            "factionRelationshipChanges": [],
            "informationDiscovered": [{"id": f"travel_{action.actionId}", "content": f"Arrived at {location['name']}"}],
            "worldEventsTriggered": [{"type": event_type, "seed": rng}],
        }

    def _resolve_search(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        location = self._get_location(state, action.target)
        if location is None:
            return self._empty_result(action, 1, {})
        location["discovered"] = True
        location["visibleState"] = {"status": "searched"}
        info = {"id": f"search_{action.actionId}", "content": f"Searched {location['name']}"}

        for proposal in list(state.get("proposedDiscoveries", []) or []):
            if proposal.get("kind") != "poi":
                continue
            confidence = float(proposal.get("confidence", 0.0))
            if confidence < 0.5:
                continue
            template_id = proposal.get("templateId")
            if not template_id:
                continue
            materialize_poi(state, template_id)
            break

        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": self._resource_delta(action.resourceCost),
            "playerStatusChanges": [{"playerId": action.actor, "stress": 2}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.05 + (rng % 7) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [info],
            "worldEventsTriggered": [{"type": "search", "seed": rng}],
        }

    def _resolve_gather(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        self._apply_resource_cost(state, action.resourceCost)
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": 2, "water": 1},
            "playerStatusChanges": [{"playerId": action.actor, "hunger": -2}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.08 + (rng % 5) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "gather", "seed": rng}],
        }

    def _resolve_build(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        self._apply_resource_cost(state, action.resourceCost)
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": -1},
            "playerStatusChanges": [{"playerId": action.actor, "stress": 1}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.1 + (rng % 4) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "build", "seed": rng}],
        }

    def _resolve_reinforce(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        self._apply_resource_cost(state, action.resourceCost)
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"medical_supplies": -1},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -1}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.06 + (rng % 6) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "reinforce", "seed": rng}],
        }

    def _resolve_rest(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -3, "stamina": 10}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.03 + (rng % 3) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "rest", "seed": rng}],
        }

    def _resolve_communicate(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -1}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.05 + (rng % 8) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 1}],
            "informationDiscovered": [{"id": f"communicate_{action.actionId}", "content": "Messages exchanged"}],
            "worldEventsTriggered": [{"type": "communicate", "seed": rng}],
        }

    def _resolve_negotiate(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {},
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.04 + (rng % 5) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 2}],
            "informationDiscovered": [{"id": f"negotiate_{action.actionId}", "content": "Negotiation succeeded"}],
            "worldEventsTriggered": [{"type": "negotiate", "seed": rng}],
        }

    def _resolve_attack(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        self._apply_resource_cost(state, action.resourceCost)
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": -1},
            "playerStatusChanges": [{"playerId": action.actor, "stress": 4}],
            "locationChanges": [],
            "encounterProbability": 0.9,
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": -3}],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "attack", "seed": rng}],
        }

    def _resolve_defend(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -2, "health": 0}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.02 + (rng % 4) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "defend", "seed": rng}],
        }

    def _resolve_trade(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"water": -1, "food": 1},
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.07 + (rng % 4) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 1}],
            "informationDiscovered": [{"id": f"trade_{action.actionId}", "content": "Trade completed"}],
            "worldEventsTriggered": [{"type": "trade", "seed": rng}],
        }

    def _resolve_recruit(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": -1},
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.05 + (rng % 6) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 1}],
            "informationDiscovered": [{"id": f"recruit_{action.actionId}", "content": "Recruitment offer sent"}],
            "worldEventsTriggered": [{"type": "recruit", "seed": rng}],
        }

    def _resolve_send_mission(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"water": -1},
            "playerStatusChanges": [{"playerId": action.target, "stress": 2}],
            "locationChanges": [],
            "encounterProbability": self._clamp(0.1 + (rng % 5) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [{"id": f"mission_{action.actionId}", "content": "Mission assigned"}],
            "worldEventsTriggered": [{"type": "mission", "seed": rng}],
        }

    def _resolve_establish_settlement(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        state.setdefault("settlements", []).append({"id": action.actionId, "name": action.target, "location": action.target})
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": -2, "water": -1},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -1}],
            "locationChanges": [{"locationId": action.target, "state": "settled"}],
            "encounterProbability": self._clamp(0.02 + (rng % 6) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 2}],
            "informationDiscovered": [{"id": f"settlement_{action.actionId}", "content": "Settlement established"}],
            "worldEventsTriggered": [{"type": "settlement", "seed": rng}],
        }

    def _empty_result(self, action: StructuredAction, time_cost: int, resource_cost: dict[str, int]) -> dict[str, Any]:
        return {
            "timeConsumed": time_cost,
            "resourceChanges": self._resource_delta(resource_cost),
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": 0.0,
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [],
        }

    def _get_player(self, state: dict[str, Any], actor_id: str) -> dict[str, Any] | None:
        players = state.get("players", [])
        if isinstance(players, dict):
            return players.get(actor_id)
        return next((player for player in players if player.get("id") == actor_id), None)

    def _get_location(self, state: dict[str, Any], location_id: str) -> dict[str, Any] | None:
        return next((item for item in state.get("locations", []) if item.get("id") == location_id), None)

    def _apply_resource_cost(self, state: dict[str, Any], resource_cost: dict[str, int]) -> None:
        for resource_name, value in resource_cost.items():
            state.setdefault("resources", {})[resource_name] = state.setdefault("resources", {}).get(resource_name, 0) - value

    def _resource_delta(self, resource_cost: dict[str, int]) -> dict[str, int]:
        return {name: -value for name, value in resource_cost.items() if value}

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, value))

    def _advance_time(self, current_time: str, hours: int) -> str:
        parts = current_time.split(":")
        if len(parts) != 2:
            return current_time
        hour = int(parts[0]) + hours
        minute = int(parts[1])
        if hour >= 24:
            hour = hour % 24
        return f"{hour:02d}:{minute:02d}"
