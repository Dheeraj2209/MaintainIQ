"""System KPIs over live MQTT telemetry (design/M6_LIVE_TELEMETRY.md §9).

Every test seeds telemetry_messages / device_status directly with fixed
timestamps and passes an explicit `now`, so the windowing, clamping and
online-staleness arithmetic is checked exactly rather than "roughly now".
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.kpi import calculations as kpi
from src.storage.db import init_schema

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
KEYS = {"sensor_collection_rate", "transmission_success_rate",
        "edge_buffer_health", "cloud_sync_health"}


def _ts(moment: datetime, style: str = "z") -> str:
    """Write timestamps in both shapes a writer may produce: protocol.format_utc
    ('...000Z') and datetime.isoformat ('+00:00')."""
    if style == "z":
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    return moment.isoformat()


def at(minutes_before_now: float = 0.0, seconds_before_now: float = 0.0) -> datetime:
    return NOW - timedelta(minutes=minutes_before_now, seconds=seconds_before_now)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    yield conn
    conn.close()


def add_msg(conn, device, seq, *, sampled, received=None, boot="b1",
            status="accepted", buffered=False, synced=True, style="z"):
    received = received or sampled
    conn.execute(
        """INSERT INTO telemetry_messages
           (device_id, boot_id, seq, machine_id, sampled_at, received_at,
            buffered, time_synced, status, error)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (device, boot, seq, "sim-01",
         _ts(sampled, style) if sampled is not None else None,
         _ts(received, style), int(buffered), int(synced), status,
         "bad payload" if status == "rejected" else None),
    )


def add_status(conn, device, *, last_seen, online=True, interval=2.0, heartbeat=10.0,
               depth=0, capacity=50, dropped=0, attempts=None, failures=None):
    conn.execute(
        """INSERT INTO device_status
           (device_id, machine_id, boot_id, online, last_seen_at, snapshot_interval_s,
            heartbeat_interval_s, buffer_depth, buffer_capacity, buffer_dropped_total,
            publish_attempts_total, publish_failures_total)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (device, "sim-01", "b1", int(online), _ts(last_seen), interval, heartbeat,
         depth, capacity, dropped, attempts, failures),
    )


def by_device(payload):
    return {d["device_id"]: d for d in payload["devices"]}


# --- not_applicable ---------------------------------------------------------

def test_empty_tables_are_not_applicable(db):
    result = kpi.system_kpis(db, now=NOW)
    assert set(result) == KEYS
    for value in result.values():
        assert value["status"] == "not_applicable"
        assert "just simulate" in value["reason"]


def test_status_only_is_available(db):
    add_status(db, "d1", last_seen=at(seconds_before_now=1))
    result = kpi.system_kpis(db, now=NOW)
    assert set(result) == KEYS
    assert all(v["status"] == "available" and v["window_minutes"] == 60.0
               for v in result.values())
    # Nothing collected but the device is reporting -> visible at rate 0.
    assert result["sensor_collection_rate"]["devices"] == [
        {"device_id": "d1", "collected": 0, "expected": 1800.0, "rate": 0.0}]
    assert result["transmission_success_rate"]["rate"] is None
    assert result["edge_buffer_health"]["buffered_share"] is None
    assert result["cloud_sync_health"]["last_message_age_s"] is None


# --- sensor_collection_rate -------------------------------------------------

def test_collection_rate_full_window_mid_window_clamped_and_partial(db):
    # full: interval 60 s, a snapshot every minute for the whole hour -> 60/60.
    add_status(db, "full", last_seen=at(0), interval=60.0)
    for i in range(60):
        add_msg(db, "full", i, sampled=at(60 - i))
    # Out-of-window snapshot: neither collected nor moves the first-seen time.
    add_msg(db, "full", 999, boot="old", sampled=at(90))

    # late: started at 11:30 -> effective window 1800 s, expected 30.
    add_status(db, "late", last_seen=at(0), interval=60.0)
    for i in range(30):
        add_msg(db, "late", i, sampled=at(30 - i), style="iso")

    # half: started at 11:30, only 15 of 30 expected arrived; a rejected one
    # does not count as collected.
    add_status(db, "half", last_seen=at(0), interval=60.0)
    for i in range(15):
        add_msg(db, "half", i * 2, sampled=at(30 - 2 * i))
    add_msg(db, "half", 1, sampled=at(29), status="rejected")

    # fast: 60 snapshots but configured at 120 s -> 2.0, clamped to 1.0.
    add_status(db, "fast", last_seen=at(0), interval=120.0)
    for i in range(60):
        add_msg(db, "fast", i, sampled=at(60 - i))

    # nointerval: interval unknown -> expected/rate null, excluded from mean.
    add_status(db, "nointerval", last_seen=at(0), interval=None)
    add_msg(db, "nointerval", 0, sampled=at(10))

    result = kpi.system_kpis(db, now=NOW)["sensor_collection_rate"]
    devs = by_device(result)
    assert devs["full"] == {"device_id": "full", "collected": 60, "expected": 60.0, "rate": 1.0}
    assert devs["late"] == {"device_id": "late", "collected": 30, "expected": 30.0, "rate": 1.0}
    assert devs["half"] == {"device_id": "half", "collected": 15, "expected": 30.0, "rate": 0.5}
    assert devs["fast"] == {"device_id": "fast", "collected": 60, "expected": 30.0, "rate": 1.0}
    assert devs["nointerval"] == {"device_id": "nointerval", "collected": 1,
                                  "expected": None, "rate": None}
    assert result["rate"] == pytest.approx((1.0 + 1.0 + 0.5 + 1.0) / 4)


def test_collection_rate_counts_distinct_boot_seq_and_buffered_flush(db):
    add_status(db, "d1", last_seen=at(0), interval=60.0)
    # Device was mid-outage at window start: snapshots sampled 11:00-11:19
    # arrive buffered at 11:40. sampled_at proves it was running all hour, so
    # the effective window is the full 3600 s (expected 60), not 20 min.
    for i in range(20):
        add_msg(db, "d1", i, sampled=at(60 - i), received=at(20), buffered=True)
    # Same seq under a different boot is a different snapshot.
    add_msg(db, "d1", 0, boot="b2", sampled=at(5))
    result = kpi.system_kpis(db, now=NOW)["sensor_collection_rate"]
    d1 = by_device(result)["d1"]
    assert d1["collected"] == 21
    assert d1["expected"] == 60.0
    assert d1["rate"] == pytest.approx(21 / 60, abs=1e-4)


# --- transmission_success_rate ----------------------------------------------

def test_transmission_seq_gaps_are_per_boot(db):
    add_status(db, "d1", last_seen=at(0), attempts=100, failures=5)
    add_status(db, "d2", last_seen=at(0), attempts=300, failures=15)
    # boot b1: 0,1,2,4 -> sent 5, received 4 (seq 3 lost).
    for seq in (0, 1, 2, 4):
        add_msg(db, "d1", seq, boot="b1", sampled=at(50 - seq))
    # reboot: seq restarts at 0 under b2 -> 0,1,2 all received. Pooling the
    # boots would wrongly see max-min+1 = 5 for {0,1,2,4} ∪ {0,1,2}.
    for seq in (0, 1, 2):
        add_msg(db, "d1", seq, boot="b2", sampled=at(20 - seq))
    # Rejected and out-of-window messages never count.
    add_msg(db, "d1", 9, boot="b2", sampled=at(10), status="rejected")
    add_msg(db, "d1", 50, boot="b0", sampled=at(120))
    # d2: 10..19 with 12 and 13 lost.
    for seq in range(10, 20):
        if seq not in (12, 13):
            add_msg(db, "d2", seq, sampled=at(40 - seq))

    result = kpi.system_kpis(db, now=NOW)["transmission_success_rate"]
    assert result["received"] == 7 + 8
    assert result["expected"] == 8 + 10
    assert result["lost"] == 3
    assert result["rate"] == pytest.approx(15 / 18, abs=1e-4)
    assert result["device_reported_failure_rate"] == pytest.approx(20 / 400)
    devs = by_device(result)
    assert devs["d1"]["boots"] == 2
    assert devs["d1"]["lost"] == 1
    assert devs["d1"]["rate"] == pytest.approx(7 / 8)
    assert devs["d1"]["device_reported_failure_rate"] == pytest.approx(0.05)
    assert devs["d2"]["rate"] == pytest.approx(0.8)


# --- edge_buffer_health -----------------------------------------------------

def test_buffered_share_counts_only_accepted_in_window(db):
    add_status(db, "d1", last_seen=at(0))
    add_msg(db, "d1", 0, sampled=at(30))
    add_msg(db, "d1", 1, sampled=at(29))
    add_msg(db, "d1", 2, sampled=at(28))
    add_msg(db, "d1", 3, sampled=at(27), received=at(10), buffered=True)
    add_msg(db, "d1", 4, sampled=at(26), received=at(10), buffered=True, status="rejected")
    add_msg(db, "d1", 5, boot="old", sampled=at(200), received=at(150), buffered=True)
    result = kpi.system_kpis(db, now=NOW)["edge_buffer_health"]
    assert result["buffered_share"] == 0.25


def test_buffer_states_and_worst_of_fleet(db):
    add_status(db, "ok", last_seen=at(0), depth=10, capacity=50)
    add_status(db, "half", last_seen=at(0), depth=25, capacity=50)         # 0.5 -> degraded
    add_status(db, "dropped", last_seen=at(0), depth=0, capacity=50, dropped=3)
    add_status(db, "full", last_seen=at(0), depth=45, capacity=50)         # 0.9 -> critical
    add_status(db, "nocap", last_seen=at(0), depth=None, capacity=None)
    result = kpi.system_kpis(db, now=NOW)["edge_buffer_health"]
    devs = by_device(result)
    assert devs["ok"]["state"] == "ok" and devs["ok"]["utilization"] == 0.2
    assert devs["half"]["state"] == "degraded"
    assert devs["dropped"]["state"] == "degraded"
    assert devs["full"]["state"] == "critical" and devs["full"]["utilization"] == 0.9
    assert devs["nocap"]["state"] == "ok" and devs["nocap"]["utilization"] is None
    assert result["state"] == "critical"
    assert result["dropped_total"] == 3
    assert devs["full"] == {"device_id": "full", "depth": 45, "capacity": 50,
                            "utilization": 0.9, "dropped_total": 0, "state": "critical"}


def test_buffer_fleet_state_degraded_when_no_critical(db):
    add_status(db, "a", last_seen=at(0), depth=1, capacity=50)
    add_status(db, "b", last_seen=at(0), depth=30, capacity=50)
    assert kpi.system_kpis(db, now=NOW)["edge_buffer_health"]["state"] == "degraded"


def test_buffer_state_unknown_without_any_status(db):
    add_msg(db, "d1", 0, sampled=at(1))
    result = kpi.system_kpis(db, now=NOW)["edge_buffer_health"]
    assert result["state"] == "unknown"
    assert result["devices"] == []


# --- cloud_sync_health ------------------------------------------------------

def test_lag_percentiles_exclude_buffered_unsynced_and_rejected(db):
    add_status(db, "d1", last_seen=at(0))
    # Live, synced lags 1..10 s -> nearest-rank p50 = 5, p95 = 10.
    for i, lag in enumerate(range(1, 11)):
        sampled = at(30 - i)
        add_msg(db, "d1", i, sampled=sampled, received=sampled + timedelta(seconds=lag))
    # Excluded: buffered (late by design), unsynced (sampled_at = receive time),
    # rejected.
    add_msg(db, "d1", 20, sampled=at(50), received=at(5), buffered=True)
    add_msg(db, "d1", 21, sampled=at(4), received=at(4), synced=False)
    add_msg(db, "d1", 22, sampled=at(40), received=at(3), status="rejected")
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["lag_p50_s"] == 5.0
    assert result["lag_p95_s"] == 10.0


def test_online_offline_by_staleness_and_lwt(db):
    add_status(db, "fresh", last_seen=at(seconds_before_now=5))       # online
    add_status(db, "stale", last_seen=at(seconds_before_now=31))      # > 3 x 10 s
    add_status(db, "edge", last_seen=at(seconds_before_now=30))       # == 30 s -> offline
    add_status(db, "slowbeat", last_seen=at(seconds_before_now=50), heartbeat=20.0)
    add_status(db, "defaultbeat", last_seen=at(seconds_before_now=25), heartbeat=None)
    add_status(db, "lwt", last_seen=at(0), online=False)              # LWT fired
    # Stale status row, but a telemetry message 2 s ago -> heard from, online.
    add_status(db, "talking", last_seen=at(minutes_before_now=5))
    add_msg(db, "talking", 0, sampled=at(seconds_before_now=3),
            received=at(seconds_before_now=2))
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["devices_total"] == 7
    # fresh, slowbeat, defaultbeat, talking
    assert result["devices_online"] == 4
    assert result["state"] == "degraded"


def test_cloud_sync_down_when_no_device_online(db):
    add_status(db, "a", last_seen=at(0), online=False)
    add_status(db, "b", last_seen=at(minutes_before_now=10))
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["devices_online"] == 0
    assert result["state"] == "down"


def test_rejected_rate_and_state_transitions(db):
    add_status(db, "d1", last_seen=at(0))
    for i in range(19):
        sampled = at(30 - i)
        add_msg(db, "d1", i, sampled=sampled, received=sampled + timedelta(seconds=1))
    add_msg(db, "?", None, sampled=at(1), status="rejected", boot=None)
    # Old rejections outside the window do not count.
    add_msg(db, "?", None, sampled=at(120), status="rejected", boot=None)
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["rejected_rate"] == 0.05      # 1 of 20; not > 0.05
    assert result["state"] == "ok"

    add_msg(db, "?", None, sampled=at(1), status="rejected", boot=None)
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["rejected_rate"] == pytest.approx(2 / 21, abs=1e-4)
    assert result["state"] == "degraded"


def test_cloud_sync_degraded_by_p95_lag(db):
    add_status(db, "d1", last_seen=at(0))
    sampled = at(10)
    add_msg(db, "d1", 0, sampled=sampled, received=sampled + timedelta(seconds=31))
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["lag_p95_s"] == 31.0
    assert result["devices_online"] == 1
    assert result["state"] == "degraded"


def test_last_message_age_is_not_windowed(db):
    add_status(db, "d1", last_seen=at(minutes_before_now=180), online=False)
    add_msg(db, "d1", 0, sampled=at(minutes_before_now=180), style="iso")
    result = kpi.system_kpis(db, now=NOW)
    sync = result["cloud_sync_health"]
    assert sync["last_message_age_s"] == 10800.0
    assert sync["rejected_rate"] == 0.0
    assert sync["lag_p50_s"] is None
    assert sync["state"] == "down"
    assert result["transmission_success_rate"]["received"] == 0


def test_window_minutes_parameter_and_naive_now(db):
    add_status(db, "d1", last_seen=at(0), interval=60.0)
    for i in range(60):
        add_msg(db, "d1", i, sampled=at(60 - i))
    result = kpi.system_kpis(db, now=NOW.replace(tzinfo=None), window_minutes=10.0)
    coll = result["sensor_collection_rate"]
    assert coll["window_minutes"] == 10.0
    # sampled at 11:50 .. 11:59 -> 10 collected out of 600 s / 60 s.
    assert by_device(coll)["d1"]["collected"] == 10
    assert by_device(coll)["d1"]["rate"] == 1.0
    assert result["transmission_success_rate"]["received"] == 10


# --- API ---------------------------------------------------------------------

def test_kpi_detail_system_section_not_applicable_then_available(client, conn):
    system = client.get("/api/kpis/detail").json()["system"]
    assert set(system) == KEYS
    assert all(v["status"] == "not_applicable" for v in system.values())

    real_now = datetime.now(timezone.utc)
    add_status(conn, "d1", last_seen=real_now)
    add_msg(conn, "d1", 0, sampled=real_now - timedelta(seconds=2),
            received=real_now - timedelta(seconds=1))
    conn.commit()

    system = client.get("/api/kpis/detail").json()["system"]
    assert set(system) == KEYS
    assert all(v["status"] == "available" for v in system.values())
    assert system["cloud_sync_health"]["devices_online"] == 1
    assert system["transmission_success_rate"]["rate"] == 1.0


# --- M6 review regressions ----------------------------------------------------


def _add_error_with_reading(conn, device, seq, *, at_time, reading_id):
    conn.execute(
        """INSERT INTO telemetry_messages
           (device_id, boot_id, seq, machine_id, sampled_at, received_at,
            buffered, time_synced, status, error, reading_id)
           VALUES (?, 'b1', ?, 'sim-01', ?, ?, 0, 1, 'error', 'prediction failed', ?)""",
        (device, seq, _ts(at_time), _ts(at_time), reading_id),
    )


def test_prediction_failures_with_stored_reading_count_as_delivered(db):
    """A model outage (reading stored, prediction failed) is not a transport
    loss: alternate seqs failing prediction must not show as lost."""
    add_status(db, "d1", last_seen=at(0), interval=60.0)
    for i in range(10):
        if i % 2:
            _add_error_with_reading(db, "d1", i, at_time=at(10 - i), reading_id=100 + i)
        else:
            add_msg(db, "d1", i, sampled=at(10 - i))
    # An error that never produced a reading (e.g. feature extraction) is lost.
    _add_error_with_reading(db, "d1", 10, at_time=at(0), reading_id=None)
    add_msg(db, "d1", 11, sampled=at(0))
    result = kpi.system_kpis(db, now=NOW)
    tx = result["transmission_success_rate"]
    assert (tx["received"], tx["expected"], tx["lost"]) == (11, 12, 1)
    assert by_device(result["sensor_collection_rate"])["d1"]["collected"] == 11


def test_all_predictions_failing_does_not_zero_transport_kpis(db):
    add_status(db, "d1", last_seen=at(0), interval=60.0)
    for i in range(5):
        _add_error_with_reading(db, "d1", i, at_time=at(5 - i), reading_id=i + 1)
    result = kpi.system_kpis(db, now=NOW)
    assert result["transmission_success_rate"]["rate"] == 1.0
    assert by_device(result["sensor_collection_rate"])["d1"]["collected"] == 5


def test_buffer_unknown_for_device_seen_only_through_snapshots(db):
    """Ingest creates a device_status row (buffer columns NULL) on a device's
    first snapshot; that must read as unknown, not a healthy 'ok'."""
    db.execute("""INSERT INTO device_status (device_id, machine_id, boot_id, online, last_seen_at)
                  VALUES ('snaponly', 'sim-01', 'b1', 0, ?)""", (_ts(at(0)),))
    add_msg(db, "snaponly", 0, sampled=at(0))
    buf = kpi.system_kpis(db, now=NOW)["edge_buffer_health"]
    assert buf["state"] == "unknown"
    assert by_device(buf)["snaponly"]["state"] == "unknown"

    # A device that does report its buffer decides the fleet state.
    add_status(db, "reporting", last_seen=at(0), depth=30, capacity=50)
    buf = kpi.system_kpis(db, now=NOW)["edge_buffer_health"]
    assert buf["state"] == "degraded"
    assert by_device(buf)["snaponly"]["state"] == "unknown"


def test_window_query_uses_the_received_at_index(db):
    lo = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    plan = " ".join(str(tuple(r)) for r in db.execute(
        "EXPLAIN QUERY PLAN " + kpi._WINDOW_MESSAGES_SQL, {"lo": lo}).fetchall())
    assert "idx_telemetry_received" in plan
    assert "SCAN telemetry_messages" not in plan


def test_fast_device_clock_still_found_by_sampled_window(db):
    """Dropping the unindexed `OR sampled_at` arm must not lose a snapshot
    whose device clock runs a little ahead (sampled in window, received just
    before the window start)."""
    add_status(db, "d1", last_seen=at(0), interval=60.0)
    add_msg(db, "d1", 0, sampled=at(59), received=at(61))
    assert by_device(kpi.system_kpis(db, now=NOW)["sensor_collection_rate"])["d1"]["collected"] == 1


def test_devices_online_counts_online_and_stale_via_shared_rule(db):
    """Device-health design decision 3: `devices_online` keeps its old meaning
    (state in {online, stale}) now that it goes through device_health."""
    from src.telemetry import device_health

    add_status(db, "online", last_seen=at(seconds_before_now=5))
    add_status(db, "stale", last_seen=at(seconds_before_now=20))       # 1.5x..3x hb
    add_status(db, "offline", last_seen=at(seconds_before_now=45))
    add_status(db, "lwt", last_seen=at(0), online=False)
    # A row created by a snapshot only (online=0, no status ever) — never heard
    # as "online" either way.
    add_status(db, "never", last_seen=at(0), online=False)
    result = kpi.system_kpis(db, now=NOW)["cloud_sync_health"]
    assert result["devices_total"] == 5
    assert result["devices_online"] == 2
    # The KPI module no longer carries its own copy of the rule's constants.
    assert not hasattr(kpi, "_OFFLINE_AFTER_HEARTBEATS")
    assert not hasattr(kpi, "_DEFAULT_HEARTBEAT_S")
    assert device_health.OFFLINE_AFTER_HEARTBEATS == 3
