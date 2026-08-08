from world_state import WorldState, append_world_history_event, normalize_world_history


def test_append_world_history_event_creates_linked_graph_nodes():
    world_state = WorldState.from_dict({"currentDay": 15, "currentTime": "08:00", "worldHistory": []})

    first = append_world_history_event(world_state, {
        "type": "player_decision",
        "description": "A survivor chose to fortify the shelter.",
        "day": 15,
        "timestamp": 15,
    })
    second = append_world_history_event(world_state, {
        "type": "faction_conflict",
        "description": "The Wardens attacked the eastern settlement.",
        "day": 100,
        "timestamp": 100,
        "causes": [first["eventId"]],
    })

    assert first["eventId"] in second["causes"]
    assert second["eventId"] in first["effects"]
    assert second["description"].startswith("The Wardens")


def test_normalize_world_history_converts_legacy_entries_to_structured_events():
    history = [{"day": 1, "summary": "The outbreak begins."}]
    normalized = normalize_world_history(history)

    assert len(normalized) == 1
    assert normalized[0]["type"] == "world_event"
    assert normalized[0]["description"] == "The outbreak begins."
    assert normalized[0]["eventId"].startswith("event_")
