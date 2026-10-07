"""ConnectionManager.add_listener / remove_listener (M6 contract §5)."""
import asyncio

from src.realtime.manager import ConnectionManager


def test_broadcast_calls_listeners_with_the_event():
    mgr = ConnectionManager()
    seen = []
    mgr.add_listener(seen.append)
    mgr.add_listener(seen.append)  # idempotent
    event = {"type": "alert_created", "machine_id": "m1"}
    asyncio.run(mgr.broadcast(event))
    assert seen == [event]


def test_failing_listener_is_swallowed_and_others_still_run():
    mgr = ConnectionManager()
    seen = []

    def broken(_event):
        raise RuntimeError("broker down")

    mgr.add_listener(broken)
    mgr.add_listener(seen.append)
    asyncio.run(mgr.broadcast({"type": "alert_resolved", "machine_id": "m1"}))
    assert len(seen) == 1


def test_remove_listener_stops_delivery_and_ignores_unknown():
    mgr = ConnectionManager()
    seen = []
    mgr.add_listener(seen.append)
    mgr.remove_listener(seen.append)
    mgr.remove_listener(print)  # never registered: no error
    asyncio.run(mgr.broadcast({"type": "alert_created", "machine_id": "m1"}))
    assert seen == []


def test_threadsafe_broadcast_without_loop_still_reaches_listeners():
    mgr = ConnectionManager()
    seen = []
    mgr.add_listener(seen.append)
    mgr.broadcast_threadsafe({"type": "alert_created", "machine_id": "m2"})
    assert seen == [{"type": "alert_created", "machine_id": "m2"}]
