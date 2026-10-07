"""Work-order HTTP API (src/api/routes/work_orders.py) and the alert-side
routes it adds (design/2026-10-07-work-orders-escalation-design.md, "API").

Demo users: admin (1), supervisor (2), operator (3). Alert 2 is open/high on m1.
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


def _make_order(c, **body):
    payload = {"machine_id": "m2", "title": "Grease bearing"}
    payload.update(body)
    resp = c.post("/api/work-orders", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


ROUTES = [
    ("get", "/api/work-orders", None),
    ("get", "/api/work-orders/assignees", None),
    ("get", "/api/work-orders/1", None),
    ("post", "/api/work-orders", {"machine_id": "m2", "title": "t"}),
    ("patch", "/api/work-orders/1", {"title": "t"}),
    ("post", "/api/work-orders/1/assign", {"assigned_to": 1}),
    ("post", "/api/work-orders/1/start", None),
    ("post", "/api/work-orders/1/complete", {}),
    ("post", "/api/work-orders/1/cancel", {}),
    ("post", "/api/alerts/2/work-order", {}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_anonymous_gets_401(anon_client, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    assert getattr(anon_client, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize("role,expected", [("admin", 201), ("supervisor", 201), ("operator", 403)])
def test_free_standing_create_rbac(auth_client, broadcasts, role, expected):
    resp = auth_client(role).post("/api/work-orders", json={"machine_id": "m2", "title": "t"})
    assert resp.status_code == expected, resp.text


@pytest.mark.parametrize("role", ["admin", "supervisor", "operator"])
def test_any_role_creates_from_alert_and_lists(auth_client, broadcasts, role):
    c = auth_client(role)
    resp = c.post("/api/alerts/2/work-order")
    assert resp.status_code == 201, resp.text
    wo = resp.json()
    assert wo["status"] == "open" and wo["alert_id"] == 2 and wo["priority"] == "high"
    listed = c.get("/api/work-orders").json()
    assert [w["id"] for w in listed] == [wo["id"]]
    assert c.get(f"/api/work-orders/{wo['id']}").status_code == 200


def test_operator_cannot_assign_on_create_from_alert(auth_client, broadcasts):
    resp = auth_client("operator").post("/api/alerts/2/work-order", json={"assigned_to": 3})
    assert resp.status_code == 403


@pytest.mark.parametrize("role,expected", [("admin", 200), ("supervisor", 200), ("operator", 403)])
def test_supervising_actions_rbac(auth_client, broadcasts, role, expected):
    wo = _make_order(auth_client("admin"))
    c = auth_client(role)
    assert c.patch(f"/api/work-orders/{wo['id']}", json={"priority": "high"}).status_code == expected
    assert c.post(f"/api/work-orders/{wo['id']}/assign",
                  json={"assigned_to": 3}).status_code == expected
    assert c.post(f"/api/work-orders/{wo['id']}/cancel",
                  json={"reason": "no"}).status_code == expected


def test_operator_starts_and_completes_only_own_orders(auth_client, broadcasts):
    admin, op = auth_client("admin"), auth_client("operator")
    mine = _make_order(admin, assigned_to=3)
    theirs = _make_order(admin, assigned_to=2)

    assert op.post(f"/api/work-orders/{theirs['id']}/start").status_code == 403
    resp = op.post(f"/api/work-orders/{mine['id']}/start")
    assert resp.status_code == 200 and resp.json()["status"] == "in_progress"
    resp = op.post(f"/api/work-orders/{mine['id']}/complete",
                   json={"notes": "done", "maintenance_type": "corrective"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "done" and body["maintenance_record_id"] is not None


def test_not_found_conflict_and_validation(client, broadcasts):
    assert client.get("/api/work-orders/999").status_code == 404
    assert client.post("/api/work-orders/999/start").status_code == 404
    assert client.post("/api/alerts/999/work-order").status_code == 404

    first = client.post("/api/alerts/2/work-order").json()
    dup = client.post("/api/alerts/2/work-order")
    assert dup.status_code == 409
    assert f"#{first['id']}" in dup.json()["detail"]

    bad = client.post(f"/api/work-orders/{first['id']}/start")
    assert bad.status_code == 409
    assert bad.json()["detail"] == "invalid transition: open -> start"

    assert client.get("/api/work-orders?status=bogus").status_code == 400
    assert client.post(f"/api/work-orders/{first['id']}/assign",
                       json={"assigned_to": 999}).status_code == 400
    assert client.get("/api/work-orders?limit=0").status_code == 422
    assert client.post("/api/work-orders", json={"machine_id": "ghost", "title": "t"}).status_code == 400
    assert client.post("/api/work-orders", json={"machine_id": "m2", "title": ""}).status_code == 422


def test_create_from_alert_acknowledges_and_links(client, broadcasts):
    resp = client.post("/api/alerts/2/work-order", json={})
    assert resp.status_code == 201
    wo = resp.json()
    alerts = {a["id"]: a for a in client.get("/api/alerts").json()}
    assert alerts[2]["acknowledged_at"] is not None
    assert alerts[2]["active_work_order_id"] == wo["id"]
    assert alerts[2]["page_level"] == 0 and alerts[2]["last_paged_at"] is None
    assert alerts[1]["active_work_order_id"] is None

    assert [e["type"] for e in broadcasts] == ["alert_acknowledged", "work_order_created"]
    assert broadcasts[1]["work_order"]["id"] == wo["id"]
    assert broadcasts[1]["machine_id"] == "m1"


def test_machine_detail_carries_paging_and_work_order_fields(client, broadcasts):
    wo = client.post("/api/alerts/2/work-order").json()
    body = client.get("/api/machines/m1").json()
    by_id = {a["id"]: a for a in body["alerts"]}
    assert by_id[2]["active_work_order_id"] == wo["id"]
    assert "page_level" in by_id[2] and "last_paged_at" in by_id[2]
    assert body["maintenance"]["open_work_order_count"] == 1
    assert client.get("/api/kpis").json()["open_work_order_count"] == 1


def test_mutations_broadcast_work_order_updated(client, broadcasts):
    wo = _make_order(client)
    wid = wo["id"]
    client.patch(f"/api/work-orders/{wid}", json={"title": "x"})
    client.post(f"/api/work-orders/{wid}/assign", json={"assigned_to": 1})
    client.post(f"/api/work-orders/{wid}/start")
    client.post(f"/api/work-orders/{wid}/complete", json={})
    other = _make_order(client)
    client.post(f"/api/work-orders/{other['id']}/cancel", json={})

    updates = [(e["type"], e.get("change")) for e in broadcasts]
    assert updates == [
        ("work_order_created", None),
        ("work_order_updated", "edited"),
        ("work_order_updated", "assigned"),
        ("work_order_updated", "started"),
        ("work_order_updated", "completed"),
        ("work_order_created", None),
        ("work_order_updated", "cancelled"),
    ]
    assert all(e["machine_id"] == "m2" and "at" in e for e in broadcasts)


def test_noop_patch_neither_audits_nor_broadcasts(client, broadcasts):
    wo = _make_order(client, description="d")
    for body in ({}, {"title": wo["title"]}):
        resp = client.patch(f"/api/work-orders/{wo['id']}", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["updated_at"] == wo["updated_at"]
    assert [e["type"] for e in broadcasts] == ["work_order_created"]
    detail = client.get(f"/api/work-orders/{wo['id']}").json()
    assert [e["event"] for e in detail["events"]] == ["created"]

    cleared = client.patch(f"/api/work-orders/{wo['id']}", json={"description": None}).json()
    assert cleared["description"] is None


def test_broadcast_failure_still_returns_2xx(client, monkeypatch):
    async def boom(event):
        raise RuntimeError("socket gone")

    monkeypatch.setattr(manager, "broadcast", boom)
    assert client.post("/api/alerts/2/work-order").status_code == 201
    wo = _make_order(client)
    assert client.post(f"/api/work-orders/{wo['id']}/assign", json={"assigned_to": 1}).status_code == 200


def test_assignees_endpoint(auth_client):
    assert auth_client("operator").get("/api/work-orders/assignees").status_code == 403
    for role in ("admin", "supervisor"):
        rows = auth_client(role).get("/api/work-orders/assignees").json()
        assert [r["name"] for r in rows] == ["Ava Admin", "Otis Operator", "Sam Supervisor"]
        assert all(set(r) == {"id", "name", "role"} for r in rows)


def test_detail_has_events_and_alert(client, broadcasts):
    wo = client.post("/api/alerts/2/work-order").json()
    client.post(f"/api/work-orders/{wo['id']}/assign", json={"assigned_to": 2})
    detail = client.get(f"/api/work-orders/{wo['id']}").json()
    assert [e["event"] for e in detail["events"]] == ["created", "assigned"]
    assert detail["events"][1]["assigned_to_name"] == "Sam Supervisor"
    assert detail["alert"]["id"] == 2
    assert detail["assigned_to_name"] == "Sam Supervisor"


def test_list_filters(client, broadcasts):
    a = _make_order(client, assigned_to=3)
    b = _make_order(client)
    client.post(f"/api/work-orders/{b['id']}/cancel", json={})
    assert [w["id"] for w in client.get("/api/work-orders?status=active").json()] == [a["id"]]
    assert [w["id"] for w in client.get("/api/work-orders?assigned_to=3").json()] == [a["id"]]
    assert [w["id"] for w in client.get("/api/work-orders?status=cancelled").json()] == [b["id"]]
    assert client.get("/api/work-orders?machine_id=m1").json() == []
