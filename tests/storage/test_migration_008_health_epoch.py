"""Migration 008: health epoch/episode schema and the deploy seed (plan D14)."""
import sqlite3

from src.storage.migrations import LATEST_VERSION, MIGRATIONS, run_migrations


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _v7_db() -> sqlite3.Connection:
    """A DB as it existed before migration 008: steps 1-7 applied and stamped."""
    conn = _mem()
    conn.execute(
        "CREATE TABLE schema_version (version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for target, step in MIGRATIONS:
        if target > 7:
            break
        step(conn)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
    conn.commit()
    assert "health_episode" not in _columns(conn, "alerts")
    return conn


_T = "2026-10-01T00:00:00"


def _machine(conn, machine_id="m1"):
    conn.execute("INSERT OR IGNORE INTO machines (machine_id) VALUES (?)", (machine_id,))


def _alert(conn, *, machine_id="m1", state="critical", source="xjtu_rul", status="open",
           opened_at=_T, resolved_at=None, closed_by=None) -> int:
    _machine(conn, machine_id)
    cur = conn.execute(
        "INSERT INTO alerts (machine_id, opened_at, resolved_at, severity, health_state, "
        "status, source, created_at, closed_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (machine_id, opened_at, resolved_at, "high", state, status, source, opened_at, closed_by),
    )
    return cur.lastrowid


def _maintenance(conn, performed_at, machine_id="m1"):
    _machine(conn, machine_id)
    conn.execute(
        "INSERT INTO maintenance_records (machine_id, performed_at, created_at, type) "
        "VALUES (?,?,?, 'corrective')",
        (machine_id, performed_at, performed_at),
    )


def _seed(conn, machine_id="m1"):
    row = conn.execute(
        "SELECT epoch, episode, max_state, model_version FROM machine_health_state "
        "WHERE machine_id = ?", (machine_id,)).fetchone()
    return None if row is None else tuple(row)


def _episode(conn, alert_id):
    return conn.execute("SELECT health_episode FROM alerts WHERE id = ?",
                        (alert_id,)).fetchone()["health_episode"]


def test_fresh_db_has_health_schema():
    conn = _mem()
    assert run_migrations(conn) == LATEST_VERSION
    assert LATEST_VERSION == 8
    assert {"machine_id", "epoch", "episode", "max_state", "model_version",
            "epoch_started_at", "episode_started_at", "reset_reason",
            "updated_at"} <= _columns(conn, "machine_health_state")
    assert {"health_epoch", "health_episode", "instant_health_state"} <= _columns(conn, "predictions")
    assert "health_episode" in _columns(conn, "alerts")
    assert "resets_health" in _columns(conn, "maintenance_records")
    index = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_predictions_machine_epoch'"
    ).fetchone()
    assert index is not None
    # max_state is constrained; epoch/episode default to 0.
    _machine(conn)
    conn.execute("INSERT INTO machine_health_state (machine_id, max_state, updated_at) "
                 "VALUES ('m1', 'faulty', ?)", (_T,))
    row = conn.execute("SELECT epoch, episode FROM machine_health_state").fetchone()
    assert tuple(row) == (0, 0)
    try:
        conn.execute("INSERT INTO machine_health_state (machine_id, max_state, updated_at) "
                     "VALUES ('m2', 'bogus', ?)", (_T,))
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("max_state CHECK missing")


def test_v7_upgrade_seeds_open_real_alert():
    conn = _v7_db()
    real = _alert(conn, machine_id="m1", state="critical")
    demo = _alert(conn, machine_id="m2", state="critical", source="demo")
    other = _alert(conn, machine_id="m3", state="faulty", source="other_detector")
    conn.commit()

    assert run_migrations(conn) == 8

    assert _seed(conn, "m1") == (0, 0, "critical", None)
    assert _episode(conn, real) == 0
    assert _seed(conn, "m2") is None
    assert _episode(conn, demo) is None
    assert _seed(conn, "m3") is None
    assert _episode(conn, other) is None


def test_v7_upgrade_seeds_worst_open_alert():
    conn = _v7_db()
    _alert(conn, state="degrading")
    worst = _alert(conn, state="faulty")
    conn.commit()
    run_migrations(conn)
    assert _seed(conn) == (0, 0, "faulty", None)
    assert _episode(conn, worst) == 0


def test_v7_upgrade_seeds_human_closed_after_maintenance():
    conn = _v7_db()
    _maintenance(conn, "2026-09-01T00:00:00")
    closed = _alert(conn, state="faulty", status="resolved",
                    resolved_at="2026-10-02T00:00:00", closed_by=1)
    conn.commit()
    run_migrations(conn)
    assert _seed(conn) == (0, 0, "faulty", None)
    assert _episode(conn, closed) == 0


def test_no_seed_when_maintenance_after_human_close():
    conn = _v7_db()
    closed = _alert(conn, state="faulty", status="resolved",
                    resolved_at="2026-10-02T00:00:00", closed_by=1)
    _maintenance(conn, "2026-10-03T00:00:00")
    conn.commit()
    run_migrations(conn)
    assert _seed(conn) is None
    assert _episode(conn, closed) is None


def test_human_closed_seeds_without_any_maintenance():
    conn = _v7_db()
    _alert(conn, state="critical", status="resolved",
           resolved_at="2026-10-02T00:00:00", closed_by=1)
    conn.commit()
    run_migrations(conn)
    assert _seed(conn) == (0, 0, "critical", None)


def test_no_seed_when_newest_resolved_is_system_resolved():
    conn = _v7_db()
    a = _alert(conn, state="critical", status="resolved",
               resolved_at="2026-10-02T00:00:00", closed_by=1)
    b = _alert(conn, state="faulty", status="resolved",
               opened_at="2026-10-03T00:00:00", resolved_at="2026-10-04T00:00:00")
    conn.commit()
    run_migrations(conn)
    assert _seed(conn) is None
    assert _episode(conn, a) is None
    assert _episode(conn, b) is None


def test_no_seed_for_false_alarm_feedback():
    conn = _v7_db()
    fa = _alert(conn, machine_id="m1", state="critical", status="resolved",
                resolved_at="2026-10-02T00:00:00", closed_by=1)
    dq = _alert(conn, machine_id="m2", state="critical", status="resolved",
                resolved_at="2026-10-02T00:00:00", closed_by=1)
    ok = _alert(conn, machine_id="m3", state="critical", status="resolved",
                resolved_at="2026-10-02T00:00:00", closed_by=1)
    sql = ("INSERT INTO alert_feedback (alert_id, outcome, actual_cause, recorded_by, "
           "recorded_at) VALUES (?,?,?,1,?)")
    conn.execute(sql, (fa, "false_alarm", None, _T))
    conn.execute(sql, (dq, "unknown", "sensor_or_data_quality_issue", _T))
    conn.execute(sql, (ok, "confirmed_failure", "bearing_wear", _T))
    conn.commit()
    run_migrations(conn)
    assert _seed(conn, "m1") is None
    assert _seed(conn, "m2") is None
    assert _seed(conn, "m3") == (0, 0, "critical", None)


def test_migration_008_is_idempotent_on_rerun():
    conn = _v7_db()
    real = _alert(conn, state="critical")
    conn.commit()
    step = dict(MIGRATIONS)[8]
    step(conn)
    step(conn)  # a crash before the version stamp re-runs the step cleanly
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM machine_health_state").fetchone()["n"] == 1
    assert _seed(conn) == (0, 0, "critical", None)
    assert _episode(conn, real) == 0
    assert run_migrations(conn) == 8
    assert run_migrations(conn) == 8
    assert conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == 8


def test_legacy_rows_get_null_epoch_and_episode():
    conn = _v7_db()
    _machine(conn)
    conn.execute(
        "INSERT INTO predictions (machine_id, timestamp, health_state, source) "
        "VALUES ('m1', ?, 'healthy', 'xjtu_rul')", (_T,))
    _maintenance(conn, _T)
    conn.commit()
    run_migrations(conn)
    pred = conn.execute(
        "SELECT health_epoch, health_episode, instant_health_state FROM predictions").fetchone()
    assert tuple(pred) == (None, None, None)
    assert conn.execute("SELECT resets_health FROM maintenance_records").fetchone()[0] == 0
