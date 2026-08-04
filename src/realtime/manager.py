import asyncio
import json

from fastapi import WebSocket


class ConnectionManager:
    """Tracks live WebSocket connections and fans out JSON events to all of
    them. Broadcasts are not role-filtered — every connected dashboard sees
    every event; only email notifications are role-filtered."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event: dict) -> None:
        """`event` is the full wire payload, e.g.
        {"type": "alert_created"|"alert_escalated"|"alert_resolved",
         "machine_id": ..., "alert": {...}, "at": iso_timestamp}."""
        message = json.dumps(event)
        async with self._lock:
            targets = list(self._connections)

        for websocket in targets:
            try:
                await websocket.send_text(message)
            except Exception:
                await self.disconnect(websocket)


manager = ConnectionManager()
