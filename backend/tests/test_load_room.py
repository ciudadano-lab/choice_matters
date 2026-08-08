import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import rooms_store


def test_load_room_endpoint_returns_saved_state(tmp_path, monkeypatch):
    monkeypatch.setattr(rooms_store, "DB_PATH", str(tmp_path / "rooms.db"))
    rooms_store.init_db()

    code, state = rooms_store.create_room()
    state["meta"]["saved"] = True
    state["meta"]["save_count"] = 1
    rooms_store.save_room(code, state)

    client = TestClient(main.app)
    response = client.get(f"/api/rooms/{code}/load")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == code
    assert payload["saved"] is True
    assert payload["state"]["meta"]["save_count"] == 1
