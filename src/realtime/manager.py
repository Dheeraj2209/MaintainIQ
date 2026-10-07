import asyncio
import json
import logging
from typing import Callable

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks live WebSocket connections and fans out JSON events to all of
    them. Broadcasts are not role-filtered — every connected dashboard sees
    every event; only email notifications are role-filtered."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Non-websocket consumers of the same event stream (M6: the MQTT
        # service republishes alert events to devices). A plain list guarded
        # by copy-on-write, not the asyncio lock: listeners are added/removed
        # from the lifespan hook and called from broadcast(), and a list
        # replacement is atomic under the GIL.
        self._listeners: list[Callable[[dict], None]] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    def add_listener(self, listener: Callable[[dict], None]) -> None:
        """Register a synchronous callable invoked with every broadcast event
        (design/M6_LIVE_TELEMETRY.md §5). Must be quick and non-blocking — it
        runs on the API event loop. Adding the same callable twice is a no-op."""
        if listener not in self._listeners:
            self._listeners = [*self._listeners, listener]

    def remove_listener(self, listener: Callable[[dict], None]) -> None:
        """Unregister a listener; unknown listeners are ignored."""
        self._listeners = [fn for fn in self._listeners if fn != listener]

    def _notify_listeners(self, event: dict) -> None:
        # One faulty listener (e.g. a broker hiccup in the MQTT republisher)
        # must neither stop the others nor cost dashboards the event.
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("Realtime listener %r failed", listener)

    async def broadcast(self, event: dict) -> None:
        """`event` is the full wire payload, e.g.
        {"type": "alert_created"|"alert_escalated"|"alert_resolved",
         "machine_id": ..., "alert": {...}, "at": iso_timestamp}."""
        message = json.dumps(event)
        self._notify_listeners(event)
        async with self._lock:
            targets = list(self._connections)

        for websocket in targets:
            try:
                await websocket.send_text(message)
            except Exception:
                await self.disconnect(websocket)

    @property
    def bound_loop(self) -> asyncio.AbstractEventLoop | None:
        """The loop broadcast_threadsafe will target, or None if unbound —
        in which case events from worker threads are silently dropped."""
        return self._loop

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Remember the loop the API runs on, so non-async code can broadcast.
        Called once from the app's startup hook; pass None to unbind."""
        self._loop = loop

    def broadcast_threadsafe(self, event: dict) -> None:
        """Broadcast from a thread that has no event loop of its own.

        The replay ingestion workers (src/ingestion/replay_service.py) are
        plain daemon threads, so they can neither await broadcast() nor use
        asyncio.run() — the connections live on the API's loop, not theirs.

        Fire-and-forget by design: this returns as soon as the coroutine is
        scheduled rather than blocking a prediction worker on socket writes.
        A missing or already-closed loop means nobody is listening (CLI
        replay, tests, shutdown in progress), which is not an error worth
        killing the worker over.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            # No websocket can be listening without a loop, but in-process
            # listeners (the MQTT alert republisher) still can — e.g. during
            # shutdown ordering or a loop-less test harness. Deliver to them
            # directly so a device LED does not miss an alert.
            self._notify_listeners(event)
            return

        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(event), loop)
        except RuntimeError:  # loop stopped between the check and the call
            logger.debug("Dropped realtime event for websockets; event loop is gone")
            self._notify_listeners(event)


manager = ConnectionManager()
