import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import rooms_store
from world_state import WorldState


def test_save_room_endpoint_marks_room_as_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(rooms_store, "DB_PATH", str(tmp_path / "rooms.db"))
    rooms_store.init_db()

    code, state = rooms_store.create_room()
    client = TestClient(main.app)

    response = client.post(
        f"/api/rooms/{code}/save",
        json={"state": state, "saved_by_slot": 1},
    )

    assert response.status_code == 200
    saved_state = rooms_store.get_room(code)
    assert saved_state["meta"]["saved"] is True
    assert saved_state["meta"]["save_count"] == 1
    assert saved_state["meta"]["saved_by_slot"] == 1


def test_world_state_round_trips_through_room_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(rooms_store, "DB_PATH", str(tmp_path / "rooms.db"))
    rooms_store.init_db()

    code, state = rooms_store.create_room()
    world_state = WorldState.from_dict(state["world_state"])
    assert world_state.currentDay == 1
    assert world_state.players[0]["id"] == "player_1"

    state["world_state"]["weather"] = "stormy"
    rooms_store.save_room(code, state)

    loaded_state = rooms_store.get_room(code)
    assert loaded_state["world_state"]["weather"] == "stormy"
    assert loaded_state["world_state"]["currentDay"] == 1
    assert loaded_state["world_state"]["players"][0]["id"] == "player_1"
