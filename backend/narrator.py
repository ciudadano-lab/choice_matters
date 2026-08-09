from __future__ import annotations

from typing import Any

from simulation_engine import StructuredAction, SimulationResult


def _find_location_name(world_state: dict[str, Any], location_id: str) -> str:
    for location in world_state.get("locations", []):
        if location.get("id") == location_id:
            return location.get("name", location_id)
    return location_id


def _format_resource_changes(changes: dict[str, int]) -> str | None:
    if not changes:
        return None
    parts = []
    for resource, delta in sorted(changes.items()):
        if delta == 0:
            continue
        sign = "+" if delta > 0 else ""
        parts.append(f"{sign}{delta} {resource}")
    if not parts:
        return None
    return ", ".join(parts)


def _format_player_status_changes(changes: list[dict[str, Any]]) -> list[str]:
    phrases: list[str] = []
    for change in changes:
        player_id = change.get("playerId")
        for key, value in change.items():
            if key == "playerId":
                continue
            if key == "location":
                continue
            if isinstance(value, (int, float)):
                name = key.replace("_", " ")
                sign = "increases" if value > 0 else "decreases"
                phrases.append(f"{name.capitalize()} {sign} by {abs(int(value))}.")
            else:
                phrases.append(f"{key.capitalize()} is now {value}.")
    return phrases


def _summarize_world_events(events: list[dict[str, Any]]) -> list[str]:
    if not events:
        return []
    phrases: list[str] = []
    for event in events:
        event_type = event.get("type")
        if event_type == "travel":
            phrases.append("The move completes without incident.")
        elif event_type == "encounter":
            phrases.append("A possible encounter is now in play.")
        elif event_type == "search":
            phrases.append("The area has been scouted.")
        elif event_type == "gather":
            phrases.append("Supplies have been gathered.")
        elif event_type == "rest":
            phrases.append("You take a moment to recover.")
        elif event_type == "build":
            phrases.append("You devote time to construction.")
        elif event_type == "reinforce":
            phrases.append("Defenses have been strengthened.")
        else:
            phrases.append(f"An event of type '{event_type}' occurs.")
    return phrases


def narrate_simulation_result(
    previous_state: dict[str, Any],
    action: StructuredAction,
    result: SimulationResult,
    player_known_info: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    location_name = _find_location_name(previous_state, action.target)

    if action.actionType == "travel":
        lines.append(f"You travel toward {location_name}.")
    elif action.actionType == "search":
        lines.append(f"You search the area at {location_name}.")
    elif action.actionType == "gather":
        lines.append("You spend time gathering supplies.")
    elif action.actionType == "build":
        lines.append("You work on building and fortifying the site.")
    elif action.actionType == "reinforce":
        lines.append("You focus on reinforcing your position.")
    elif action.actionType == "rest":
        lines.append("You take a break to rest and recover.")
    elif action.actionType == "communicate":
        lines.append("You reach out to others and exchange messages.")
    elif action.actionType == "negotiate":
        lines.append("You attempt to negotiate with nearby survivors.")
    elif action.actionType == "attack":
        lines.append(f"You launch an attack on {location_name}.")
    elif action.actionType == "defend":
        lines.append("You prepare to defend your position.")
    elif action.actionType == "trade":
        lines.append("You conduct a trade to shift resources.")
    elif action.actionType == "recruit":
        lines.append("You try to recruit help from others.")
    elif action.actionType == "send_mission":
        lines.append("You send out a mission on your behalf.")
    elif action.actionType == "establish_settlement":
        lines.append("You work to establish a new settlement.")
    else:
        lines.append(f"You perform {action.actionType}.")

    if result.timeConsumed:
        lines.append(f"This takes {result.timeConsumed} time unit{'s' if result.timeConsumed != 1 else ''}.")

    resource_text = _format_resource_changes(result.resourceChanges)
    if resource_text:
        lines.append(f"Resource change: {resource_text}.")

    status_phrases = _format_player_status_changes(result.playerStatusChanges)
    if status_phrases:
        lines.extend(status_phrases)

    if result.locationChanges:
        for change in result.locationChanges:
            loc_name = _find_location_name(previous_state, change.get("locationId", ""))
            if loc_name:
                lines.append(f"{loc_name} is now marked as {change.get('state', 'visited')}.")

    if result.informationDiscovered:
        for info in result.informationDiscovered:
            content = info.get("content")
            if content:
                lines.append(f"You discover: {content}")

    if result.encounterProbability is not None:
        chance = int(result.encounterProbability * 100)
        if chance > 0:
            lines.append(f"There is a {chance}% chance of a hostile encounter.")

    lines.extend(_summarize_world_events(result.worldEventsTriggered))

    if not lines:
        return "The action completes with no notable changes."
    return " ".join(lines)
