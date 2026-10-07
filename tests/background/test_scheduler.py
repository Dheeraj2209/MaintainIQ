"""Generic background scheduler (src/background/scheduler.py), design decision 7.

Driven with asyncio.run and injected connection factory / broadcast, so no
test ever opens the developer's maintainiq.db.
"""
import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from src.background import scheduler as sched
from src.background.scheduler import BackgroundScheduler, SchedulerSettings

FIXED = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


class Factory:
    """Hands out tracked in-memory connections."""

    def __init__(self):
        self.made = []

    def __call__(self):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.made.append(conn)
        return conn

    def all_closed(self) -> bool:
        for conn in self.made:
            try:
                conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                continue
            return False
        return True


class Broadcast:
    def __init__(self, exc=None):
        self.events = []
        self.exc = exc

    async def __call__(self, event):
        self.events.append(event)
        if self.exc is not None:
            raise self.exc


def _make(interval_s=60.0, **kw):
    factory = kw.pop("factory", Factory())
    broadcast = kw.pop("broadcast", Broadcast())
    s = BackgroundScheduler(interval_s=interval_s, connection_factory=factory,
                            broadcast=broadcast, now_fn=lambda: FIXED, **kw)
    return s, factory, broadcast


# --- settings --------------------------------------------------------------------

def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("MAINTAINIQ_SWEEP_INTERVAL_S", raising=False)
    assert SchedulerSettings.from_env().interval_s == 0.0
    assert not SchedulerSettings.from_env().enabled
    monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", "5")
    assert SchedulerSettings.from_env().interval_s == 5.0
    assert SchedulerSettings.from_env().enabled
    monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", "")
    assert SchedulerSettings.from_env().interval_s == 0.0
    for bad in ("abc", "-1", "nan", "inf"):
        monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", bad)
        with pytest.raises(ValueError):
            SchedulerSettings.from_env()


# --- run_once --------------------------------------------------------------------

def test_run_once_gives_each_job_a_fresh_connection_and_closes_it():
    s, factory, _ = _make()
    seen = []

    def job_a(conn, now):
        seen.append(("a", conn, now))
        return []

    def job_b(conn, now):
        seen.append(("b", conn, now))
        raise RuntimeError("boom")

    s.register("a", job_a)
    s.register("b", job_b)
    assert s.jobs == ("a", "b")
    asyncio.run(s.run_once())
    assert [name for name, _, _ in seen] == ["a", "b"]
    assert seen[0][1] is not seen[1][1]
    assert all(now == FIXED for _, _, now in seen)
    assert len(factory.made) == 2 and factory.all_closed()


def test_events_are_broadcast_in_order():
    s, _, broadcast = _make()
    s.register("one", lambda conn, now: [{"type": "x", "n": 1}, {"type": "x", "n": 2}])
    s.register("two", lambda conn, now: [{"type": "y", "n": 3}])
    returned = asyncio.run(s.run_once())
    assert [e["n"] for e in broadcast.events] == [1, 2, 3]
    assert returned == broadcast.events


def test_failing_job_is_logged_and_others_still_run(caplog):
    s, _, broadcast = _make()
    s.register("bad", lambda conn, now: 1 / 0)
    s.register("good", lambda conn, now: [{"type": "ok"}])
    with caplog.at_level("ERROR", logger="src.background.scheduler"):
        asyncio.run(s.run_once())
    assert broadcast.events == [{"type": "ok"}]
    assert any("bad" in r.getMessage() for r in caplog.records)


def test_failing_broadcast_is_logged_not_raised(caplog):
    s, _, broadcast = _make(broadcast=Broadcast(exc=RuntimeError("socket gone")))
    s.register("j", lambda conn, now: [{"type": "a"}, {"type": "b"}])
    with caplog.at_level("ERROR", logger="src.background.scheduler"):
        asyncio.run(s.run_once())
    assert [e["type"] for e in broadcast.events] == ["a", "b"]
    assert caplog.records


def test_failing_connection_factory_is_logged(caplog):
    def broken():
        raise sqlite3.OperationalError("unable to open database file")

    s, _, broadcast = _make(factory=broken)
    s.register("j", lambda conn, now: [{"type": "a"}])
    with caplog.at_level("ERROR", logger="src.background.scheduler"):
        assert asyncio.run(s.run_once()) == []
    assert broadcast.events == []


# --- loop ---------------------------------------------------------------------------

def test_start_runs_repeatedly_and_stop_is_idempotent():
    calls = []

    async def scenario():
        s, factory, _ = _make(interval_s=0.05)
        s.register("j", lambda conn, now: calls.append(now) or [])
        s.start()
        s.start()  # idempotent
        await asyncio.sleep(0.4)
        await s.stop()
        count = len(calls)
        await asyncio.sleep(0.15)
        await s.stop()
        return count, factory

    count, factory = asyncio.run(scenario())
    assert count >= 2
    assert len(calls) == count  # nothing ran after stop()
    assert factory.all_closed()


def test_first_tick_waits_one_interval():
    calls = []

    async def scenario():
        s, _, _ = _make(interval_s=10.0)
        s.register("j", lambda conn, now: calls.append(now) or [])
        s.start()
        await asyncio.sleep(0.1)
        await s.stop()

    asyncio.run(scenario())
    assert calls == []


def test_ticks_never_overlap():
    import threading
    import time

    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(conn, now):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.08)
        with lock:
            active["now"] -= 1
        return []

    async def scenario():
        s, _, _ = _make(interval_s=0.01)
        s.register("slow", slow)
        s.start()
        await asyncio.sleep(0.35)
        await s.stop()

    asyncio.run(scenario())
    assert active["max"] == 1


def test_accessors():
    s, _, _ = _make()
    try:
        sched.set_scheduler(s)
        assert sched.get_scheduler() is s
    finally:
        sched.set_scheduler(None)
    assert sched.get_scheduler() is None


def test_zero_interval_is_rejected():
    with pytest.raises(ValueError):
        BackgroundScheduler(interval_s=0)


# --- lifespan -----------------------------------------------------------------------

def test_lifespan_leaves_scheduler_off_by_default(client):
    assert sched.get_scheduler() is None
    body = client.get("/api/telemetry/status").json()
    assert body["device_watchdog"]["enabled"] is False


def test_lifespan_starts_and_stops_scheduler_when_enabled(_db_override, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api import app as app_module

    made = []

    def factory():
        made.append(1)
        raise AssertionError("first tick must not happen within a 3600 s interval")

    monkeypatch.setattr(app_module, "_scheduler_connection_factory", factory)
    monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", "3600")
    with TestClient(app_module.app):
        running = sched.get_scheduler()
        assert running is not None and running.jobs == ("device_silence", "alert_paging")
    assert sched.get_scheduler() is None
    assert made == []


def test_lifespan_with_invalid_interval_still_starts(_db_override, monkeypatch):
    from fastapi.testclient import TestClient

    from src.api.app import app

    monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", "soon")
    with TestClient(app) as test_client:
        assert test_client.get("/api/health").status_code == 200
        assert sched.get_scheduler() is None



def test_lifespan_with_invalid_escalation_env_uses_default_policy(_db_override, monkeypatch):
    from fastapi.testclient import TestClient

    from src.alerts import paging
    from src.api import app as app_module

    monkeypatch.setattr(app_module, "_scheduler_connection_factory",
                        lambda: (_ for _ in ()).throw(AssertionError("no tick expected")))
    monkeypatch.setenv("MAINTAINIQ_SWEEP_INTERVAL_S", "3600")
    monkeypatch.setenv("ESCALATION_L1_MINUTES", "soon")
    with TestClient(app_module.app):
        running = sched.get_scheduler()
        assert running is not None and "alert_paging" in running.jobs
        pager = paging.get_pager()
        assert pager is not None and pager.policy == paging.PagingPolicy()
    assert paging.get_pager() is None
