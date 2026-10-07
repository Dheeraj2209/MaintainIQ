"""DeviceSilenceWatcher: opens / resolves device_incidents (design decisions 4-9).

Seeds device_status directly (as tests/telemetry/test_telemetry_route.py
does) with `payload_json` set for nodes that have reported a status, drives
tick() with a fixed `now`, and records notify calls instead of sending mail.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.telemetry import protocol
from src.telemetry.watchdog import REPAGE_COOLDOWN_S, DeviceSilenceWatcher

T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


class Notify:
    def __init__(self, exc: Exception | None = None):
        self.calls = []
        self.exc = exc

    def __call__(self, conn, incident):
        self.calls.append(dict(incident))
        if self.exc is not None:
            raise self.exc
        return 2


def seed(conn, device_id, *, seconds_ago, now=T0, online=1, hb=10.0, machine_id="sim-01",
         reported=True):
    conn.execute(
        """INSERT INTO device_status (device_id, machine_id, online, last_seen_at,
                                      heartbeat_interval_s, payload_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(device_id) DO UPDATE SET
               online = excluded.online, last_seen_at = excluded.last_seen_at,
               heartbeat_interval_s = excluded.heartbeat_interval_s,
               payload_json = excluded.payload_json, machine_id = excluded.machine_id""",
        (device_id, machine_id, online, protocol.format_utc(now - timedelta(seconds=seconds_ago)),
         hb, '{"online": true}' if reported else None),
    )
    conn.commit()


def incidents(conn, device_id=None):
    sql = "SELECT * FROM device_incidents"
    params = ()
    if device_id:
        sql += " WHERE device_id = ?"
        params = (device_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]


def watcher(notify=None, *, connected_for=3600.0, grace_s=30.0, **kw):
    notify = notify if notify is not None else Notify()
    w = DeviceSilenceWatcher(grace_s=grace_s, ingest_connected_for=lambda: connected_for,
                             notify=notify, **kw)
    return w, notify


def test_silent_node_opens_incident_event_and_page(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, notify = watcher()
    events = w.tick(conn, T0)
    rows = incidents(conn)
    assert len(rows) == 1
    inc = rows[0]
    assert (inc["device_id"], inc["machine_id"], inc["kind"], inc["status"]) == (
        "dev-a", "sim-01", "silent", "open")
    assert inc["opened_at"] == protocol.format_utc(T0) == inc["created_at"]
    assert inc["last_seen_at"] == protocol.format_utc(T0 - timedelta(seconds=40))
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "device_offline" and ev["device_id"] == "dev-a"
    assert ev["machine_id"] == "sim-01" and ev["at"] == protocol.format_utc(T0)
    assert ev["incident"]["id"] == inc["id"] and ev["incident"]["status"] == "open"
    assert set(ev["incident"]) == {
        "id", "device_id", "machine_id", "kind", "status", "opened_at", "last_seen_at",
        "resolved_at", "acknowledged_at", "acknowledged_by",
    }
    assert len(notify.calls) == 1 and notify.calls[0]["id"] == inc["id"]


def test_lwt_opens_on_first_armed_tick(conn):
    seed(conn, "dev-l", seconds_ago=0, online=0)
    w, notify = watcher()
    events = w.tick(conn, T0)
    assert [r["kind"] for r in incidents(conn)] == ["lwt"]
    assert [e["type"] for e in events] == ["device_offline"]
    assert len(notify.calls) == 1


def test_stale_and_never_reported_open_nothing(conn):
    seed(conn, "dev-s", seconds_ago=20)                    # stale
    seed(conn, "dev-n", seconds_ago=3600, reported=False)  # never_reported
    seed(conn, "dev-o", seconds_ago=1)                     # online
    w, notify = watcher()
    assert w.tick(conn, T0) == []
    assert incidents(conn) == [] and notify.calls == []


def test_second_tick_does_not_duplicate(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, notify = watcher()
    w.tick(conn, T0)
    assert w.tick(conn, T0 + timedelta(seconds=5)) == []
    assert len(incidents(conn)) == 1 and len(notify.calls) == 1


def test_unique_index_and_race_tolerance(conn):
    now = protocol.format_utc(T0)
    sql = ("INSERT INTO device_incidents (device_id, kind, status, opened_at, created_at) "
           "VALUES ('dev-a', 'silent', 'open', ?, ?)")
    conn.execute(sql, (now, now))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (now, now))
    conn.rollback()

    class Racing(DeviceSilenceWatcher):
        """Another instance opens the incident between our read and INSERT."""

        def _open_incidents(self, conn):
            return {}

    seed(conn, "dev-a", seconds_ago=40)
    notify = Notify()
    w = Racing(grace_s=0, ingest_connected_for=lambda: 10.0, notify=notify)
    assert w.tick(conn, T0) == []
    assert notify.calls == [] and len(incidents(conn)) == 1


def test_heard_again_resolves_and_keeps_ack(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, _ = watcher()
    w.tick(conn, T0)
    inc_id = incidents(conn)[0]["id"]
    conn.execute("UPDATE device_incidents SET acknowledged_at = ?, acknowledged_by = 3 WHERE id = ?",
                 ("2026-10-06T12:00:02.000Z", inc_id))
    conn.commit()

    later = T0 + timedelta(seconds=10)
    seed(conn, "dev-a", seconds_ago=0, now=later)
    events = w.tick(conn, later)
    row = incidents(conn)[0]
    assert row["status"] == "resolved" and row["resolved_at"] == protocol.format_utc(later)
    assert row["acknowledged_at"] == "2026-10-06T12:00:02.000Z" and row["acknowledged_by"] == 3
    assert [e["type"] for e in events] == ["device_online"]
    assert events[0]["incident"]["status"] == "resolved"
    assert events[0]["incident"]["acknowledged_by"] == 3


def test_stale_counts_as_heard_for_resolution(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, _ = watcher()
    w.tick(conn, T0)
    later = T0 + timedelta(seconds=60)
    seed(conn, "dev-a", seconds_ago=20, now=later)  # stale, but heard
    assert [e["type"] for e in w.tick(conn, later)] == ["device_online"]


def test_later_silence_opens_a_new_incident(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, _ = watcher()
    w.tick(conn, T0)
    t1 = T0 + timedelta(seconds=10)
    seed(conn, "dev-a", seconds_ago=0, now=t1)
    w.tick(conn, t1)
    t2 = T0 + timedelta(minutes=30)
    seed(conn, "dev-a", seconds_ago=45, now=t2)
    events = w.tick(conn, t2)
    rows = incidents(conn)
    assert [r["status"] for r in rows] == ["resolved", "open"]
    assert rows[1]["id"] != rows[0]["id"]
    assert events[0]["incident"]["id"] == rows[1]["id"]


@pytest.mark.parametrize("connected_for", [None, 0.0, 10.0])
def test_not_armed_does_nothing(conn, connected_for):
    seed(conn, "dev-a", seconds_ago=40)
    seed(conn, "dev-b", seconds_ago=0)
    now = protocol.format_utc(T0)
    conn.execute("INSERT INTO device_incidents (device_id, kind, status, opened_at, created_at) "
                 "VALUES ('dev-b', 'silent', 'open', ?, ?)", (now, now))
    conn.commit()
    w, notify = watcher(connected_for=connected_for, grace_s=30.0)
    assert not w.armed()
    assert w.tick(conn, T0) == []
    assert [(r["device_id"], r["status"]) for r in incidents(conn)] == [("dev-b", "open")]
    assert notify.calls == []
    assert w.status()["last_tick_at"] == now and w.status()["armed"] is False


def test_grace_window_boundary(conn):
    seed(conn, "dev-a", seconds_ago=40)
    connected = {"s": 10.0}
    notify = Notify()
    w = DeviceSilenceWatcher(grace_s=30.0, ingest_connected_for=lambda: connected["s"], notify=notify)
    assert w.tick(conn, T0) == []
    connected["s"] = 30.0
    assert w.armed()
    assert [e["type"] for e in w.tick(conn, T0)] == ["device_offline"]


def test_flap_guard_suppresses_repage_within_cooldown(conn):
    assert REPAGE_COOLDOWN_S == 900
    w, notify = watcher()

    def cycle(open_at):
        seed(conn, "dev-a", seconds_ago=40, now=open_at)
        events = w.tick(conn, open_at)
        back = open_at + timedelta(seconds=5)
        seed(conn, "dev-a", seconds_ago=0, now=back)
        w.tick(conn, back)
        return events

    assert len(cycle(T0)) == 1
    assert len(notify.calls) == 1
    # Re-opened 5 min later: still recorded and broadcast, not emailed.
    assert [e["type"] for e in cycle(T0 + timedelta(minutes=5))] == ["device_offline"]
    assert len(notify.calls) == 1
    # 16 min after the previous opening: paged again.
    cycle(T0 + timedelta(minutes=21))
    assert len(notify.calls) == 2
    assert len(incidents(conn)) == 3


def test_failing_notify_keeps_the_incident(conn, caplog):
    seed(conn, "dev-a", seconds_ago=40)
    w, notify = watcher(Notify(exc=RuntimeError("smtp down")))
    with caplog.at_level("ERROR", logger="src.telemetry.watchdog"):
        events = w.tick(conn, T0)
    assert [e["type"] for e in events] == ["device_offline"]
    assert [r["status"] for r in incidents(conn)] == ["open"]
    assert any("smtp down" in (r.exc_text or "") or r.exc_info for r in caplog.records)


def test_pre_v3_db_is_migrated_by_first_armed_tick(conn):
    from src.storage.migrations import MIGRATIONS, current_version

    conn.execute("DROP TABLE device_incidents")
    conn.execute("DELETE FROM schema_version WHERE version >= 3")
    conn.commit()
    seed(conn, "dev-a", seconds_ago=40)

    # Not armed: no migration side effect either.
    idle, _ = watcher(connected_for=None)
    assert idle.tick(conn, T0) == []
    assert current_version(conn) == 2

    w, _ = watcher()
    events = w.tick(conn, T0)
    assert current_version(conn) == MIGRATIONS[-1][0]
    assert [e["type"] for e in events] == ["device_offline"]


def test_missing_tables_return_empty_without_raising(monkeypatch):
    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    w, _ = watcher()
    assert w.tick(empty, T0) == []  # v0: tables get created, nothing to watch

    from src.telemetry import watchdog

    def boom(conn):
        raise sqlite3.OperationalError("disk I/O error")

    other = sqlite3.connect(":memory:")
    other.row_factory = sqlite3.Row
    monkeypatch.setattr(watchdog, "ensure_telemetry_schema", boom)
    w2, _ = watcher()
    assert w2.tick(other, T0) == []
    assert "disk I/O error" in (w2.status()["last_error"] or "")


@pytest.mark.parametrize("machine_id", ["sim-07", None])
def test_machine_id_is_copied(conn, machine_id):
    seed(conn, "dev-a", seconds_ago=40, machine_id=machine_id)
    w, _ = watcher()
    events = w.tick(conn, T0)
    assert incidents(conn)[0]["machine_id"] == machine_id
    assert events[0]["machine_id"] == machine_id


def test_status_shape():
    w, _ = watcher(grace_s=12.0)
    assert w.status() == {"armed": True, "grace_s": 12.0, "last_tick_at": None, "last_error": None}


def test_runs_as_a_scheduler_job_end_to_end(db_path, conn):
    """The watcher's tick() as registered by the app lifespan: own connection
    per tick, events broadcast by the scheduler."""
    import asyncio

    from src.background.scheduler import BackgroundScheduler

    seed(conn, "dev-a", seconds_ago=40)
    sent = []

    async def broadcast(event):
        sent.append(event)

    def factory():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    w, notify = watcher()
    s = BackgroundScheduler(interval_s=5.0, connection_factory=factory, broadcast=broadcast,
                            now_fn=lambda: T0)
    s.register("device_silence", w.tick)
    asyncio.run(s.run_once())
    assert [e["type"] for e in sent] == ["device_offline"]
    assert len(notify.calls) == 1
    assert [r["status"] for r in incidents(conn)] == ["open"]


class FlakyConn:
    """Proxies a sqlite3 connection, raising `exc` on the `nth` execute()
    whose SQL contains `needle` (sqlite3.Connection.execute is read-only, so
    it cannot be monkeypatched in place)."""

    def __init__(self, conn, needle, *, nth=1, exc=None):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_needle", needle)
        object.__setattr__(self, "_nth", nth)
        object.__setattr__(self, "_seen", 0)
        object.__setattr__(self, "_exc", exc or sqlite3.OperationalError("database is locked"))

    def execute(self, sql, *args):
        if self._needle in sql:
            object.__setattr__(self, "_seen", self._seen + 1)
            if self._seen == self._nth:
                raise self._exc
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        setattr(self._conn, name, value)


def test_failure_on_one_device_keeps_events_already_committed(conn):
    """A 'database is locked' on device B must not drop device A's committed
    incident event: later ticks see A's incident as open and never re-emit it."""
    seed(conn, "dev-a", seconds_ago=40)
    seed(conn, "dev-b", seconds_ago=40)
    w, notify = watcher()
    flaky = FlakyConn(conn, "INSERT INTO device_incidents", nth=2)
    events = w.tick(flaky, T0)
    assert [(e["type"], e["device_id"]) for e in events] == [("device_offline", "dev-a")]
    assert "database is locked" in (w.status()["last_error"] or "")
    assert [n["device_id"] for n in notify.calls] == ["dev-a"]
    assert [r["device_id"] for r in incidents(conn)] == ["dev-a"]

    # The failed device is retried on the next tick.
    events = w.tick(conn, T0 + timedelta(seconds=5))
    assert [(e["type"], e["device_id"]) for e in events] == [("device_offline", "dev-b")]
    assert w.status()["last_error"] is None


def test_failure_after_a_resolve_keeps_the_online_event(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, _ = watcher()
    w.tick(conn, T0)
    seed(conn, "dev-a", seconds_ago=0, now=T0 + timedelta(seconds=60))  # heard again
    seed(conn, "dev-b", seconds_ago=40, now=T0 + timedelta(seconds=60))  # goes silent
    flaky = FlakyConn(conn, "INSERT INTO device_incidents")
    events = w.tick(flaky, T0 + timedelta(seconds=60))
    assert [(e["type"], e["device_id"]) for e in events] == [("device_online", "dev-a")]
    assert [r["status"] for r in incidents(conn, "dev-a")] == ["resolved"]


def test_cooldown_check_failure_still_pages_and_emits(conn, monkeypatch):
    seed(conn, "dev-a", seconds_ago=40)
    w, notify = watcher()

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(w, "_recently_paged", boom)
    events = w.tick(conn, T0)
    assert [e["type"] for e in events] == ["device_offline"]
    assert len(notify.calls) == 1
    assert [r["status"] for r in incidents(conn)] == ["open"]


def test_reread_failure_after_insert_still_emits_the_event(conn):
    seed(conn, "dev-a", seconds_ago=40)
    w, notify = watcher()
    flaky = FlakyConn(conn, "FROM device_incidents WHERE id = ?")
    events = w.tick(flaky, T0)
    assert [e["type"] for e in events] == ["device_offline"]
    inc = events[0]["incident"]
    row = incidents(conn)[0]
    assert inc["id"] == row["id"] and inc["status"] == "open" and inc["kind"] == "silent"
    assert inc["opened_at"] == row["opened_at"] and inc["acknowledged_at"] is None
    assert len(notify.calls) == 1


def test_deliver_hands_the_page_to_the_delivery_thread(conn):
    # With a deliver callable the tick does not send the page itself (a slow
    # SMTP server must not stall the scheduler); the queued task does.
    seed(conn, "dev-q", seconds_ago=40)
    queued = []
    w, notify = watcher(deliver=lambda task, description: queued.append((task, description)))
    events = w.tick(conn, T0)
    assert [e["type"] for e in events] == ["device_offline"]
    assert notify.calls == [] and len(queued) == 1
    task, description = queued[0]
    assert "device incident" in description
    task(conn)
    assert len(notify.calls) == 1 and notify.calls[0]["device_id"] == "dev-q"
