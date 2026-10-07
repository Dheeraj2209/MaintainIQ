"""AlertPager: the unacknowledged-alert paging ladder
(design/2026-10-07-work-orders-escalation-design.md, decisions 7-8).

Alert 2 (open/high on m1) is the subject. Its created_at is pinned to T0 so
ages are exact; notify is a recorder, never real mail.
"""
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from src.alerts import live, paging
from src.alerts.paging import AlertPager, PagingPolicy

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


class Notify:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def __call__(self, conn, alert, *, roles, page_level):
        self.calls.append((alert["id"], tuple(roles), page_level))
        if self.exc is not None:
            raise self.exc
        return len(roles)


@pytest.fixture
def conn(conn):
    conn.execute("UPDATE alerts SET created_at = ? WHERE id = 2", (T0.isoformat(),))
    conn.commit()
    return conn


def pager(notify=None, **policy):
    notify = notify if notify is not None else Notify()
    return AlertPager(policy=PagingPolicy(**policy), notify=notify), notify


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def level(conn, alert_id=2):
    row = conn.execute("SELECT page_level, last_paged_at FROM alerts WHERE id = ?",
                       (alert_id,)).fetchone()
    return row["page_level"], row["last_paged_at"]


def test_below_l1_nothing_happens(conn):
    p, notify = pager()
    assert p.tick(conn, at(14)) == []
    assert notify.calls == []
    assert level(conn) == (0, None)


def test_l1_pages_supervisors_once(conn):
    p, notify = pager()
    events = p.tick(conn, at(15))
    assert level(conn) == (1, at(15).isoformat())
    assert notify.calls == [(2, ("supervisor",), 1)]
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "alert_paged" and ev["page_level"] == 1 and ev["machine_id"] == "m1"
    assert ev["alert"]["id"] == 2 and ev["alert"]["page_level"] == 1
    assert ev["at"].startswith("2026-10-07T12:15:00")

    assert p.tick(conn, at(16)) == []
    assert len(notify.calls) == 1


def test_l2_pages_admins_then_stops(conn):
    p, notify = pager()
    p.tick(conn, at(15))
    events = p.tick(conn, at(30))
    assert [e["page_level"] for e in events] == [2]
    assert notify.calls[-1] == (2, ("admin",), 2)
    assert p.tick(conn, at(60)) == []
    assert len(notify.calls) == 2
    assert level(conn)[0] == 2


def test_crossing_both_levels_pages_union_once(conn):
    p, notify = pager()
    events = p.tick(conn, at(45))
    assert notify.calls == [(2, ("supervisor", "admin"), 2)]
    assert len(events) == 1 and events[0]["page_level"] == 2


def test_acknowledged_alert_is_never_paged_and_ack_stops_l2(conn):
    p, notify = pager()
    p.tick(conn, at(15))
    live.acknowledge_alert(conn, 2, 1)
    assert p.tick(conn, at(40)) == []
    assert len(notify.calls) == 1
    assert level(conn)[0] == 1  # never lowered


def test_active_work_order_stops_paging_cancelled_does_not(conn):
    conn.execute("INSERT INTO work_orders (alert_id, machine_id, status, title, created_at, updated_at) "
                 "VALUES (2, 'm1', 'open', 't', 'x', 'x')")
    conn.commit()
    p, notify = pager()
    assert p.tick(conn, at(20)) == []
    conn.execute("UPDATE work_orders SET status = 'cancelled'")
    conn.commit()
    assert len(p.tick(conn, at(20))) == 1
    assert notify.calls == [(2, ("supervisor",), 1)]


def test_resolved_alert_is_not_paged(conn):
    conn.execute("UPDATE alerts SET status = 'resolved' WHERE id = 2")
    conn.commit()
    p, notify = pager()
    assert p.tick(conn, at(60)) == []
    assert notify.calls == []


def test_age_uses_created_at_not_opened_at(conn):
    # opened_at is the 2003 reading time from the replayed dataset.
    assert conn.execute("SELECT opened_at FROM alerts WHERE id = 2").fetchone()[0].startswith("2003")
    p, _ = pager()
    assert p.tick(conn, at(1)) == []


@pytest.mark.parametrize("suffix", ["Z", "+00:00", ""])
def test_timestamp_suffixes_parse_the_same(conn, suffix):
    conn.execute("UPDATE alerts SET created_at = ? WHERE id = 2", (f"2026-10-07T12:00:00{suffix}",))
    conn.commit()
    p, _ = pager()
    assert p.tick(conn, at(14)) == []
    assert len(p.tick(conn, at(15))) == 1


def test_unparseable_created_at_is_skipped(conn):
    conn.execute("UPDATE alerts SET created_at = 'not a time' WHERE id = 2")
    conn.commit()
    p, notify = pager()
    assert p.tick(conn, at(60)) == []
    assert notify.calls == []


def test_raising_notify_still_commits_and_emits(conn, caplog):
    p, _ = pager(Notify(exc=RuntimeError("smtp down")))
    with caplog.at_level(logging.ERROR):
        events = p.tick(conn, at(15))
    assert len(events) == 1
    assert level(conn)[0] == 1
    assert "smtp down" in caplog.text or "Failed" in caplog.text


def test_cas_race_sends_nothing(conn, monkeypatch):
    p, notify = pager()

    def bump(c, alert_id):
        other = sqlite3.connect(c.execute("PRAGMA database_list").fetchone()[2])
        other.execute("UPDATE alerts SET page_level = 1 WHERE id = ?", (alert_id,))
        other.commit()
        other.close()

    monkeypatch.setattr(paging, "_before_page", bump)
    assert p.tick(conn, at(15)) == []
    assert notify.calls == []


def test_tick_waits_for_the_transition_lock(db_path, conn):
    p, notify = pager()
    result = {}

    def run():
        c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            result["events"] = p.tick(c, at(15))
        finally:
            c.close()

    with live._TRANSITION_LOCK:
        t = threading.Thread(target=run)
        t.start()
        t.join(0.3)
        assert t.is_alive(), "tick must block while the lock is held"
        assert notify.calls == []
    t.join(10)
    assert len(result["events"]) == 1


def test_pre_v4_file_is_migrated_on_first_tick(tmp_path):
    from src.storage import migrations

    migrations._reset_current_schema_memo()
    try:
        c = sqlite3.connect(tmp_path / "v3.db", check_same_thread=False)
        c.row_factory = sqlite3.Row
        # A v3 file: steps 1-3 stamped, alerts without the paging columns.
        c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, "
                  "applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
        for version, step in migrations.MIGRATIONS[:3]:
            step(c)
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        c.execute("DROP TABLE work_order_events")
        c.execute("DROP TABLE work_orders")
        c.execute("ALTER TABLE alerts DROP COLUMN last_paged_at")
        c.execute("ALTER TABLE alerts DROP COLUMN page_level")
        c.commit()
        assert "page_level" not in {r["name"] for r in c.execute("PRAGMA table_info(alerts)")}
        p, _ = pager()
        assert p.tick(c, at(15)) == []
        assert migrations.current_version(c) == migrations.MIGRATIONS[-1][0]
        assert "page_level" in {r["name"] for r in c.execute("PRAGMA table_info(alerts)")}
    finally:
        migrations._reset_current_schema_memo()


def test_file_without_page_level_returns_nothing():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE alerts (id INTEGER PRIMARY KEY, status TEXT, created_at TEXT)")
    p, notify = pager()
    assert p.tick(c, at(60)) == []
    assert notify.calls == []


def test_status_reports_policy_and_last_tick(conn):
    p, _ = pager(l1_minutes=5, l2_minutes=10)
    p.tick(conn, at(1))
    s = p.status()
    assert s["l1_minutes"] == 5 and s["l2_minutes"] == 10
    assert s["last_tick_at"].startswith("2026-10-07T12:01:00")
    assert s["last_error"] is None


def test_accessors():
    p, _ = pager()
    try:
        paging.set_pager(p)
        assert paging.get_pager() is p
    finally:
        paging.set_pager(None)
    assert paging.get_pager() is None


# --- PagingPolicy -----------------------------------------------------------------

def test_policy_levels_and_roles():
    policy = PagingPolicy()
    assert policy.level_for(timedelta(minutes=14, seconds=59)) == 0
    assert policy.level_for(timedelta(minutes=15)) == 1
    assert policy.level_for(timedelta(minutes=30)) == 2
    assert policy.level_for(timedelta(days=3)) == 2
    assert policy.roles_between(0, 1) == ("supervisor",)
    assert policy.roles_between(1, 2) == ("admin",)
    assert policy.roles_between(0, 2) == ("supervisor", "admin")


def test_policy_from_env(monkeypatch):
    monkeypatch.delenv("ESCALATION_L1_MINUTES", raising=False)
    monkeypatch.delenv("ESCALATION_L2_MINUTES", raising=False)
    assert PagingPolicy.from_env() == PagingPolicy(15.0, 30.0)
    monkeypatch.setenv("ESCALATION_L1_MINUTES", "5")
    monkeypatch.setenv("ESCALATION_L2_MINUTES", "10")
    assert PagingPolicy.from_env() == PagingPolicy(5.0, 10.0)


@pytest.mark.parametrize("l1,l2", [("abc", "30"), ("0", "30"), ("-1", "30"), ("inf", "30"),
                                   ("15", "15"), ("20", "10"), ("15", "nan")])
def test_policy_from_env_rejects_bad_values(monkeypatch, caplog, l1, l2):
    monkeypatch.setenv("ESCALATION_L1_MINUTES", l1)
    monkeypatch.setenv("ESCALATION_L2_MINUTES", l2)
    with pytest.raises(ValueError):
        PagingPolicy.from_env()
    with caplog.at_level(logging.ERROR):
        assert PagingPolicy.from_env_or_default() == PagingPolicy()
    assert "ESCALATION" in caplog.text


# --- Push tier (design/2026-10-07-mobile-operator-pwa-design.md, decision 2) ----------

def test_ladder_push_reaches_the_supervisor_never_the_operator(conn, add_sub, push_enabled,
                                                              monkeypatch):
    from src.notifications import dispatch

    monkeypatch.setattr(dispatch, "send_email", lambda to, subject, body: None)
    for user_id, role in ((1, "admin"), (2, "supervisor"), (3, "operator")):
        add_sub(conn, user_id, f"https://fcm.googleapis.com/fcm/send/{role}")
    p = AlertPager(policy=PagingPolicy(), notify=dispatch.notify_alert)

    p.tick(conn, at(15))

    assert push_enabled.endpoints == ["https://fcm.googleapis.com/fcm/send/supervisor"]
    rows = conn.execute("SELECT recipient_role, channel FROM notifications ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("supervisor", "email"), ("supervisor", "push")]


# --- Delivery off the tick (e2e finding: a sweep sending email inline held
# every alert_paged event, newer pages and the watchdog behind the SMTP) -----

def _add_open_alerts(conn, n):
    for _ in range(n):
        conn.execute(
            """INSERT INTO alerts (machine_id, opened_at, severity, health_state, probable_cause,
                                   message, status, source, created_at)
               VALUES ('m2', ?, 'medium', 'faulty', 'imbalance', 'm2 faulty', 'open', 'mqtt', ?)""",
            (T0.isoformat(), T0.isoformat()),
        )
    conn.commit()


def test_scheduler_broadcasts_pages_before_slow_email_is_sent(db_path, conn):
    import asyncio
    import time

    from src.background.delivery import NotificationWorker
    from src.background.scheduler import BackgroundScheduler

    _add_open_alerts(conn, 4)  # 5 candidates with alert 2
    sent = []

    def slow_notify(c, alert, *, roles, page_level):
        time.sleep(0.4)  # a slow / unreachable SMTP relay
        c.execute("SELECT 1").fetchone()  # the task gets a usable connection
        sent.append((alert["id"], page_level))

    def factory():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    worker = NotificationWorker(connection_factory=factory)
    p = AlertPager(policy=PagingPolicy(), notify=slow_notify, deliver=worker.submit)
    broadcast_at = []
    start = time.perf_counter()

    async def broadcast(event):
        broadcast_at.append(time.perf_counter() - start)

    later_job_ran_at = []
    scheduler = BackgroundScheduler(interval_s=5.0, connection_factory=factory,
                                    broadcast=broadcast, now_fn=lambda: at(45))
    scheduler.register(paging.JOB_NAME, p.tick)
    scheduler.register("next_job", lambda c, now: later_job_ran_at.append(
        time.perf_counter() - start) or [])
    try:
        events = asyncio.run(scheduler.run_once())
        assert len(events) == 5 and all(e["page_level"] == 2 for e in events)
        # Inline delivery took >= 5 x 0.4 s before anything was broadcast.
        assert max(broadcast_at) < 1.0
        assert later_job_ran_at and later_job_ran_at[0] < 1.0
        assert worker.join(10)
        assert sorted(sent) == sorted((e["alert"]["id"], 2) for e in events)
    finally:
        worker.stop()


def test_failing_delivery_queue_still_emits(conn, caplog):
    def broken_deliver(task, description):
        raise RuntimeError("queue gone")

    p = AlertPager(policy=PagingPolicy(), notify=Notify(), deliver=broken_deliver)
    events = p.tick(conn, at(15))
    assert len(events) == 1 and level(conn)[0] == 1
    assert "Could not queue" in caplog.text


def test_inline_mode_sends_after_every_alert_is_committed(conn):
    _add_open_alerts(conn, 2)
    seen_levels = []

    def notify(c, alert, *, roles, page_level):
        # Every candidate is already committed when the first page is sent.
        seen_levels.append([r[0] for r in c.execute(
            "SELECT page_level FROM alerts WHERE status = 'open' ORDER BY id")])

    p = AlertPager(policy=PagingPolicy(), notify=notify)
    assert len(p.tick(conn, at(15))) == 3
    assert seen_levels[0] == [1, 1, 1]


# --- Alerts that predate the ladder are never paged by it ---------------------

def test_upgrade_marks_existing_open_alerts_and_first_tick_pages_nothing(tmp_path):
    from src.storage import migrations

    migrations._reset_current_schema_memo()
    try:
        c = sqlite3.connect(tmp_path / "v3.db", check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL, "
                  "applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
        for version, step in migrations.MIGRATIONS[:3]:
            step(c)
            c.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        c.execute("DROP TABLE work_order_events")
        c.execute("DROP TABLE work_orders")
        c.execute("ALTER TABLE alerts DROP COLUMN last_paged_at")
        c.execute("ALTER TABLE alerts DROP COLUMN page_level")
        # Weeks-old dataset / demo alerts, as in the real maintainiq.db.
        c.executemany(
            """INSERT INTO alerts (machine_id, opened_at, resolved_at, severity, health_state,
                                   message, status, source, created_at)
               VALUES (?, '2003-10-22T13:00:00+00:00', ?, 'high', 'critical', 'x', ?, ?,
                       '2026-07-23T00:00:00+00:00')""",
            [("test1_b1", None, "open", "ml"), ("m1", None, "open", "demo"),
             ("m2", "2003-10-22T14:00:00+00:00", "resolved", "ml")],
        )
        c.commit()
        notify = Notify()
        p = AlertPager(policy=PagingPolicy(), notify=notify)
        assert p.tick(c, at(0)) == []
        assert notify.calls == []
        rows = c.execute("SELECT status, page_level, last_paged_at FROM alerts ORDER BY id").fetchall()
        assert [tuple(r) for r in rows] == [("open", 2, None), ("open", 2, None),
                                            ("resolved", 0, None)]
    finally:
        migrations._reset_current_schema_memo()


def test_new_alert_after_upgrade_is_still_paged(conn):
    # conftest's DB ran migration 4 before its alerts were written: alert 2
    # (created after the ladder existed) is paged as normal.
    assert level(conn) == (0, None)
    p, notify = pager()
    assert len(p.tick(conn, at(15))) == 1


def test_batch_seed_alerts_are_never_paged(conn):
    from src.storage.db import insert_alerts

    insert_alerts(conn, [{
        "machine_id": "m2", "opened_at": "2003-10-22T13:00:00+00:00", "resolved_at": None,
        "severity": "high", "health_state": "critical", "probable_cause": None,
        "message": "m2 critical", "status": "open", "source": "ml",
        "created_at": T0.isoformat(),
    }])
    seeded = conn.execute("SELECT id, page_level, last_paged_at FROM alerts "
                          "WHERE machine_id = 'm2'").fetchone()
    assert (seeded["page_level"], seeded["last_paged_at"]) == (MAX_LEVEL, None)
    p, notify = pager()
    p.tick(conn, at(60))
    assert [call[0] for call in notify.calls] == [2]


MAX_LEVEL = paging.MAX_PAGE_LEVEL


def test_pre_ladder_level_matches_the_ladder_top():
    from src.storage.migrations import PRE_LADDER_PAGE_LEVEL

    assert PRE_LADDER_PAGE_LEVEL == paging.MAX_PAGE_LEVEL
