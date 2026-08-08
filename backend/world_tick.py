from __future__ import annotations

import copy
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from world_state import WorldState, append_world_history_event


@dataclass
class WorldEvent:
    day: int
    summary: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)


class WorldEventQueue:
    def __init__(self) -> None:
        self._events: list[WorldEvent] = []

    def add(self, event: WorldEvent) -> None:
        self._events.append(event)
        self._events.sort(key=lambda item: (item.day, item.summary))

    def next_event(self) -> dict[str, Any] | None:
        if not self._events:
            return None
        event = self._events.pop(0)
        return {"day": event.day, "summary": event.summary, "type": event.event_type, "payload": event.payload}

    def has_pending_events(self) -> bool:
        return bool(self._events)


class WorldTick:
    def __init__(self, seed: int = 7) -> None:
        self.seed = seed

    def advance(self, world_state: WorldState | dict[str, Any], event_queue: WorldEventQueue, *, hours: int = 24) -> WorldState:
        state = copy.deepcopy(world_state.to_dict() if hasattr(world_state, "to_dict") else world_state)
        world = WorldState.from_dict(state)

        current_day = world.currentDay
        current_time = world.currentTime
        parts = current_time.split(":") if ":" in current_time else ["00", "00"]
        hour = int(parts[0]) + hours
        new_day = current_day + (hour // 24)
        new_hour = hour % 24
        world.currentDay = new_day
        world.currentTime = f"{new_hour:02d}:{parts[1] if len(parts) > 1 else '00'}"

        weather_options = ["clear", "rain", "storm"]
        world.weather = weather_options[(self._stable_seed(world.currentDay, self.seed) + hours) % len(weather_options)]

        for player in world.players:
            player["hunger"] = max(0, player.get("hunger", 0) - 1)
            player["stress"] = max(0, player.get("stress", 0) + 1)
            player["stamina"] = min(100, player.get("stamina", 0) + 2)

        for location in world.locations:
            location["zombiePopulation"] = max(0, location.get("zombiePopulation", 0) + 1)
            if location.get("zombiePopulation", 0) > 4:
                location["dangerLevel"] = min(5, location.get("dangerLevel", 1) + 1)

        for faction in world.factions:
            faction["resources"] = {
                **faction.get("resources", {}),
                "food": faction.get("resources", {}).get("food", 0) - 1,
            }

        self._simulate_factions(world, hours)

        world.resources["food"] = max(0, world.resources.get("food", 0) - 1)
        world.resources["water"] = max(0, world.resources.get("water", 0) - 1)

        if world.resources.get("food", 0) < 2:
            world.activeEvents.append({"type": "food_shortage", "day": world.currentDay, "summary": "Food supplies are running low."})

        append_world_history_event(world, {
            "type": "world_tick",
            "description": f"World tick advanced {hours} hours.",
            "day": world.currentDay,
            "timestamp": world.currentDay,
        })

        if self._stable_seed(world.currentDay, self.seed + 1) % 2 == 0:
            event_queue.add(WorldEvent(day=world.currentDay + 1, summary="Survivors report strange noise nearby.", event_type="encounter"))
        if self._stable_seed(world.currentDay, self.seed + 2) % 3 == 0:
            event_queue.add(WorldEvent(day=world.currentDay + 2, summary="A supply cache is discovered.", event_type="supply"))

        world.activeEvents.append({"type": "tick", "day": world.currentDay, "summary": "World tick processed."})
        return world

    def _simulate_factions(self, world: WorldState, hours: int) -> None:
        if not world.factions:
            return

        for index, faction in enumerate(world.factions):
            if not isinstance(faction, dict):
                continue
            self._normalize_faction(faction)
            action = self._choose_faction_action(world, faction, hours, index)
            self._apply_faction_action(world, faction, action, hours, index)
            faction["lastAction"] = action
            world.activeEvents.append({
                "type": "faction_action",
                "day": world.currentDay,
                "summary": f"{faction.get('name', faction.get('id', 'Faction'))} chose to {action}.",
            })
            append_world_history_event(world, {
                "type": "faction_action",
                "description": f"Faction {faction.get('id', 'unknown')} performed {action}.",
                "day": world.currentDay,
                "timestamp": world.currentDay,
            })

    def _normalize_faction(self, faction: dict[str, Any]) -> None:
        faction.setdefault("territory", [])
        faction.setdefault("population", 1)
        faction.setdefault("militaryStrength", 1)
        faction.setdefault("resources", {})
        faction.setdefault("ideology", "survival")
        faction.setdefault("goals", [])
        faction.setdefault("relationships", {})

    def _choose_faction_action(self, world: WorldState, faction: dict[str, Any], hours: int, index: int) -> str:
        resources = faction.get("resources", {})
        food = resources.get("food", 0)
        water = resources.get("water", 0)
        military = faction.get("militaryStrength", 1)
        population = faction.get("population", 1)
        ideology = faction.get("ideology", "survival")
        resource_pressure = int(food <= 0) + int(water <= 0)
        player_pressure = sum(
            1
            for item in world.worldHistory[-8:]
            if isinstance(item, dict) and any(word in str(item.get("summary", "")).lower() for word in ("trade", "negotiate", "attack", "recruit", "travel", "build"))
        )
        seed = self._stable_seed(f"{world.currentDay}:{faction.get('id', 'faction')}:{hours}:{index}", self.seed + index)

        if resource_pressure >= 2 and (seed % 5) == 0:
            return "collapse"
        if food <= 1 and (seed % 4) == 0:
            return "raid_settlements"
        if population > 4 and (seed % 7) == 0:
            return "split_into_new_factions"
        if military >= 3 and (seed % 6) == 0 and self._has_other_faction(world, faction):
            return "attack"
        if ideology == "resource_acquisition" and (seed % 5) == 0:
            return "raid_settlements"
        if player_pressure > 0 and (seed % 3) == 0:
            return "negotiate" if (seed % 2) == 0 else "trade"
        if military >= 2 and (seed % 4) == 0:
            return "establish_checkpoints"
        if population > 2 and (seed % 5) == 1:
            return "recruit_survivors"
        if (seed % 3) == 0:
            return "expand_territory"
        return "trade" if self._has_other_faction(world, faction) else "negotiate"

    def _apply_faction_action(self, world: WorldState, faction: dict[str, Any], action: str, hours: int, index: int) -> None:
        resources = faction.setdefault("resources", {})
        food = resources.get("food", 0)
        water = resources.get("water", 0)
        military = faction.get("militaryStrength", 1)
        population = faction.get("population", 1)
        territory = faction.setdefault("territory", [])
        ideology = faction.get("ideology", "survival")
        seed = self._stable_seed(f"{world.currentDay}:{faction.get('id', 'faction')}:{hours}:{index}:{action}", self.seed + index + 11)

        target = self._select_target_faction(world, faction)
        if target is not None and action not in {"attack", "trade", "negotiate", "raid_settlements"}:
            self._update_relationship(world, faction.get("id"), target.get("id"), 1)

        if action == "expand_territory":
            candidates = [location for location in world.locations if location.get("id") not in territory]
            if candidates:
                territory.append(candidates[0].get("id"))
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
        elif action == "recruit_survivors":
            faction["population"] = population + 1
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
        elif action == "attack":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), -20 - (seed % 10))
                self._update_relationship(world, target.get("id"), faction.get("id"), -15 - (seed % 8))
                faction["militaryStrength"] = min(10, military + 1)
                target["militaryStrength"] = max(1, target.get("militaryStrength", 1) - 1)
                resources["food"] = max(0, food - 2)
            else:
                resources["food"] = max(0, food - 1)
        elif action == "trade":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), 10 + (seed % 5))
                self._update_relationship(world, target.get("id"), faction.get("id"), 8 + (seed % 4))
            resources["water"] = water + 1
            resources["food"] = max(0, food - 1)
        elif action == "establish_checkpoints":
            territory = list(territory)
            if len(territory) < 3:
                territory.append(territory[0] if territory else "checkpoint")
            faction["territory"] = territory
            resources["food"] = max(0, food - 1)
            faction["militaryStrength"] = min(10, military + 1)
        elif action == "raid_settlements":
            resources["food"] = food + 2
            resources["water"] = max(0, water - 1)
            faction["militaryStrength"] = min(10, military + 1)
            if ideology != "survival":
                self._update_relationship(world, faction.get("id"), self._first_other_faction_id(world, faction), -12)
        elif action == "negotiate":
            target = self._select_target_faction(world, faction)
            if target is not None:
                self._update_relationship(world, faction.get("id"), target.get("id"), 12 + (seed % 6))
                self._update_relationship(world, target.get("id"), faction.get("id"), 9 + (seed % 4))
            resources["food"] = max(0, food - 1)
        elif action == "collapse":
            faction["population"] = max(1, population // 2)
            faction["militaryStrength"] = max(1, military // 2)
            resources["food"] = max(0, food // 2)
            resources["water"] = max(0, water // 2)
        elif action == "split_into_new_factions":
            new_faction = {
                "id": f"{faction.get('id', 'faction')}_splinter",
                "name": f"{faction.get('name', 'Faction')} Splinter",
                "population": max(1, population // 2),
                "militaryStrength": max(1, military // 2),
                "resources": {key: max(0, value // 2) for key, value in resources.items()},
                "territory": list(territory[:1]),
                "ideology": ideology,
                "relationships": {},
                "goals": list(faction.get("goals", [])),
            }
            faction["population"] = max(1, population - new_faction["population"])
            world.factions.append(new_faction)
        else:
            resources["food"] = max(0, food - 1)

    def _has_other_faction(self, world: WorldState, faction: dict[str, Any]) -> bool:
        return len([item for item in world.factions if isinstance(item, dict) and item.get("id") != faction.get("id")]) > 0

    def _select_target_faction(self, world: WorldState, faction: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [item for item in world.factions if isinstance(item, dict) and item.get("id") != faction.get("id")]
        return candidates[0] if candidates else None

    def _first_other_faction_id(self, world: WorldState, faction: dict[str, Any]) -> str | None:
        target = self._select_target_faction(world, faction)
        return target.get("id") if target is not None else None

    def _update_relationship(self, world: WorldState, source_id: str, target_id: str, delta: int) -> None:
        source = next((item for item in world.factions if isinstance(item, dict) and item.get("id") == source_id), None)
        target = next((item for item in world.factions if isinstance(item, dict) and item.get("id") == target_id), None)
        if source is None or target is None:
            return
        current = source.setdefault("relationships", {}).get(target_id, 0)
        new_score = max(-100, min(100, current + delta))
        source.setdefault("relationships", {})[target_id] = new_score
        world.relationships.append({"from": source_id, "to": target_id, "score": new_score, "delta": delta})

    def _stable_seed(self, day: int, seed: int) -> int:
        digest = hashlib.sha256(f"{day}:{seed}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16)
