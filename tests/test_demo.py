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


def test_simulate_fault_resolves_open_alert_without_email(client, sent_emails):
    # m1 already has an open ("critical"/"high") alert from the fixture data.
    resp = client.post("/api/demo/simulate-fault", json={"machine_id": "m1", "severity": "healthy"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["health_state"] == "healthy"
    assert body["alert"]["status"] == "resolved"
    assert body["emails_sent"] == 0
    assert sent_emails == []


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
