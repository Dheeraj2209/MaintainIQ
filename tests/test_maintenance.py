"""Tests for src/maintenance/records.py (M5)."""
from datetime import datetime, timezone

import pytest

from src.maintenance import records as m


def test_log_and_get_history_roundtrip(conn):
    rec = m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", "greased", "tech1")
    assert rec["id"] > 0
    assert rec["machine_id"] == "m1"
    assert rec["performed_at"].startswith("2026-07-20T10:00:00")

    history = m.get_history(conn, "m1")
    assert len(history) == 1
    assert history[0]["description"] == "greased"


def test_history_is_most_recent_first(conn):
    m.log_maintenance(conn, "m1", "2026-07-01T10:00:00Z", "older", "t")
    m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", "newer", "t")
    history = m.get_history(conn, "m1")
    assert [r["description"] for r in history] == ["newer", "older"]


def test_unknown_machine_rejected(conn):
    with pytest.raises(m.MaintenanceError, match="unknown machine_id"):
        m.log_maintenance(conn, "ghost", "2026-07-20T10:00:00Z")


def test_bad_timestamp_rejected(conn):
    with pytest.raises(m.MaintenanceError, match="not a valid ISO-8601"):
        m.log_maintenance(conn, "m1", "not-a-date")


def test_empty_timestamp_rejected(conn):
    with pytest.raises(m.MaintenanceError, match="required"):
        m.log_maintenance(conn, "m1", "")


def test_days_since_none_when_never_serviced(conn):
    assert m.days_since_last_maintenance(conn, "m1") is None
    assert m.last_maintenance_at(conn, "m1") is None


def test_days_since_counts_whole_days(conn):
    m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", "svc", "t")
    fixed_now = datetime(2026, 7, 25, 10, 0, 0, tzinfo=timezone.utc)
    assert m.days_since_last_maintenance(conn, "m1", now=fixed_now) == 5


def test_days_since_uses_latest_record(conn):
    m.log_maintenance(conn, "m1", "2026-07-01T10:00:00Z", "old", "t")
    m.log_maintenance(conn, "m1", "2026-07-24T10:00:00Z", "new", "t")
    fixed_now = datetime(2026, 7, 25, 10, 0, 0, tzinfo=timezone.utc)
    assert m.days_since_last_maintenance(conn, "m1", now=fixed_now) == 1


def test_alert_id_accepted_when_it_belongs_to_the_machine(conn):
    rec = m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", alert_id=2, type="corrective")
    assert rec["alert_id"] == 2
    assert rec["type"] == "corrective"


def test_alert_id_rejected_when_unknown(conn):
    with pytest.raises(m.MaintenanceError, match="does not belong"):
        m.log_maintenance(conn, "m1", "2026-07-20T10:00:00Z", alert_id=9999)


def test_alert_id_rejected_when_belongs_to_a_different_machine(conn):
    with pytest.raises(m.MaintenanceError, match="does not belong"):
        m.log_maintenance(conn, "m2", "2026-07-20T10:00:00Z", alert_id=2)


def test_get_history_pagination(conn):
    for i in range(3):
        m.log_maintenance(conn, "m1", f"2026-07-{10 + i:02d}T10:00:00Z", description=str(i))
    page1 = m.get_history(conn, "m1", limit=2, offset=0)
    page2 = m.get_history(conn, "m1", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1


def test_get_history_unbounded_when_limit_omitted(conn):
    for i in range(3):
        m.log_maintenance(conn, "m1", f"2026-07-{10 + i:02d}T10:00:00Z")
    assert len(m.get_history(conn, "m1")) == 3
