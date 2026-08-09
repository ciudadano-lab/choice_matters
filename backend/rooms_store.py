"""
Room persistence. One SQLite row per room, state stored as a JSON blob.
SQLite is genuinely fine at this scale (a handful of friends playing a few
rooms at once) — swap for Postgres later only if this actually needs to
grow beyond that.
"""

import json
import os
import random
import sqlite3
import string
import sys
from contextlib import contextmanager
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from game_data import fresh_room_state
from world_state import normalize_room_state

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_PATH = os.path.join(DATA_DIR, "rooms.db")

ROOM_CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "")  # avoid confusing chars


@contextmanager
def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.commit()


def _generate_code(length=5) -> str:
    return "".join(random.choices(ROOM_CODE_ALPHABET, k=length))


def _serialize_state(state: dict) -> str:
    return json.dumps(normalize_room_state(state), sort_keys=True)


def _seed_from_code(code: str) -> int:
    return sum(ord(ch) for ch in code)


def create_room() -> tuple[str, dict]:
    with get_conn() as conn:
        for _ in range(10):  # collision retry, practically never needed
            code = _generate_code()
            existing = conn.execute("SELECT 1 FROM rooms WHERE code = ?", (code,)).fetchone()
            if not existing:
                break
        else:
            raise RuntimeError("Could not generate a unique room code")

        room_seed = _seed_from_code(code)
        state = fresh_room_state(seed=room_seed)
        state.setdefault("meta", {})
        state["meta"].update({
            "saved": False,
            "save_count": 0,
            "saved_by_slot": None,
            "saved_at": None,
        })
        state = normalize_room_state(state)
        conn.execute("INSERT INTO rooms (code, state) VALUES (?, ?)", (code, _serialize_state(state)))
        conn.commit()
        return code, state


def get_room(code: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT state FROM rooms WHERE code = ?", (code,)).fetchone()
        return normalize_room_state(json.loads(row[0])) if row else None


def save_room(code: str, state: dict) -> None:
    normalized_state = normalize_room_state(state)
    with get_conn() as conn:
        conn.execute("UPDATE rooms SET state = ? WHERE code = ?", (_serialize_state(normalized_state), code))
        conn.commit()


def room_exists(code: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM rooms WHERE code = ?", (code,)).fetchone() is not None
