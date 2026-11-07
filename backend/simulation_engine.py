from __future__ import annotations

import copy
import hashlib
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import materialize_poi, settlement_tier


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
    encounterOccurred: bool
    encounterDetails: dict[str, Any]
    factionRelationshipChanges: list[dict[str, Any]]
    informationDiscovered: list[dict[str, Any]]
    worldEventsTriggered: list[dict[str, Any]]
    updatedWorldState: dict[str, Any]
    outcomeTier: str = "success"
    socialOutcome: dict[str, Any] = field(default_factory=dict)
    rejected: bool = False
    rejectionReason: str | None = None
    physicallyImpossible: bool = False
    impossibleReason: str | None = None


class SimulationEngine:
    POLICY_ACTIONS = {
        "resource_policy",
        "diplomatic_stance",
        "infrastructure_investment",
        "population_policy",
        "personal_development",
    }
    ACTIONS = {
        "resource_policy",
        "diplomatic_stance",
        "infrastructure_investment",
        "population_policy",
        "personal_development",
        "travel",
        "search",
        "scavenge",
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
        "failed_attempt",
    }

    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed if seed is not None else 1337

    def auto_resolve_policy_day(
        self,
        world_state: dict | Any,
        actor_id: str,
        policy: dict[str, Any],
        day_index: int,
    ) -> SimulationResult:
        """Resolve one internal tactical day from the player's strategic policy."""
        raw_state = world_state.to_dict() if hasattr(world_state, "to_dict") else world_state
        player = self._get_player(raw_state, actor_id)
        location = player.get("location") if player else None
        if not location:
            location = next(
                (item.get("id") for item in raw_state.get("locations", []) if item.get("discovered")),
                "location_shelter",
            )

        action_type = str(policy.get("actionType", policy.get("type", ""))).casefold()
        focus = str(policy.get("focus", policy.get("stance", ""))).casefold()
        start_day = int(policy.get("startDay", day_index))
        days_since_commitment = day_index - start_day
        commitment_days = max(1, int(policy.get("commitmentDays", 21)))
        pivotal_timing = str(policy.get("pivotalTiming", "culmination")).casefold()
        pivotal_day = 0 if pivotal_timing == "immediate" else commitment_days - 1
        mission_target = self._policy_mission_target(raw_state, focus)
        pivotal_target = self._policy_location_target(raw_state, focus, location)
        tactical_target = location

        pivotal_action_type: str | None = None
        if days_since_commitment == pivotal_day and mission_target:
            pivotal_action_type = "send_mission"
            tactical_target = mission_target
        elif days_since_commitment == pivotal_day and action_type == "infrastructure_investment" and any(
            keyword in focus for keyword in ("settlement", "found", "establish")
        ):
            pivotal_action_type = "establish_settlement"
        elif days_since_commitment == pivotal_day and action_type == "resource_policy" and pivotal_target:
            pivotal_action_type = "travel"
            tactical_target = pivotal_target
        elif days_since_commitment == pivotal_day and action_type in {"personal_development", "infrastructure_investment", "diplomatic_stance"}:
            if any(keyword in focus for keyword in ("attack", "aggressive", "hostile", "raid", "assault")):
                pivotal_action_type = "attack"
            elif any(keyword in focus for keyword in ("defend", "defensive", "protect", "guard", "security")):
                pivotal_action_type = "defend"

        if action_type == "resource_policy":
            if pivotal_action_type is not None:
                tactical_type = pivotal_action_type
            elif mission_target:
                tactical_type = "search"
            elif any(keyword in focus for keyword in ("scavenge", "loot", "salvage", "raid")):
                tactical_type = "scavenge"
            elif "search" in focus or "scout" in focus:
                tactical_type = "search"
            else:
                tactical_type = "gather"
        elif action_type == "diplomatic_stance":
            if pivotal_action_type is not None:
                tactical_type = pivotal_action_type
            elif mission_target:
                tactical_type = "rest"
            elif any(keyword in focus for keyword in ("communicate", "message", "talk", "contact")):
                tactical_type = "communicate"
            elif "alliance" in focus or "negotiate" in focus:
                tactical_type = "negotiate"
            else:
                tactical_type = "trade"
        elif action_type == "infrastructure_investment":
            if pivotal_action_type is not None:
                tactical_type = pivotal_action_type
            elif mission_target:
                tactical_type = "rest"
            elif any(keyword in focus for keyword in ("attack", "aggressive", "hostile", "raid", "assault")):
                tactical_type = "reinforce"
            elif "fortif" in focus or "defen" in focus:
                tactical_type = "reinforce"
            else:
                tactical_type = "build"
        elif action_type == "population_policy":
            tactical_type = pivotal_action_type or ("rest" if mission_target else ("recruit" if "recruit" in focus else "search"))
        elif action_type == "personal_development":
            tactical_type = pivotal_action_type or (
                "rest"
                if mission_target or any(keyword in focus for keyword in ("attack", "aggressive", "hostile", "raid", "assault", "defend", "defensive"))
                else ("gather" if "crop" in focus or "invest" in focus else "rest")
            )
        else:
            tactical_type = pivotal_action_type or "rest"

        action = StructuredAction(
            actionId=f"autopilot-{actor_id}-{day_index}-{tactical_type}",
            actionType=tactical_type,
            actor=actor_id,
            target=tactical_target,
            parameters={"autopilot": True, "policy": copy.deepcopy(policy)},
            timeCost=0,
            resourceCost={},
        )
        return self.apply(world_state, action)

    def _policy_location_target(self, state: dict[str, Any], focus: str, current_location: str) -> str | None:
        if not any(keyword in focus for keyword in ("travel", "move", "relocate", "explore")):
            return None
        current = self._get_location(state, current_location)
        current_coordinates = (current or {}).get("coordinates", {})
        for location in state.get("locations", []):
            location_id = str(location.get("id", ""))
            name = str(location.get("name", "")).casefold()
            tokens = [token for token in re.findall(r"[a-z0-9]+", name) if len(token) >= 4]
            named = location_id.casefold() in focus or (tokens and all(token in focus for token in tokens))
            if not named or location_id == current_location:
                continue
            coordinates = location.get("coordinates", {})
            distance = abs(int(coordinates.get("x", 0)) - int(current_coordinates.get("x", 0))) + abs(
                int(coordinates.get("y", 0)) - int(current_coordinates.get("y", 0))
            )
            if not location.get("discovered", False) or distance > 10:
                return location_id
        return None

    def _policy_mission_target(self, state: dict[str, Any], focus: str) -> str | None:
        if not any(keyword in focus for keyword in ("mission", "assign", "send")):
            return None
        player_match = re.search(r"player\s*#?\s*(\d+)", focus)
        if player_match:
            return f"player_{player_match.group(1)}"
        for faction in state.get("factions", []):
            faction_id = str(faction.get("id", ""))
            faction_name = str(faction.get("name", "")).casefold()
            if faction_id.casefold() in focus or faction_name and faction_name in focus:
                return faction_id
        return None

    def apply(self, world_state: dict | Any, action: StructuredAction) -> SimulationResult:
        raw_state = copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        if "world_state" not in raw_state:
            raw_state = {"world_state": raw_state}
        state = raw_state["world_state"]

        if action.actionType not in self.ACTIONS:
            raise ValueError(f"Unsupported action type: {action.actionType}")

        rejection_reason = self._validate_target_entity(state, action)
        if rejection_reason is not None:
            return SimulationResult(
                actionId=action.actionId,
                actionType=action.actionType,
                timeConsumed=0,
                resourceChanges={},
                playerStatusChanges=[],
                locationChanges=[],
                encounterProbability=0.0,
                encounterOccurred=False,
                encounterDetails={"type": "hostile_encounter", "outcome": "not_applicable"},
                factionRelationshipChanges=[],
                informationDiscovered=[],
                worldEventsTriggered=[],
                updatedWorldState=state,
                outcomeTier="failure",
                rejected=True,
                rejectionReason=rejection_reason,
            )

        deterministic_seed = self._stable_seed(action.actionId, action.parameters.get("seed", self.seed))
        rng = deterministic_seed

        if action.actionType in self.POLICY_ACTIONS:
            result = self._resolve_policy_commitment(state, action)
        elif action.actionType == "failed_attempt":
            result = self._resolve_failed_attempt(state, action)
        elif action.actionType == "travel":
            result = self._resolve_travel(state, action, rng)
        elif action.actionType == "search":
            result = self._resolve_search(state, action, rng)
        elif action.actionType == "scavenge":
            result = self._resolve_scavenge(state, action, rng)
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

        encounter_probability = self._clamp(float(result.get("encounterProbability", 0.0)))
        encounter_roll = self._stable_seed(
            f"encounter:{action.actionId}", self.seed
        ) % 1_000_000 / 1_000_000
        encounter_occurred = encounter_roll < encounter_probability
        encounter_details = {
            "type": "hostile_encounter",
            "roll": encounter_roll,
            "outcome": "occurred" if encounter_occurred else "avoided",
        }
        if encounter_occurred:
            result["worldEventsTriggered"].append({
                "type": "encounter",
                "seed": encounter_roll,
                "details": encounter_details,
            })

        new_time, day_delta = self._advance_time(state.get("currentTime", "00:00"), result["timeConsumed"])
        state["currentTime"] = new_time
        state["currentDay"] = state.get("currentDay", 1) + day_delta
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
            effect_updates = change.get("statusEffects")
            if isinstance(effect_updates, list):
                player.setdefault("statusEffects", [])
                for effect in effect_updates:
                    if not isinstance(effect, dict):
                        continue
                    existing = next((item for item in player["statusEffects"] if item.get("id") == effect.get("id")), None)
                    if existing is None:
                        player["statusEffects"].append(effect)
                    else:
                        existing.update(effect)
            for key, value in change.items():
                if key in {"playerId", "statusEffects"}:
                    continue
                if isinstance(value, (int, float)) and key in {"health", "stamina", "hunger", "stress"}:
                    player[key] = player.get(key, 0) + value
                else:
                    player[key] = value

        for change in result["locationChanges"]:
            location = next((item for item in state["locations"] if item.get("id") == change.get("locationId")), None)
            if location is not None:
                location["visibleState"] = {"status": change.get("state", "visited")}

        outcome_tier = self._resolve_outcome_tier(state, action, result, rng)
        social_outcome = self._resolve_social_outcome(state, action, rng)
        self._apply_injury_if_needed(state, action, outcome_tier, rng)
        if social_outcome:
            result["socialOutcome"] = social_outcome
            result["informationDiscovered"].append({
                "id": f"social_{action.actionId}",
                "content": social_outcome.get("summary", f"{social_outcome.get('targetId', action.target)} reacted {social_outcome.get('reaction', 'quietly')}.") ,
                "targetId": social_outcome.get("targetId", action.target),
                "reaction": social_outcome.get("reaction"),
            })

        return SimulationResult(
            actionId=action.actionId,
            actionType=action.actionType,
            timeConsumed=result["timeConsumed"],
            resourceChanges=result["resourceChanges"],
            playerStatusChanges=result["playerStatusChanges"],
            locationChanges=result["locationChanges"],
            encounterProbability=encounter_probability,
            encounterOccurred=encounter_occurred,
            encounterDetails=encounter_details,
            factionRelationshipChanges=result["factionRelationshipChanges"],
            informationDiscovered=result["informationDiscovered"],
            worldEventsTriggered=result["worldEventsTriggered"],
            updatedWorldState=state,
            outcomeTier=outcome_tier,
            socialOutcome=result.get("socialOutcome", {}),
            physicallyImpossible=bool(result.get("physicallyImpossible", False)),
            impossibleReason=result.get("impossibleReason"),
        )

    def _resolve_policy_commitment(self, state: dict[str, Any], action: StructuredAction) -> dict[str, Any]:
        player = self._get_player(state, action.actor)
        if player is None:
            return self._empty_result(action, 0, {})
        policy = copy.deepcopy(action.parameters.get("policy", {}))
        policy.setdefault("actionType", action.actionType)
        policy.setdefault("focus", action.parameters.get("focus", ""))
        policy.setdefault("commitmentDays", 21)
        policy.setdefault("startDay", int(state.get("currentDay", 1)))
        policy.setdefault("pivotalTiming", "culmination")
        player["currentPolicy"] = policy
        return {
            "timeConsumed": 0,
            "resourceChanges": {},
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": 0.0,
            "factionRelationshipChanges": [],
            "informationDiscovered": [{
                "id": f"policy_{action.actionId}",
                "content": f"Policy committed for {policy['commitmentDays']} days: {action.actionType}.",
            }],
            "worldEventsTriggered": [{
                "type": "policy_committed",
                "seed": action.actionId,
                "policy": policy,
            }],
        }

    def _apply_injury_if_needed(self, state: dict[str, Any], action: StructuredAction, outcome_tier: str, rng: int) -> None:
        if outcome_tier not in {"partial_success", "failure", "critical_failure"}:
            return
        if action.actionType not in {"travel", "search", "gather", "build", "reinforce", "attack", "defend", "trade", "recruit", "send_mission", "establish_settlement"}:
            return

        player = self._get_player(state, action.actor)
        if player is None:
            return

        injury_names = ["sprained ankle", "laceration", "bite scare"]
        injury_name = injury_names[rng % len(injury_names)]
        mechanical_effect = {"stamina": -20, "health": -5}
        if injury_name == "laceration":
            mechanical_effect = {"health": -10, "stamina": -10}
        elif injury_name == "bite scare":
            mechanical_effect = {"stress": 10, "stamina": -5}

        status_effect = {
            "id": f"effect_{action.actionId}_{self._stable_seed(action.actionId, rng) % 997}",
            "name": injury_name,
            "appliedDay": state.get("currentDay", 1),
            "expiresDay": state.get("currentDay", 1) + (2 if outcome_tier == "critical_failure" else 1),
            "mechanicalEffect": mechanical_effect,
        }
        existing = player.setdefault("statusEffects", [])
        if not any(item.get("name") == injury_name for item in existing):
            existing.append(status_effect)
        player["stamina"] = max(0, player.get("stamina", 0) + mechanical_effect.get("stamina", 0))
        player["health"] = max(0, player.get("health", 0) + mechanical_effect.get("health", 0))
        player["stress"] = max(0, player.get("stress", 0) + mechanical_effect.get("stress", 0))

    def _resolve_social_outcome(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        if action.actionType not in {"recruit", "negotiate", "trade"}:
            return {}

        target_id = action.target
        actor_player = self._get_player(state, action.actor)
        if actor_player is None:
            return {}

        target_npc = None
        for player in state.get("players", []):
            if not isinstance(player, dict):
                continue
            known = player.get("entities", {}).get("knownCharacters", []) if isinstance(player.get("entities", {}), dict) else []
            for npc in known:
                if isinstance(npc, dict) and str(npc.get("id")) == str(target_id):
                    target_npc = npc
                    break
            if target_npc is not None:
                break
        if target_npc is None:
            return {}

        relationship = actor_player.get("trustRelationships", {}).get(target_id, {})
        relationship_value = float(relationship.get("trust", 0.5)) if isinstance(relationship, dict) else 0.5
        trait_names = {str(item).lower() for item in target_npc.get("traits", []) if isinstance(item, str)}

        threat_adjustment = 0.12 if "cautious" in trait_names else 0.0
        if "guarded" in trait_names:
            threat_adjustment += 0.08
        if "resourceful" in trait_names and action.actionType == "trade":
            threat_adjustment -= 0.12
        if "charismatic" in trait_names and action.actionType == "negotiate":
            threat_adjustment -= 0.1
        if "stubborn" in trait_names:
            threat_adjustment += 0.08

        threshold = 0.62 - (relationship_value * 0.35) + threat_adjustment
        if action.actionType == "trade":
            threshold = 0.55 - (relationship_value * 0.28) + (0.12 if "resourceful" in trait_names else 0.0)
        elif action.actionType == "recruit":
            threshold = 0.68 - (relationship_value * 0.38) + (0.08 if "charismatic" in trait_names else 0.0)
        threshold = max(0.15, min(0.9, threshold))

        roll = (self._stable_seed(f"social:{action.actionId}:{target_id}:{rng}", rng) % 100) / 100.0
        if roll <= threshold - 0.12:
            reaction = "accepted"
        elif roll <= threshold + 0.16:
            reaction = "countered"
        else:
            reaction = "declined"

        summary = {
            "targetId": target_id,
            "reaction": reaction,
            "threshold": round(threshold, 3),
            "roll": round(roll, 3),
            "relationshipTrust": round(relationship_value, 3),
            "traits": sorted(trait_names),
            "actionType": action.actionType,
        }
        if action.actionType == "trade":
            summary["summary"] = f"{target_npc.get('name', target_id)} {('accepts the trade' if reaction == 'accepted' else 'counters the trade offer' if reaction == 'countered' else 'declines the trade')}."
        elif action.actionType == "negotiate":
            summary["summary"] = f"{target_npc.get('name', target_id)} {('agrees to the terms' if reaction == 'accepted' else 'offers a counterproposal' if reaction == 'countered' else 'refuses the terms')}."
        else:
            summary["summary"] = f"{target_npc.get('name', target_id)} {('accepts the offer to join' if reaction == 'accepted' else 'wants to negotiate before joining' if reaction == 'countered' else 'turns down the invitation')}."
        return summary

    def _resolve_failed_attempt(self, state: dict[str, Any], action: StructuredAction) -> dict[str, Any]:
        return {
            "timeConsumed": max(0, action.timeCost),
            "resourceChanges": {},
            "playerStatusChanges": [],
            "locationChanges": [],
            "encounterProbability": 0.0,
            "factionRelationshipChanges": [],
            "informationDiscovered": [],
            "worldEventsTriggered": [{"type": "failed_attempt", "reason": "physically impossible"}],
            "physicallyImpossible": True,
            "impossibleReason": "Nothing happens because the claimed effect is not possible in this world.",
        }

    def _resolve_outcome_tier(self, state: dict[str, Any], action: StructuredAction, result: dict[str, Any], rng: int) -> str:
        if result.get("physicallyImpossible"):
            return "failure"

        if action.actionType in {"travel", "search", "gather", "build", "reinforce", "rest", "communicate", "negotiate", "trade", "recruit", "send_mission", "establish_settlement"}:
            actor = self._get_player(state, action.actor)
            if actor is None:
                actor = {"stamina": 50, "health": 100, "stress": 0}

            if actor.get("location") and str(actor.get("location")) == str(action.target):
                return "failure"
            if action.actionType == "travel" and self._get_location(state, action.target) is None:
                return "failure"

            physical = float(actor.get("stamina", 50)) / 100.0
            health = float(actor.get("health", 100)) / 100.0
            stress = float(actor.get("stress", 0)) / 100.0
            trust = 0.5
            if action.actionType in {"communicate", "negotiate", "trade", "recruit"}:
                relationship_bucket = state.get("relationships", [])
                if relationship_bucket:
                    trust = 0.5
                for item in relationship_bucket:
                    if isinstance(item, dict) and item.get("targetId") == action.target:
                        trust = float(item.get("trust", 0.5))
                        break

            base = (rng % 100) / 100.0
            if action.actionType in {"attack", "defend", "build", "reinforce"}:
                score = (physical * 0.5) + (health * 0.3) - (stress * 0.3) + (base * 0.2)
            elif action.actionType in {"communicate", "negotiate", "trade", "recruit"}:
                score = (trust * 0.7) + (health * 0.1) + (base * 0.2)
            else:
                score = (physical * 0.4) + (health * 0.2) + (base * 0.4)

            if score >= 0.82:
                return "critical_success"
            if score >= 0.62:
                return "success"
            if score >= 0.38:
                return "partial_success"
            if score >= 0.18:
                return "failure"
            return "critical_failure"

        return "success"

    def _validate_target_entity(self, state: dict[str, Any], action: StructuredAction) -> str | None:
        target_entity = action.parameters.get("targetEntity")
        if not isinstance(target_entity, dict):
            return None

        owner = str(target_entity.get("owner", ""))
        entity_type = str(target_entity.get("type", ""))
        entity_name = str(target_entity.get("name", "")).strip()
        entity_type = {
            "companions": "companion",
            "possessions": "possession",
        }.get(entity_type, entity_type)
        if not owner and not entity_type and not entity_name:
            return None
        if not owner or entity_type not in {"companion", "possession"} or not entity_name:
            return "The claimed target entity is incomplete or invalid."
        if owner == action.actor:
            return None

        owner_player = self._get_player(state, owner)
        if owner_player is None:
            return f"No player exists with owner {owner}."
        ledger_key = "companions" if entity_type == "companion" else "possessions"
        entities = owner_player.get("entities", {})
        ledger = entities.get(ledger_key, []) if isinstance(entities, dict) else []
        for entity in ledger:
            name = entity.get("name") if isinstance(entity, dict) else entity
            if isinstance(name, str) and name.casefold() == entity_name.casefold():
                return None
        return f"Player {owner} has no known {entity_type} named {entity_name}."

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
        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": self._resource_delta(action.resourceCost),
            "playerStatusChanges": [{"playerId": action.actor, "location": target, "stamina": -1}],
            "locationChanges": [{"locationId": target, "state": "visited"}],
            "encounterProbability": encounter_probability,
            "factionRelationshipChanges": [],
            "informationDiscovered": [{"id": f"travel_{action.actionId}", "content": f"Arrived at {location['name']}"}],
            "worldEventsTriggered": [{"type": "travel", "seed": rng}],
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

    def _resolve_scavenge(self, state: dict[str, Any], action: StructuredAction, rng: int) -> dict[str, Any]:
        location = self._get_location(state, action.target)
        if location is None:
            return self._empty_result(action, 1, {})

        resources = location.get("resources", {})
        harvested: dict[str, int] = {}
        if isinstance(resources, dict):
            for resource, available_value in list(resources.items()):
                available = max(0, int(available_value))
                if available <= 0:
                    continue
                harvest_seed = self._stable_seed(f"{action.actionId}:{resource}", rng)
                taken = min(available, 1 + harvest_seed % min(available, 3))
                harvested[resource] = taken
                resources[resource] = max(0, available - taken)

        location["visibleState"] = {"status": "scavenged"}
        if harvested:
            information = {
                "id": f"scavenge_{action.actionId}",
                "content": f"Scavenged {location['name']}: " + ", ".join(
                    f"{amount} {resource.replace('_', ' ')}" for resource, amount in harvested.items()
                ),
            }
        else:
            information = {
                "id": f"scavenge_{action.actionId}",
                "content": f"{location['name']} has been picked clean.",
            }

        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": harvested,
            "playerStatusChanges": [{"playerId": action.actor, "stamina": -1, "stress": 1}],
            "locationChanges": [{"locationId": action.target, "state": "scavenged"}],
            "encounterProbability": self._clamp(0.05 + (rng % 8) / 100.0),
            "factionRelationshipChanges": [],
            "informationDiscovered": [information],
            "worldEventsTriggered": [{"type": "scavenge", "seed": rng}],
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
        location = self._get_location(state, action.target)
        target_name = (location or {}).get("name", action.target)
        settlement_name = f"{target_name} Settlement"
        settlements = state.setdefault("settlements", [])
        if not isinstance(settlements, list):
            settlements = []
            state["settlements"] = settlements

        existing_index = next(
            (idx for idx, settlement in enumerate(settlements) if isinstance(settlement, dict) and settlement.get("location") == action.target),
            None,
        )

        if existing_index is not None:
            existing = settlements[existing_index]
            existing["population"] = int(existing.get("population", 0)) + max(1, int(rng % 5) + 1)
            existing["tier"] = settlement_tier(existing["population"])
            existing["founderId"] = existing.get("founderId", action.actor)
            settlement_record = existing
        else:
            population = 3 + (rng % 8)
            settlement_record = {
                "id": action.actionId,
                "name": settlement_name,
                "location": action.target,
                "coordinates": copy.deepcopy((location or {}).get("coordinates", {"x": 0, "y": 0})),
                "population": population,
                "tier": settlement_tier(population),
                "foundedDay": int(state.get("currentDay", 1)),
                "founderId": action.actor,
            }
            settlements.append(settlement_record)

        return {
            "timeConsumed": action.timeCost,
            "resourceChanges": {"food": -2, "water": -1},
            "playerStatusChanges": [{"playerId": action.actor, "stress": -1}],
            "locationChanges": [{"locationId": action.target, "state": "settled"}],
            "encounterProbability": self._clamp(0.02 + (rng % 6) / 100.0),
            "factionRelationshipChanges": [{"factionId": "faction_survivors", "delta": 2}],
            "informationDiscovered": [{"id": f"settlement_{action.actionId}", "content": f"Settlement founded at {target_name}: {settlement_record['population']} residents"}],
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

    def _advance_time(self, current_time: str, hours: int) -> tuple[str, int]:
        """Returns (new "HH:MM" string, number of days that rolled over).
        Previously this silently dropped the day rollover, so currentDay
        never advanced no matter how many hours/actions passed."""
        parts = current_time.split(":")
        if len(parts) != 2:
            return current_time, 0
        hour = int(parts[0]) + hours
        minute = int(parts[1])
        day_delta = hour // 24
        hour = hour % 24
        return f"{hour:02d}:{minute:02d}", day_delta
