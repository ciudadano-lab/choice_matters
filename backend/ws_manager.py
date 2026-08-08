"""
Tracks which websocket connections belong to which room, and which player
slot (if any) each connection has claimed. Pure in-memory — fine since a
room's live connections don't need to survive a server restart (the game
state in SQLite does).
"""

from fastapi import WebSocket


class RoomConnectionManager:
    def __init__(self):
        # room_code -> list of {"ws": WebSocket, "slot": int | None}
        self.rooms: dict[str, list[dict]] = {}

    async def connect(self, room_code: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(room_code, []).append({"ws": ws, "slot": None})

    def disconnect(self, room_code: str, ws: WebSocket):
        conns = self.rooms.get(room_code, [])
        self.rooms[room_code] = [c for c in conns if c["ws"] is not ws]

    def claim_slot(self, room_code: str, ws: WebSocket, slot: int):
        for c in self.rooms.get(room_code, []):
            if c["ws"] is ws:
                c["slot"] = slot
                return

    def slot_for(self, room_code: str, ws: WebSocket) -> int | None:
        for c in self.rooms.get(room_code, []):
            if c["ws"] is ws:
                return c["slot"]
        return None

    def claimed_slots(self, room_code: str) -> set[int]:
        return {c["slot"] for c in self.rooms.get(room_code, []) if c["slot"] is not None}

    async def broadcast(self, room_code: str, message: dict):
        dead = []
        for c in self.rooms.get(room_code, []):
            try:
                await c["ws"].send_json(message)
            except Exception:
                dead.append(c["ws"])
        for ws in dead:
            self.disconnect(room_code, ws)


manager = RoomConnectionManager()
