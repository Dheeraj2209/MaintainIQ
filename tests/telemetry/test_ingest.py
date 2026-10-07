"""TelemetryIngestor against a real temp SQLite DB (tests/conftest.py db_path).

The predictor is faked (the ML model is out of scope here and may be absent);
everything downstream of it — rul_store persistence, telemetry_messages,
device_status — runs for real.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.telemetry import protocol
from src.telemetry.ingest import TelemetryIngestor, device_online

PREFIX = "maintainiq/v1"
T0 = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


class FakePredictor:
    model_version = "fake-v1"

    def __init__(self, health_state="faulty", fail: Exception | None = None):
        self.calls = []
        self.health_state = health_state
        self.fail = fail

    def predict(self, machine_id, horizontal, vertical, sample_rate_hz, speed_rpm, load_kn):
        self.calls.append((machine_id, len(horizontal), sample_rate_hz, speed_rpm, load_kn))
        if self.fail is not None:
            raise self.fail
        return {
            "machine_id": machine_id,
            "predicted_rul_minutes": 42.0,
            "rul_estimate_kind": "point_estimate",
            "prognostic_horizon_minutes": 720.0,
            "failure_within_horizon_probability": 0.9,
            "prediction_interval_90_minutes": [30.0, 54.0],
            "health_state": self.health_state,
            "model_version": "test-model-v1",
            "history_snapshots": len(self.calls),
            "out_of_distribution": False,
            "warnings": [],
        }


def _conn_factory(db_path):
    def factory():
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c
    return factory


@pytest.fixture
def db(db_path):
    c = _conn_factory(db_path)()
    yield c
    c.close()


def _ingestor(db_path, predictor=None, fan_out=None, **kwargs):
    predictor = predictor or FakePredictor()
    calls = []

    def default_fan_out(conn, result, features, timestamp, **_):
        calls.append((result, features, timestamp))
        return {"event": None}

    ing = TelemetryIngestor(
        predictor_provider=lambda: predictor,
        connection_factory=_conn_factory(db_path),
        topic_prefix=PREFIX,
        on_prediction=fan_out or default_fan_out,
        **kwargs,
    )
    return ing, predictor, calls


def _snapshot(machine_id="sim-01", seq=0, device_id="simdev-01", boot_id="boot1", **kw):
    rng = np.random.default_rng(seq)
    n = kw.pop("n", 256)
    defaults = dict(
        horizontal=rng.normal(0, 1, n), vertical=rng.normal(0, 1, n),
        sample_rate_hz=25600.0, speed_rpm=2100.0, load_kn=12.0,
        sampled_at=T0 + timedelta(seconds=2 * seq),
    )
    defaults.update(kw)
    return (protocol.telemetry_topic(machine_id, PREFIX),
            protocol.build_snapshot_payload(device_id=device_id, machine_id=machine_id,
                                            boot_id=boot_id, seq=seq, **defaults))


def _messages(db):
    return [dict(r) for r in db.execute("SELECT * FROM telemetry_messages ORDER BY id")]


def test_accepted_snapshot_writes_reading_prediction_and_message(db_path, db):
    ing, pred, fan = _ingestor(db_path)
    topic, payload = _snapshot(seq=0)
    out = ing.handle_snapshot(topic, payload, T0 + timedelta(seconds=1))

    assert out.status == "accepted" and out.reading_id is not None
    assert len(pred.calls) == 1 and pred.calls[0][0] == "sim-01"

    machine = db.execute("SELECT * FROM machines WHERE machine_id='sim-01'").fetchone()
    assert machine["dataset"] == "live_mqtt" and machine["bearing_id"] == "sim-01"
    assert machine["speed_rpm"] == 2100.0 and machine["is_documented_failure"] == 0

    reading = db.execute("SELECT * FROM readings WHERE id=?", (out.reading_id,)).fetchone()
    assert reading["dataset"] == "live_mqtt" and reading["cycle"] == 0
    assert reading["elapsed_minutes"] == 0.0 and reading["rul_minutes"] is None
    assert reading["timestamp"] == "2026-10-06T12:00:00.000Z"
    features = json.loads(reading["features_json"])
    assert reading["vibration_h_rms"] == pytest.approx(features["h_rms"])
    assert reading["cross_axis_correlation"] == pytest.approx(features["cross_axis_correlation"])

    prediction = db.execute("SELECT * FROM predictions WHERE reading_id=?", (out.reading_id,)).fetchone()
    assert prediction["model_version"] == "test-model-v1"
    log = db.execute("SELECT status FROM model_inference_log WHERE machine_id='sim-01'").fetchall()
    assert [r["status"] for r in log] == ["ok"]

    (msg,) = _messages(db)
    assert msg["status"] == "accepted" and msg["reading_id"] == out.reading_id
    assert (msg["device_id"], msg["boot_id"], msg["seq"]) == ("simdev-01", "boot1", 0)
    assert msg["sampled_at"] == "2026-10-06T12:00:00.000Z"
    assert msg["received_at"] == "2026-10-06T12:00:01.000Z"
    assert msg["payload_bytes"] == len(payload) and msg["latency_ms"] >= 0
    assert msg["buffered"] == 0 and msg["time_synced"] == 1

    # fan-out got the result, the base features and the capture timestamp
    assert len(fan) == 1
    assert fan[0][0]["machine_id"] == "sim-01" and fan[0][1] == features
    assert fan[0][2] == "2026-10-06T12:00:00.000Z"

    dev = db.execute("SELECT * FROM device_status WHERE device_id='simdev-01'").fetchone()
    assert dev["last_seen_at"] == "2026-10-06T12:00:01.000Z" and dev["machine_id"] == "sim-01"
    assert dev["online"] == 0  # only a status message can declare a device online (§4)


def test_cycle_and_elapsed_minutes_advance(db_path, db):
    ing, _pred, _fan = _ingestor(db_path)
    for seq in range(3):
        topic, payload = _snapshot(seq=seq, sampled_at=T0 + timedelta(minutes=seq * 1.5))
        assert ing.handle_snapshot(topic, payload, T0).status == "accepted"
    rows = db.execute("SELECT cycle, elapsed_minutes FROM readings WHERE machine_id='sim-01' ORDER BY cycle").fetchall()
    assert [(r["cycle"], r["elapsed_minutes"]) for r in rows] == [(0, 0.0), (1, 1.5), (2, 3.0)]


def test_cycle_continues_after_existing_dataset_readings(db_path, db):
    # seed machine m1 already has cycles 0 and 1 from the xjtu dataset
    ing, _pred, _fan = _ingestor(db_path)
    topic, payload = _snapshot(machine_id="m1")
    out = ing.handle_snapshot(topic, payload, T0)
    assert out.status == "accepted"
    row = db.execute("SELECT cycle, elapsed_minutes FROM readings WHERE id=?", (out.reading_id,)).fetchone()
    assert row["cycle"] == 2 and row["elapsed_minutes"] == 0.0  # first LIVE reading


def test_default_fan_out_uses_pipeline_with_mqtt_source(db_path, db, monkeypatch):
    from src.prediction import pipeline

    seen = {}

    def fake_handle(conn, result, *, source, features=None, timestamp=None, broadcast=None,
                    prediction_id=None, reading_id=None):
        seen.update(source=source, features=features, timestamp=timestamp, machine=result["machine_id"],
                    prediction_id=prediction_id, reading_id=reading_id)
        return {"event": "alert_created", "alert": None, "emails_sent": 0}

    monkeypatch.setattr(pipeline, "handle_prediction", fake_handle)
    ing = TelemetryIngestor(predictor_provider=lambda: FakePredictor(),
                            connection_factory=_conn_factory(db_path), topic_prefix=PREFIX)
    topic, payload = _snapshot()
    out = ing.handle_snapshot(topic, payload, T0)
    assert out.status == "accepted" and out.event == "alert_created"
    assert seen["source"] == "mqtt" and seen["machine"] == "sim-01"
    assert seen["timestamp"] == "2026-10-06T12:00:00.000Z" and "h_rms" in seen["features"]
    # The default fan-out forwards the stored reading and prediction ids.
    assert seen["reading_id"] == out.reading_id
    pred = db.execute("SELECT id, reading_id FROM predictions WHERE id = ?", (seen["prediction_id"],)).fetchone()
    assert pred is not None and pred["reading_id"] == out.reading_id


def test_real_pipeline_raises_mqtt_alert(db_path, db):
    events = []
    from src.prediction import pipeline

    def fan_out(conn, result, features, timestamp, **ids):
        return pipeline.handle_prediction(conn, result, source="mqtt", features=features,
                                          timestamp=timestamp, broadcast=events.append, **ids)

    ing, _pred, _ = _ingestor(db_path, predictor=FakePredictor("critical"), fan_out=fan_out)
    topic, payload = _snapshot()
    assert ing.handle_snapshot(topic, payload, T0).event == "alert_created"
    alert = db.execute("SELECT * FROM alerts WHERE machine_id='sim-01'").fetchone()
    assert alert["source"] == "mqtt"
    reading = db.execute("SELECT id FROM readings WHERE machine_id='sim-01'").fetchone()
    pred = db.execute("SELECT id FROM predictions WHERE machine_id='sim-01'").fetchone()
    assert (alert["reading_id"], alert["prediction_id"]) == (reading["id"], pred["id"])
    assert alert["model_version"] == "test-model-v1"
    assert events and events[0]["type"] == "alert_created"


def test_duplicate_is_not_reinserted(db_path, db):
    ing, pred, fan = _ingestor(db_path)
    topic, payload = _snapshot(seq=5)
    assert ing.handle_snapshot(topic, payload, T0).status == "accepted"
    later = T0 + timedelta(seconds=30)
    out = ing.handle_snapshot(topic, payload, later)
    assert out.status == "duplicate"
    assert len(_messages(db)) == 1
    assert db.execute("SELECT COUNT(*) FROM readings WHERE machine_id='sim-01'").fetchone()[0] == 1
    assert len(pred.calls) == 1 and len(fan) == 1
    # still counts as "seen"
    dev = db.execute("SELECT last_seen_at FROM device_status WHERE device_id='simdev-01'").fetchone()
    assert dev["last_seen_at"] == protocol.format_utc(later)


def test_same_seq_new_boot_is_not_a_duplicate(db_path, db):
    ing, _pred, _fan = _ingestor(db_path)
    for boot in ("bootA", "bootB"):
        topic, payload = _snapshot(seq=0, boot_id=boot)
        assert ing.handle_snapshot(topic, payload, T0).status == "accepted"
    assert len(_messages(db)) == 2


def test_bad_payload_is_rejected_with_unknown_device(db_path, db):
    ing, pred, _ = _ingestor(db_path)
    topic = protocol.telemetry_topic("sim-01", PREFIX)
    out = ing.handle_snapshot(topic, b"{not json", T0)
    assert out.status == "rejected" and out.device_id == "?"
    (msg,) = _messages(db)
    assert msg["status"] == "rejected" and msg["device_id"] == "?"
    assert "JSON" in msg["error"] and msg["payload_bytes"] == 9
    assert pred.calls == []
    assert db.execute("SELECT COUNT(*) FROM device_status").fetchone()[0] == 0


def test_topic_machine_mismatch_is_rejected_but_device_is_seen(db_path, db):
    ing, pred, _ = _ingestor(db_path)
    _topic, payload = _snapshot(machine_id="sim-01", seq=3)
    out = ing.handle_snapshot(protocol.telemetry_topic("sim-02", PREFIX), payload, T0)
    assert out.status == "rejected" and "does not match" in out.error
    (msg,) = _messages(db)
    assert msg["device_id"] == "simdev-01" and msg["seq"] == 3 and msg["status"] == "rejected"
    assert db.execute("SELECT COUNT(*) FROM readings WHERE machine_id IN ('sim-01','sim-02')").fetchone()[0] == 0
    assert db.execute("SELECT last_seen_at FROM device_status WHERE device_id='simdev-01'").fetchone()
    assert pred.calls == []


def test_unknown_machine_rejected_when_auto_register_off(db_path, db):
    ing, pred, _ = _ingestor(db_path, auto_register=False)
    topic, payload = _snapshot(machine_id="ghost")
    out = ing.handle_snapshot(topic, payload, T0)
    assert out.status == "rejected" and out.error == "unknown machine"
    (msg,) = _messages(db)
    assert msg["error"] == "unknown machine" and msg["machine_id"] == "ghost"
    assert db.execute("SELECT 1 FROM machines WHERE machine_id='ghost'").fetchone() is None
    assert pred.calls == []
    # a known machine is still accepted with auto-register off
    topic, payload = _snapshot(machine_id="m2", seq=1)
    assert ing.handle_snapshot(topic, payload, T0).status == "accepted"


@pytest.mark.parametrize("exc", [FileNotFoundError("model missing"), RuntimeError("boom")])
def test_predictor_error_keeps_reading_and_records_error(db_path, db, exc):
    ing, _pred, fan = _ingestor(db_path, predictor=FakePredictor(fail=exc))
    topic, payload = _snapshot()
    out = ing.handle_snapshot(topic, payload, T0)
    assert out.status == "error" and out.reading_id is not None
    assert db.execute("SELECT COUNT(*) FROM readings WHERE id=?", (out.reading_id,)).fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM predictions WHERE reading_id=?", (out.reading_id,)).fetchone()[0] == 0
    (msg,) = _messages(db)
    assert msg["status"] == "error" and msg["reading_id"] == out.reading_id
    assert type(exc).__name__ in msg["error"]
    log = db.execute("SELECT status, model_version FROM model_inference_log WHERE machine_id='sim-01'").fetchone()
    assert log["status"] == "error" and log["model_version"] == "fake-v1"
    assert fan == []
    # ingest keeps working for the next message
    ok_ing, _, _ = _ingestor(db_path)
    topic, payload = _snapshot(seq=1)
    assert ok_ing.handle_snapshot(topic, payload, T0).status == "accepted"


def test_predictor_provider_failure_is_recorded(db_path, db):
    def provider():
        raise FileNotFoundError("models/xjtu_rul.joblib")

    ing = TelemetryIngestor(predictor_provider=provider, connection_factory=_conn_factory(db_path),
                            topic_prefix=PREFIX, on_prediction=lambda *a, **k: None)
    topic, payload = _snapshot()
    out = ing.handle_snapshot(topic, payload, T0)
    assert out.status == "error"
    log = db.execute("SELECT status, model_version FROM model_inference_log WHERE machine_id='sim-01'").fetchone()
    assert log["status"] == "error" and log["model_version"] == "unavailable"


def test_fan_out_failure_does_not_reject(db_path, db):
    def broken(*_a, **_k):
        raise RuntimeError("smtp down")

    ing, _pred, _ = _ingestor(db_path, fan_out=broken)
    topic, payload = _snapshot()
    assert ing.handle_snapshot(topic, payload, T0).status == "accepted"


def test_unsynced_snapshot_uses_receive_time(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    topic, payload = _snapshot(sampled_at=None, time_synced=False, buffered=True)
    received = T0 + timedelta(seconds=7)
    out = ing.handle_snapshot(topic, payload, received)
    assert out.status == "accepted"
    (msg,) = _messages(db)
    assert msg["sampled_at"] == protocol.format_utc(received)
    assert msg["time_synced"] == 0 and msg["buffered"] == 1


def test_connection_failure_never_raises(db_path):
    def factory():
        raise sqlite3.OperationalError("disk gone")

    ing = TelemetryIngestor(predictor_provider=FakePredictor, connection_factory=factory, topic_prefix=PREFIX)
    topic, payload = _snapshot()
    assert ing.handle_snapshot(topic, payload, T0).status == "error"
    assert ing.handle_status(protocol.status_topic("d1", PREFIX), b"{}", T0).status == "error"


# --- status ---------------------------------------------------------------------

def _status(device_id="simdev-01", **fields):
    return (protocol.status_topic(device_id, PREFIX),
            protocol.build_status_payload(device_id=device_id, **fields))


def test_status_upsert_and_lwt_keeps_last_known_fields(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    topic, payload = _status(online=True, machine_id="sim-01", boot_id="boot1", firmware="sim/0.1",
                             heartbeat_interval_s=10.0, snapshot_interval_s=2.0, buffer_depth=3,
                             buffer_capacity=50, buffer_dropped_total=1, publish_attempts_total=100,
                             publish_failures_total=2, wifi_rssi_dbm=-61, uptime_s=30,
                             reported_at=T0)
    out = ing.handle_status(topic, payload, T0)
    assert out.status == "accepted"
    row = dict(db.execute("SELECT * FROM device_status WHERE device_id='simdev-01'").fetchone())
    assert row["online"] == 1 and row["firmware"] == "sim/0.1" and row["buffer_depth"] == 3
    assert row["last_seen_at"] == protocol.format_utc(T0)
    assert json.loads(row["payload_json"])["wifi_rssi_dbm"] == -61
    assert _messages(db) == []  # accepted status lives in device_status only

    lwt_topic, lwt = protocol.status_topic("simdev-01", PREFIX), protocol.build_lwt_payload("simdev-01")
    later = T0 + timedelta(seconds=40)
    assert ing.handle_status(lwt_topic, lwt, later).status == "accepted"
    row = dict(db.execute("SELECT * FROM device_status WHERE device_id='simdev-01'").fetchone())
    assert row["online"] == 0
    assert row["firmware"] == "sim/0.1" and row["buffer_capacity"] == 50  # kept
    assert row["last_seen_at"] == protocol.format_utc(later)
    assert json.loads(row["payload_json"]) == {"v": 1, "device_id": "simdev-01", "online": False}


def test_retained_status_does_not_fake_freshness(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    topic, payload = _status(online=True, reported_at=T0)
    restart = T0 + timedelta(hours=5)
    ing.handle_status(topic, payload, restart, retained=True)
    row = db.execute("SELECT last_seen_at FROM device_status").fetchone()
    assert row["last_seen_at"] == protocol.format_utc(T0)
    # and an old retained message never moves last_seen backwards
    ing.handle_status(*_status(online=True), restart)
    ing.handle_status(topic, payload, restart + timedelta(seconds=1), retained=True)
    assert db.execute("SELECT last_seen_at FROM device_status").fetchone()[0] == protocol.format_utc(restart)


def test_snapshot_after_status_bumps_last_seen_but_keeps_online(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    ing.handle_status(*_status(online=True, heartbeat_interval_s=10.0), T0)
    topic, payload = _snapshot()
    ing.handle_snapshot(topic, payload, T0 + timedelta(seconds=25))
    row = db.execute("SELECT online, last_seen_at FROM device_status").fetchone()
    assert row["online"] == 1 and row["last_seen_at"] == protocol.format_utc(T0 + timedelta(seconds=25))


def test_invalid_status_is_rejected_and_recorded(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    topic = protocol.status_topic("simdev-01", PREFIX)
    out = ing.handle_status(topic, json.dumps({"v": 1, "device_id": "other", "online": True}), T0)
    assert out.status == "rejected"
    (msg,) = _messages(db)
    assert msg["status"] == "rejected" and msg["device_id"] == "simdev-01" and msg["seq"] is None


def test_handle_message_dispatches_and_rejects_foreign_topics(db_path, db):
    ing, _pred, _ = _ingestor(db_path)
    topic, payload = _snapshot()
    assert ing.handle_message(topic, payload, T0).kind == "telemetry"
    assert ing.handle_message(*_status(online=True), T0).kind == "status"
    out = ing.handle_message("other/prefix/telemetry/x", b"{}", T0)
    assert out.status == "rejected"
    out = ing.handle_message(protocol.alert_topic("sim-01", PREFIX), b"{}", T0)
    assert out.status == "rejected"
    statuses = [m["status"] for m in _messages(db)]
    assert statuses == ["accepted", "rejected", "rejected"]


def test_device_online_rule():
    now = T0
    assert device_online(1, protocol.format_utc(now - timedelta(seconds=29)), 10.0, now=now)
    assert not device_online(1, protocol.format_utc(now - timedelta(seconds=31)), 10.0, now=now)
    assert not device_online(0, protocol.format_utc(now), 10.0, now=now)
    # unknown heartbeat defaults to 10 s
    assert device_online(1, protocol.format_utc(now - timedelta(seconds=20)), None, now=now)
    assert not device_online(1, None, None, now=now)


def _downgrade_to_v1(db):
    """Simulate a developer's pre-M6 maintainiq.db: v1 stamp, no M6 tables."""
    db.execute("DROP TABLE telemetry_messages")
    db.execute("DROP TABLE device_status")
    db.execute("DELETE FROM schema_version WHERE version >= 2")
    db.commit()


def test_first_message_migrates_a_pre_m6_db(db_path, db):
    from src.storage.migrations import MIGRATIONS, current_version

    _downgrade_to_v1(db)
    assert current_version(db) == 1
    ing, _, _ = _ingestor(db_path)
    out = ing.handle_message(*_snapshot(seq=0), received_at=T0)
    assert out.status == "accepted"
    assert current_version(db) == MIGRATIONS[-1][0]
    assert len(_messages(db)) == 1
    # Later messages reuse the ready schema.
    assert ing.handle_message(*_snapshot(seq=1), received_at=T0).status == "accepted"


def test_pre_m6_db_reports_no_telemetry_instead_of_failing(db_path, db):
    from src.kpi.calculations import system_kpis

    _downgrade_to_v1(db)
    kpis = system_kpis(db)
    assert {v["status"] for v in kpis.values()} == {"not_applicable"}


# --- M6 review regressions ----------------------------------------------------------


def test_failure_after_reading_commit_keeps_the_snapshot_key(db_path, db, monkeypatch):
    """persist_prediction blowing up (DB lock, changed result shape) must still
    leave a row keyed by the device's own (device_id, boot_id, seq) and the
    stored reading — not an anonymous '?' row — so a redelivery is a duplicate
    instead of a second reading."""
    from src.telemetry import ingest as ingest_mod

    def boom(*_a, **_k):
        raise KeyError("health_state")

    monkeypatch.setattr(ingest_mod.rul_store, "persist_prediction", boom)
    ing, _pred, _ = _ingestor(db_path)
    topic, payload = _snapshot(seq=7)
    out = ing.handle_snapshot(topic, payload, T0)

    assert out.status == "error" and out.device_id == "simdev-01" and out.reading_id is not None
    (msg,) = _messages(db)
    assert (msg["device_id"], msg["boot_id"], msg["seq"]) == ("simdev-01", "boot1", 7)
    assert msg["machine_id"] == "sim-01" and msg["reading_id"] == out.reading_id
    assert msg["status"] == "error" and "internal error" in msg["error"]

    monkeypatch.undo()
    assert ing.handle_snapshot(topic, payload, T0 + timedelta(seconds=1)).status == "duplicate"
    assert db.execute("SELECT COUNT(*) FROM readings WHERE dataset='live_mqtt'").fetchone()[0] == 1


def test_retained_malformed_status_is_not_re_recorded(db_path, db):
    """The broker replays retained status on every reconnect; a malformed one
    must not add a rejected row or bump last_seen_at each time."""
    ing, _pred, _ = _ingestor(db_path)
    ing.handle_status(*_status(online=True, heartbeat_interval_s=10.0), T0)
    topic = protocol.status_topic("simdev-01", PREFIX)
    bad = json.dumps({"v": 1, "device_id": "simdev-01", "online": "yes"})
    later = T0 + timedelta(hours=3)
    for k in range(3):
        out = ing.handle_status(topic, bad, later + timedelta(seconds=k), retained=True)
        assert out.status == "rejected"
    assert _messages(db) == []
    row = db.execute("SELECT last_seen_at FROM device_status").fetchone()
    assert row["last_seen_at"] == protocol.format_utc(T0)
    # A live (non-retained) malformed status is still recorded.
    assert ing.handle_status(topic, bad, later).status == "rejected"
    assert [m["status"] for m in _messages(db)] == ["rejected"]


def test_ensure_telemetry_schema_on_v0_db_creates_device_incidents():
    from src.storage.migrations import current_version
    from src.telemetry.ingest import ensure_telemetry_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_telemetry_schema(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"telemetry_messages", "device_status", "device_incidents"} <= names
    # Still never a migration-1 side effect on an unversioned file.
    assert current_version(conn) == 0


def test_hook_receives_the_reading_and_prediction_ids(db_path, db):
    seen = []
    ing, _pred, _ = _ingestor(
        db_path, fan_out=lambda conn, result, features, timestamp, **ids: seen.append(ids))
    topic, payload = _snapshot()
    out = ing.handle_snapshot(topic, payload, T0)
    pred = db.execute("SELECT id FROM predictions WHERE reading_id = ?", (out.reading_id,)).fetchone()
    assert seen == [{"prediction_id": pred["id"], "reading_id": out.reading_id}]
