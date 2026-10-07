"""Tests for POST /api/demo/simulate-fault (src/api/routes/demo.py).

send_email is monkeypatched (see tests/test_notifications.py for the same
convention) so these tests never need a real Mailpit instance.
"""
import pytest

from src.notifications import dispatch


@pytest.fixture
def sent_emails(monkeypatch):
    sent = []

    def _fake_send(to, subject, body_text):
        sent.append({"to": to, "subject": subject})

    monkeypatch.setattr(dispatch, "send_email", _fake_send)
    return sent


def test_simulate_fault_creates_alert_and_emails(client, sent_emails):
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "degrading"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["health_state"] == "degrading"
    assert body["alert"] is not None
    assert body["alert"]["status"] == "open"
    assert body["alert"]["severity"] == "low"
    assert body["emails_sent"] == 2
    assert len(sent_emails) == 2


def test_simulate_fault_escalates_open_alert_without_duplicating_it(client, sent_emails):
    first = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "degrading"})
    alert_id = first.json()["alert"]["id"]

    second = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "critical"})
    assert second.status_code == 200, second.text
    body = second.json()

    assert body["alert"]["id"] == alert_id
    assert body["alert"]["severity"] == "high"
    assert body["alert"]["status"] == "open"


def test_simulate_fault_same_severity_is_a_noop(client, sent_emails):
    client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "degrading"})
    sent_emails.clear()

    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "degrading"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["alert"] is None
    assert body["emails_sent"] == 0
    assert sent_emails == []


def test_simulate_fault_resolves_open_demo_alert_without_email(client, sent_emails):
    opened = client.post("/api/demo/simulate-fault", json={"machine_id": "m1", "severity": "critical"})
    assert opened.status_code == 200, opened.text
    demo_alert = opened.json()["alert"]
    assert demo_alert["source"] == "demo" and demo_alert["status"] == "open"
    sent_emails.clear()

    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m1", "severity": "healthy"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["health_state"] == "healthy"
    assert body["alert"]["id"] == demo_alert["id"]
    assert body["alert"]["status"] == "resolved"
    assert body["emails_sent"] == 0
    assert sent_emails == []


def test_simulate_fault_never_resolves_a_real_alert(client, sent_emails):
    # m1 already has an open real ('ml') alert from the fixture data. Demo and
    # real readings keep separate episodes, so a synthetic healthy reading
    # leaves it open.
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m1", "severity": "healthy"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["alert"] is None
    alerts = client.get("/api/alerts", params={"machine_id": "m1", "status": "open"}).json()
    assert [a["id"] for a in alerts] == [2]


def test_simulate_fault_unknown_machine_404(client, sent_emails):
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "does-not-exist", "severity": "critical"})
    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["supervisor", "operator"])
def test_simulate_fault_requires_admin(auth_client, role):
    non_admin = auth_client(role)
    resp = non_admin.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "critical"})
    assert resp.status_code == 403


def test_simulate_fault_rejects_anonymous(anon_client):
    resp = anon_client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "critical"})
    assert resp.status_code == 401


# --- Shared fan-out (work-orders design, decision 9) -------------------------------------

def test_simulate_fault_stamps_last_paged_at(client, sent_emails):
    body = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "faulty"}).json()
    assert body["alert"]["last_paged_at"] is not None
    assert body["alert"]["page_level"] == 0
    listed = {a["id"]: a for a in client.get("/api/alerts").json()}
    assert listed[body["alert"]["id"]]["last_paged_at"] is not None


def test_simulate_fault_email_failure_is_logged_not_raised(client, monkeypatch):
    from src.prediction import pipeline

    def boom(conn, alert):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(pipeline, "notify_alert", boom)
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "critical"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["emails_sent"] == 0
    assert body["alert"]["status"] == "open"
    assert len(client.get("/api/alerts?machine_id=m2&status=open").json()) == 1


def test_simulate_fault_snapshots_a_synthetic_explanation(client, sent_emails, monkeypatch, db_path):
    import sqlite3

    from src.root_cause import explain

    monkeypatch.setattr(explain, "loaded_predictor", lambda: None)
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m2", "severity": "critical"})
    assert resp.status_code == 200, resp.text
    alert_id = resp.json()["alert"]["id"]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT kind, reading_id FROM alert_explanations WHERE alert_id = ?",
                            (alert_id,)).fetchall()
        demo_reading = conn.execute("SELECT MAX(id) FROM readings WHERE machine_id = 'm2'").fetchone()[0]
    finally:
        conn.close()
    assert rows == [("created", demo_reading)]

    body = client.get(f"/api/alerts/{alert_id}/explanation").json()
    assert body["source"] == "snapshot" and body["synthetic"] is True
    assert body["triggering_readings"]["trigger_reading_id"] == demo_reading
    assert body["key_factors"]["status"] in {"insufficient_history", "ok"}
    if body["key_factors"]["status"] == "ok":
        assert body["key_factors"]["method"] == "promoted_columns"
    assert body["prediction"]["failure_within_horizon_probability"] is None
