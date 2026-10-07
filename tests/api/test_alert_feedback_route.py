"""Alert close / feedback HTTP API
(design/2026-10-07-prediction-feedback-design.md, "API" and "Realtime events").

Demo users: admin (1), supervisor (2), operator (3). Alert 1 is resolved and
alert 2 open on m1.
"""
import pytest

from src.realtime.manager import manager


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def _spy(event):
        sent.append(event)

    monkeypatch.setattr(manager, "broadcast", _spy)
    return sent


@pytest.fixture
def no_email(monkeypatch):
    calls = []
    monkeypatch.setattr("src.notifications.dispatch.send_email",
                        lambda *a, **k: calls.append(a))
    return calls


ROUTES = [
    ("post", "/api/alerts/2/close", {"outcome": "false_alarm"}),
    ("put", "/api/alerts/2/feedback", {"outcome": "false_alarm"}),
    ("get", "/api/alerts/2/feedback", None),
    ("get", "/api/model/feedback-accuracy", None),
    ("get", "/api/model/feedback/export", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_anonymous_gets_401(anon_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(anon_client, method)(path, **kwargs).status_code == 401


def test_operator_closes_an_open_alert(auth_client, broadcasts, no_email):
    op = auth_client("operator")
    resp = op.post("/api/alerts/2/close", json={"outcome": "maintenance_prevented",
                                                "actual_cause": "bearing_wear", "notes": "swapped"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["closed"] is True
    assert body["alert"]["status"] == "resolved" and body["alert"]["closed_by"] == 3
    assert body["feedback"]["outcome"] == "maintenance_prevented"
    assert body["feedback"]["recorded_by_name"] == "Otis Operator"

    assert [a["id"] for a in op.get("/api/alerts?status=open").json()] == []
    listed = {a["id"]: a for a in op.get("/api/alerts").json()}
    assert listed[2]["feedback"]["outcome"] == "maintenance_prevented"
    assert listed[1]["feedback"] is None

    assert [e["type"] for e in broadcasts] == ["alert_closed"]
    event = broadcasts[0]
    assert event["machine_id"] == "m1" and event["alert"]["id"] == 2
    assert event["feedback"]["outcome"] == "maintenance_prevented" and "at" in event
    assert no_email == []


def test_close_on_resolved_alert_records_only(client, broadcasts):
    resp = client.post("/api/alerts/1/close", json={"outcome": "false_alarm"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["closed"] is False
    assert [e["type"] for e in broadcasts] == ["alert_feedback_recorded"]


@pytest.mark.parametrize("body", [
    {},
    {"outcome": "closed"},
    {"outcome": "false_alarm", "actual_cause": "gremlins"},
    {"outcome": "false_alarm", "notes": "x" * 2001},
])
def test_invalid_bodies_are_422(client, broadcasts, body):
    assert client.post("/api/alerts/2/close", json=body).status_code == 422


def test_errors(client, auth_client, broadcasts):
    resp = client.post("/api/alerts/99/close", json={"outcome": "unknown"})
    assert resp.status_code == 404 and resp.json()["detail"] == "unknown alert: 99"
    assert client.get("/api/alerts/99/feedback").status_code == 404
    resp = client.post("/api/alerts/2/close", json={"outcome": "false_alarm",
                                                    "actual_failure_at": "2026-10-06T00:00:00Z"})
    assert resp.status_code == 400
    assert "only applies to outcome confirmed_failure" in resp.json()["detail"]
    assert client.get("/api/alerts?status=open").json()[0]["id"] == 2  # still open

    sup = auth_client("supervisor")
    assert sup.put("/api/alerts/1/feedback", json={"outcome": "unknown"}).status_code == 200
    resp = auth_client("operator").put("/api/alerts/1/feedback", json={"outcome": "false_alarm"})
    assert resp.status_code == 403


def test_put_then_get_round_trips(client, broadcasts):
    assert client.get("/api/alerts/1/feedback").json() is None
    resp = client.put("/api/alerts/1/feedback", json={"outcome": "false_alarm",
                                                      "actual_cause": "other", "notes": "n"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["outcome"] == "false_alarm"
    got = client.get("/api/alerts/1/feedback").json()
    assert got["actual_cause"] == "other" and got["notes"] == "n" and got["recorded_by"] == 1
    assert [e["type"] for e in broadcasts] == ["alert_feedback_recorded"]
    # An open alert keeps its status.
    client.put("/api/alerts/2/feedback", json={"outcome": "unknown"})
    assert client.get("/api/alerts?status=open").json()[0]["id"] == 2


def test_machine_and_work_order_detail_carry_feedback(client, broadcasts):
    wo = client.post("/api/alerts/2/work-order", json={}).json()
    client.post("/api/alerts/2/close", json={"outcome": "confirmed_failure",
                                             "actual_failure_at": "2026-10-06T00:00:00Z",
                                             "work_order_id": wo["id"]})
    alerts = {a["id"]: a for a in client.get("/api/machines/m1").json()["alerts"]}
    a2 = alerts[2]
    assert a2["feedback"]["work_order_id"] == wo["id"]
    assert a2["closed_by"] == 1
    for key in ("prediction_id", "reading_id", "model_version"):
        assert key in a2
    detail = client.get(f"/api/work-orders/{wo['id']}").json()
    assert detail["alert"]["feedback"]["outcome"] == "confirmed_failure"


def test_broadcast_failure_still_returns_200(client, monkeypatch):
    async def boom(event):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(manager, "broadcast", boom)
    assert client.post("/api/alerts/2/close", json={"outcome": "unknown"}).status_code == 200
    assert client.put("/api/alerts/1/feedback", json={"outcome": "unknown"}).status_code == 200
