"""Tests for the FastAPI endpoints (src/api). Uses the `client` fixture whose
get_db is overridden onto the temp DB."""


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_list_machines(client):
    resp = client.get("/api/machines")
    assert resp.status_code == 200
    machines = resp.json()
    assert len(machines) == 2
    assert machines[0]["machine_id"] == "m1"  # highest risk first


def test_machine_detail(client):
    resp = client.get("/api/machines/m1")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"health", "maintenance", "alerts", "maintenance_history"}
    assert body["health"]["health_state"] == "critical"
    assert len(body["alerts"]) == 2  # one open, one resolved
    assert body["maintenance_history"] == []


def test_machine_detail_unknown_404(client):
    assert client.get("/api/machines/ghost").status_code == 404


def test_trends_ok_and_oldest_first(client):
    resp = client.get("/api/machines/m1/trends?metric=vibration_h_rms&limit=10")
    assert resp.status_code == 200
    points = resp.json()
    assert len(points) == 2
    assert points[0]["timestamp"] < points[1]["timestamp"]


def test_trends_bad_metric_400(client):
    assert client.get("/api/machines/m1/trends?metric=bogus").status_code == 400


def test_alerts_filter_open(client):
    resp = client.get("/api/alerts?status=open")
    assert resp.status_code == 200
    alerts = resp.json()
    assert len(alerts) == 1
    assert alerts[0]["status"] == "open"


def test_maintenance_post_valid_then_history(client):
    resp = client.post("/api/maintenance", json={
        "machine_id": "m1",
        "performed_at": "2026-07-20T10:00:00",
        "description": "greased",
        "technician": "tech1",
    })
    assert resp.status_code == 201
    assert resp.json()["description"] == "greased"

    history = client.get("/api/maintenance/m1").json()
    assert len(history) == 1


def test_maintenance_post_unknown_machine_400(client):
    resp = client.post("/api/maintenance", json={
        "machine_id": "ghost", "performed_at": "2026-07-20T10:00:00",
    })
    assert resp.status_code == 400


def test_maintenance_post_bad_date_400(client):
    resp = client.post("/api/maintenance", json={
        "machine_id": "m1", "performed_at": "not-a-date",
    })
    assert resp.status_code == 400


def test_kpi_summary(client):
    body = client.get("/api/kpis").json()
    assert body["machine_count"] == 2
    assert body["open_alert_count"] == 1


def test_kpi_detail_has_all_categories(client):
    body = client.get("/api/kpis/detail").json()
    assert set(body) == {"machine_health", "maintenance", "prediction", "operational", "system"}


def test_kpi_per_machine_404(client):
    assert client.get("/api/kpis/ghost").status_code == 404


def test_acknowledge_alert_sets_fields_and_broadcasts(client):
    resp = client.post("/api/alerts/2/acknowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 2
    assert body["acknowledged_at"] is not None
    assert body["acknowledged_by"] is not None


def test_acknowledge_alert_is_idempotent(client):
    first = client.post("/api/alerts/2/acknowledge").json()
    second = client.post("/api/alerts/2/acknowledge").json()
    assert second["acknowledged_at"] == first["acknowledged_at"]
    assert second["acknowledged_by"] == first["acknowledged_by"]


def test_acknowledge_alert_unknown_id_404(client):
    assert client.post("/api/alerts/9999/acknowledge").status_code == 404


def test_acknowledge_alert_allows_all_three_roles(auth_client):
    for role in ("admin", "supervisor", "operator"):
        c = auth_client(role)
        resp = c.post("/api/alerts/2/acknowledge")
        assert resp.status_code == 200, f"{role} should be able to acknowledge"


def test_acknowledge_alert_requires_login(anon_client):
    assert anon_client.post("/api/alerts/2/acknowledge").status_code == 401
