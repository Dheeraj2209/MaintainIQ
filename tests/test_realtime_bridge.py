"""Tests for ConnectionManager's worker-thread -> event-loop bridge.

The replay ingestion service runs in plain daemon threads with no event loop,
so it cannot await manager.broadcast(). broadcast_threadsafe is how a
prediction made on a worker thread reaches dashboards connected to the
asyncio loop the API runs on.
"""
import asyncio
import threading

import pytest

from src.realtime.manager import ConnectionManager


@pytest.fixture
def mgr():
    manager = ConnectionManager()
    yield manager
    manager.bind_loop(None)


def _run_loop_in_background(manager):
    """Start an event loop on its own thread, bound to `manager`, as the API
    does at startup. Returns (loop, thread, stop)."""
    ready = threading.Event()
    holder = {}

    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        holder["loop"] = loop
        manager.bind_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    ready.wait(timeout=2)

    def _stop():
        holder["loop"].call_soon_threadsafe(holder["loop"].stop)
        thread.join(timeout=2)
        holder["loop"].close()

    return holder["loop"], thread, _stop


def test_broadcast_from_a_worker_thread_reaches_the_event_loop(mgr):
    received = []

    async def _capture(event):
        received.append(event)

    mgr.broadcast = _capture
    _loop, _thread, stop = _run_loop_in_background(mgr)
    try:
        done = threading.Event()

        def _worker():
            mgr.broadcast_threadsafe({"type": "alert_created", "machine_id": "m2"})
            done.set()

        worker = threading.Thread(target=_worker)
        worker.start()
        worker.join(timeout=2)
        assert done.is_set()

        # The coroutine is scheduled on the loop, not run inline on the worker.
        for _ in range(100):
            if received:
                break
            threading.Event().wait(0.01)
    finally:
        stop()

    assert received == [{"type": "alert_created", "machine_id": "m2"}]


def test_broadcast_without_a_bound_loop_is_a_no_op(mgr):
    """Replay started from a CLI or a test has no API event loop. Dropping the
    event is correct; raising would kill the worker's prediction loop."""
    mgr.broadcast_threadsafe({"type": "alert_created", "machine_id": "m2"})


def test_a_background_thread_reaches_a_live_dashboard_websocket(client):
    """End to end over the real app: a replay worker thread broadcasting an
    event must land on a connected dashboard's socket."""
    from src.realtime.manager import manager

    with client.websocket_connect("/api/ws") as ws:
        # Checked first: an unbound loop makes broadcast a silent no-op, and
        # receive_json() below would block forever instead of failing.
        assert manager.bound_loop is not None

        worker = threading.Thread(
            target=manager.broadcast_threadsafe,
            args=({"type": "alert_created", "machine_id": "m2"},),
        )
        worker.start()
        worker.join(timeout=2)

        assert ws.receive_json() == {"type": "alert_created", "machine_id": "m2"}


def test_broadcast_after_the_loop_is_gone_is_a_no_op(mgr):
    _loop, _thread, stop = _run_loop_in_background(mgr)
    stop()

    # Shutdown races replay's final ticks; a closed loop must not raise.
    mgr.broadcast_threadsafe({"type": "alert_resolved", "machine_id": "m2"})
