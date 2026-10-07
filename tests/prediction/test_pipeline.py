"""Tests for src/prediction/pipeline.py — the shared post-prediction fan-out.

This is the seam that makes a *model* prediction do what a *demo* injection
already did: drive the alert state machine, page admins/supervisors by email,
and broadcast to connected dashboards. Both the replay ingestion path and any
future live telemetry feed go through here, so the two can't drift apart.

Everything here uses machine m2 — the conftest fixture seeds m1 with an
already-open high-severity alert, which is the wrong starting state for
testing how a *new* episode opens.
"""
import pytest

from src.prediction import pipeline


def _result(machine_id="m2", health_state="critical", rul=12.0):
    """Shaped like RealTimeRULPredictor._predict_from_base's return value."""
    return {
        "machine_id": machine_id,
        "health_state": health_state,
        "predicted_rul_minutes": rul,
        "prognostic_horizon_minutes": 120.0,
        "failure_within_horizon_probability": 0.97,
        "model_version": "test-model-v1",
        "warnings": [],
    }


# Feature vectors as stored in readings.features_json (h_* naming), which is
# NOT the vibration_h_* naming the root-cause classifier expects.
BEARING_WEAR_FEATURES = {"h_rms": 1.4, "h_kurtosis": 7.5}
IMBALANCE_FEATURES = {"h_rms": 0.9, "h_kurtosis": 3.0}


@pytest.fixture
def emails(monkeypatch):
    """Replaces the email fan-out so tests assert on paging decisions without
    needing an SMTP server; delivery itself is covered in test_notifications.py."""
    sent = []
    monkeypatch.setattr(
        pipeline, "notify_alert",
        lambda conn, alert: (sent.append(alert) or len(sent)),
    )
    return sent


@pytest.fixture
def events():
    return []


def test_abnormal_prediction_opens_an_alert(conn, emails, events):
    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    assert outcome["event"] == "alert_created"
    row = conn.execute("SELECT * FROM alerts WHERE machine_id = 'm2'").fetchone()
    assert row["status"] == "open"
    assert row["health_state"] == "critical"
    assert row["severity"] == "high"
    assert row["source"] == "replay"


def test_abnormal_prediction_pages_by_email(conn, emails, events):
    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    assert outcome["emails_sent"] == 1
    assert len(emails) == 1
    assert emails[0]["machine_id"] == "m2"


def test_abnormal_prediction_broadcasts_to_dashboards(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    assert len(events) == 1
    assert events[0]["type"] == "alert_created"
    assert events[0]["machine_id"] == "m2"
    assert events[0]["alert"]["severity"] == "high"


def test_broadcast_uses_the_documented_wire_timestamp_key(conn, emails, events):
    """The dashboard (frontend/src/api/types.ts LiveEvent) and the demo route
    both speak `at`. A second spelling here would leave live events undated in
    the UI while every test still passed."""
    pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
        timestamp="2026-09-19T07:00:00+00:00",
    )

    assert events[0]["at"] == "2026-09-19T07:00:00+00:00"
    assert "timestamp" not in events[0]


def test_broadcast_omits_the_raw_feature_vector(conn, emails, events):
    """`input_features` is internal plumbing: the predictor carries it on its
    result purely so this module can classify a root cause. It is not in
    RULPredictionResponse, so shipping it to every connected dashboard would
    put a payload on the wire that no consumer declares and no schema pins."""
    result = _result()
    result["input_features"] = dict(BEARING_WEAR_FEATURES)

    pipeline.handle_prediction(
        conn, result, source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    assert "input_features" not in events[0]["prediction"]
    # Stripping it must not cost the caller its own dict.
    assert "input_features" in result


def test_broadcast_carries_the_prediction_so_dashboards_can_update_rul(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(rul=12.0), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    assert events[0]["prediction"]["predicted_rul_minutes"] == 12.0
    assert events[0]["prediction"]["health_state"] == "critical"


def test_probable_cause_comes_from_the_real_feature_vector(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    cause = conn.execute(
        "SELECT probable_cause FROM alerts WHERE machine_id = 'm2'"
    ).fetchone()[0]
    assert cause == "bearing_wear"


def test_probable_cause_distinguishes_imbalance_from_bearing_wear(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=IMBALANCE_FEATURES, broadcast=events.append,
    )

    cause = conn.execute(
        "SELECT probable_cause FROM alerts WHERE machine_id = 'm2'"
    ).fetchone()[0]
    assert cause == "imbalance"


def test_missing_features_are_reported_as_a_data_quality_issue(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(), source="replay", features={}, broadcast=events.append,
    )

    cause = conn.execute(
        "SELECT probable_cause FROM alerts WHERE machine_id = 'm2'"
    ).fetchone()[0]
    assert cause == "sensor_or_data_quality_issue"


def test_healthy_prediction_does_nothing_when_no_alert_is_open(conn, emails, events):
    outcome = pipeline.handle_prediction(
        conn, _result(health_state="healthy", rul=900.0), source="replay",
        features=IMBALANCE_FEATURES, broadcast=events.append,
    )

    assert outcome["event"] is None
    assert outcome["emails_sent"] == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE machine_id='m2'").fetchone()[0] == 0
    assert events == []


def test_recovery_resolves_the_open_alert_without_emailing(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    emails.clear()
    events.clear()

    outcome = pipeline.handle_prediction(
        conn, _result(health_state="healthy", rul=900.0), source="replay",
        features=IMBALANCE_FEATURES, broadcast=events.append,
    )

    assert outcome["event"] == "alert_resolved"
    # A resolution email would page people about an issue that just closed.
    assert emails == []
    assert outcome["emails_sent"] == 0
    assert events[0]["type"] == "alert_resolved"
    assert conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE machine_id='m2' AND status='open'"
    ).fetchone()[0] == 0


def test_escalation_pages_again_but_a_steady_state_does_not(conn, emails, events):
    pipeline.handle_prediction(
        conn, _result(health_state="degrading", rul=600.0), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    assert len(emails) == 1

    # Same severity again: escalate-only semantics mean no new page, and
    # nothing for the dashboard to redraw.
    pipeline.handle_prediction(
        conn, _result(health_state="degrading", rul=590.0), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    assert len(emails) == 1
    assert len(events) == 1

    # Worse: escalate, and page again.
    outcome = pipeline.handle_prediction(
        conn, _result(health_state="critical", rul=10.0), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    assert outcome["event"] == "alert_escalated"
    assert len(emails) == 2


def test_email_failure_does_not_break_the_prediction_path(conn, monkeypatch, events):
    def _boom(conn, alert):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(pipeline, "notify_alert", _boom)

    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )

    # The alert still exists and the dashboard still hears about it.
    assert outcome["event"] == "alert_created"
    assert outcome["emails_sent"] == 0
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE machine_id='m2'").fetchone()[0] == 1
    assert len(events) == 1


def test_broadcast_failure_does_not_break_the_prediction_path(conn, emails):
    def _boom(event):
        raise RuntimeError("no running event loop")

    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=_boom,
    )

    assert outcome["event"] == "alert_created"
    assert outcome["emails_sent"] == 1
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE machine_id='m2'").fetchone()[0] == 1


# --- fan_out (work-orders design, decisions 8-9) ----------------------------------------

def _last_paged(conn, alert_id):
    return conn.execute("SELECT last_paged_at FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]


def test_fan_out_stamps_last_paged_at_on_paging_events(conn, emails, events):
    created = pipeline.handle_prediction(
        conn, _result(health_state="degrading"), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    alert_id = created["alert"]["id"]
    first = _last_paged(conn, alert_id)
    assert first is not None
    assert created["alert"]["last_paged_at"] == first
    assert created["alert"]["page_level"] == 0

    conn.execute("UPDATE alerts SET last_paged_at = NULL WHERE id = ?", (alert_id,))
    conn.commit()
    escalated = pipeline.handle_prediction(
        conn, _result(health_state="critical"), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    assert escalated["event"] == "alert_escalated"
    assert _last_paged(conn, alert_id) is not None

    conn.execute("UPDATE alerts SET last_paged_at = NULL WHERE id = ?", (alert_id,))
    conn.commit()
    resolved = pipeline.handle_prediction(
        conn, _result(health_state="healthy"), source="replay", broadcast=events.append,
    )
    assert resolved["event"] == "alert_resolved"
    assert _last_paged(conn, alert_id) is None


def test_failing_stamp_is_logged_and_alert_stays(conn, emails, events, monkeypatch, caplog):
    def boom(conn, alert_id, at):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline, "_stamp_paged", boom)
    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay",
        features=BEARING_WEAR_FEATURES, broadcast=events.append,
    )
    assert outcome["event"] == "alert_created"
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE machine_id='m2'").fetchone()[0] == 1
    assert len(events) == 1
    assert "database is locked" in caplog.text


def test_fan_out_directly(conn, emails, events):
    from src.alerts.live import apply_reading

    applied = apply_reading(conn, "m2", "faulty", "imbalance", "demo", "2026-10-07T12:00:00+00:00")
    outcome = pipeline.fan_out(conn, applied, at="2026-10-07T12:00:01+00:00", broadcast=events.append)
    assert outcome["event"] == "alert_created" and outcome["emails_sent"] == 1
    assert events[0]["at"] == "2026-10-07T12:00:01+00:00"
    assert "prediction" not in events[0]
    assert pipeline.fan_out(conn, None, at="x", broadcast=events.append) == {
        "event": None, "alert": None, "emails_sent": 0}


def test_handle_prediction_links_the_alert_to_its_prediction(conn, emails, events):
    """The ids rul_store.persist_prediction returned travel onto the alert, and
    model_version comes from the result itself (feedback design, decision 3)."""
    outcome = pipeline.handle_prediction(
        conn, _result(), source="replay", features=BEARING_WEAR_FEATURES,
        broadcast=events.append, prediction_id=11, reading_id=5,
    )
    assert outcome["event"] == "alert_created"
    row = conn.execute("SELECT * FROM alerts WHERE machine_id = 'm2'").fetchone()
    assert (row["prediction_id"], row["reading_id"], row["model_version"]) == (11, 5, "test-model-v1")
    assert events[0]["alert"]["prediction_id"] == 11
    # The storage ids are not part of the broadcast prediction contract.
    assert "prediction_id" not in events[0]["prediction"]


# --- explanation snapshots (alert-explanation design, decision 2) -------------------------

def _m2_reading(conn, cycle, kurtosis=3.0):
    """A stored m2 reading after the two the conftest seeds."""
    cur = conn.execute(
        """INSERT INTO readings (machine_id, timestamp, cycle, elapsed_minutes, speed_rpm, load_kn,
               sample_rate_hz, vibration_h_rms, vibration_h_kurtosis, vibration_v_rms,
               vibration_v_kurtosis, cross_axis_rms_ratio, cross_axis_correlation, features_json)
           VALUES ('m2', ?, ?, ?, 2100.0, 12.0, 25600.0, 1.0, ?, 1.0, 3.0, 1.0, 0.5, '{}')""",
        (f"2003-10-22T14:{cycle:02d}:00+00:00", cycle, float(cycle), kurtosis),
    )
    conn.commit()
    return cur.lastrowid


def _snapshots(conn):
    return [dict(r) for r in conn.execute(
        "SELECT alert_id, kind, reading_id, prediction_id, explanation_json "
        "FROM alert_explanations ORDER BY id")]


@pytest.fixture
def no_predictor(monkeypatch):
    from src.root_cause import explain

    monkeypatch.setattr(explain, "loaded_predictor", lambda: None)


def test_create_and_escalate_write_snapshots(conn, emails, events, no_predictor):
    import json

    opener = _m2_reading(conn, 2)
    created = pipeline.handle_prediction(
        conn, _result(health_state="degrading"), source="replay", features=BEARING_WEAR_FEATURES,
        broadcast=events.append, prediction_id=None, reading_id=opener,
        timestamp="2003-10-22T14:02:00+00:00",
    )
    alert_id = created["alert"]["id"]
    snaps = _snapshots(conn)
    assert [(s["alert_id"], s["kind"], s["reading_id"]) for s in snaps] == [(alert_id, "created", opener)]
    body = json.loads(snaps[0]["explanation_json"])
    assert body["triggering_readings"]["trigger_reading_id"] == opener
    assert body["probable_cause"]["inputs_source"] == "snapshot_features"

    # Same severity: no snapshot.
    pipeline.handle_prediction(conn, _result(health_state="degrading"), source="replay",
                               features=BEARING_WEAR_FEATURES, broadcast=events.append,
                               reading_id=_m2_reading(conn, 3))
    assert len(_snapshots(conn)) == 1

    escalator = _m2_reading(conn, 4, kurtosis=9.0)
    escalated = pipeline.handle_prediction(
        conn, _result(health_state="critical"), source="replay", features=BEARING_WEAR_FEATURES,
        broadcast=events.append, reading_id=escalator, prediction_id=None,
    )
    assert escalated["event"] == "alert_escalated"
    snaps = _snapshots(conn)
    assert [(s["kind"], s["reading_id"]) for s in snaps] == [("created", opener), ("escalated", escalator)]
    # The alert keeps its opening link (feedback design, decision 3).
    assert conn.execute("SELECT reading_id FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0] == opener

    resolved = pipeline.handle_prediction(conn, _result(health_state="healthy"), source="replay",
                                          broadcast=events.append)
    assert resolved["event"] == "alert_resolved"
    assert len(_snapshots(conn)) == 2


def test_snapshot_is_committed_before_the_broadcast(conn, emails, no_predictor):
    seen = []

    def broadcast(event):
        seen.append(len(_snapshots(conn)))

    pipeline.handle_prediction(conn, _result(), source="replay", features=BEARING_WEAR_FEATURES,
                               broadcast=broadcast)
    assert seen == [1]


def test_snapshot_failure_never_breaks_the_fan_out(conn, emails, events, monkeypatch, caplog):
    from src.root_cause import explain

    def boom(*args, **kwargs):
        raise RuntimeError("snapshot exploded")

    monkeypatch.setattr(explain, "record_snapshot", boom)
    outcome = pipeline.handle_prediction(conn, _result(), source="replay",
                                         features=BEARING_WEAR_FEATURES, broadcast=events.append)
    assert outcome["event"] == "alert_created"
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE machine_id='m2'").fetchone()[0] == 1
    assert len(emails) == 1
    assert _last_paged(conn, outcome["alert"]["id"]) is not None
    assert len(events) == 1
    assert "snapshot exploded" in caplog.text


def test_fan_out_without_the_new_keywords_still_snapshots(conn, emails, events, no_predictor):
    from src.alerts.live import apply_reading

    applied = apply_reading(conn, "m2", "faulty", "imbalance", "demo", "2026-10-07T12:00:00+00:00")
    outcome = pipeline.fan_out(conn, applied, at="2026-10-07T12:00:01+00:00", broadcast=events.append)
    assert outcome["event"] == "alert_created"
    snaps = _snapshots(conn)
    assert [(s["kind"], s["reading_id"], s["prediction_id"]) for s in snaps] == [("created", None, None)]


# --- Push channel (design/2026-10-07-mobile-operator-pwa-design.md, decision 2) ------

@pytest.fixture
def operator_pushed(conn, add_sub, push_enabled, monkeypatch):
    """Real dispatch.notify_alert with SMTP stubbed and an operator push
    subscription, so the push fan-out is exercised end to end."""
    from src.notifications import dispatch

    monkeypatch.setattr(dispatch, "send_email", lambda to, subject, body: None)
    add_sub(conn, 3, "https://fcm.googleapis.com/fcm/send/operator")
    return push_enabled


def test_new_alert_pushes_the_operator(conn, events, operator_pushed):
    outcome = pipeline.handle_prediction(conn, _result(), source="replay",
                                         features=BEARING_WEAR_FEATURES, broadcast=events.append)
    assert outcome["event"] == "alert_created"
    assert outcome["emails_sent"] == 2
    assert operator_pushed.endpoints == ["https://fcm.googleapis.com/fcm/send/operator"]


def test_resolution_is_never_pushed(conn, events, operator_pushed):
    pipeline.handle_prediction(conn, _result(), source="replay",
                               features=BEARING_WEAR_FEATURES, broadcast=events.append)
    operator_pushed.calls.clear()
    outcome = pipeline.handle_prediction(conn, _result(health_state="healthy", rul=500.0),
                                         source="replay", features=BEARING_WEAR_FEATURES,
                                         broadcast=events.append)
    assert outcome["event"] == "alert_resolved"
    assert operator_pushed.calls == []
