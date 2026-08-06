"""API tests for the reports subsystem (Phase 6 Task 2).

tests/api/conftest.py clears the shared `predictions` seed, so these tests seed
their own prediction rows on db_path. machines/alerts/users seed rows remain.
"""
import json
import sqlite3


def _seed_prediction(db_path, *, machine_id="m1",
                     timestamp="2030-01-01T00:00:00+00:00",
                     health_state="critical", rul=30.0):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO predictions
                   (machine_id, timestamp, health_state, source, created_at,
                    predicted_rul_minutes, rul_estimate_kind, prediction_interval_low,
                    prediction_interval_high, model_version, out_of_distribution)
               VALUES (?, ?, ?, 'xjtu_rul', ?, ?, 'point_estimate', ?, ?, 'v1', 0)""",
            (machine_id, timestamp, health_state, timestamp, rul, rul * 0.7, rul * 1.3),
        )
        conn.commit()
    finally:
        conn.close()


def test_create_requires_auth(anon_client):
    resp = anon_client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"}
    )
    assert resp.status_code == 401


def test_list_requires_auth(anon_client):
    assert anon_client.get("/api/reports").status_code == 401


def test_operator_can_generate_machine_prognostic(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("operator")
    resp = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "json"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_type"] == "machine_prognostic"
    assert body["summary"]["current"]["predicted_rul_minutes"] == 30.0
    assert "summary_json" not in body


def test_operator_cannot_generate_fleet_summary(auth_client):
    client = auth_client("operator")
    resp = client.post(
        "/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"}
    )
    assert resp.status_code == 403


def test_supervisor_can_generate_fleet_summary(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports",
        json={"report_type": "fleet_summary", "scope": "fleet", "format": "markdown"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["report_type"] == "fleet_summary"


def test_supervisor_can_generate_model_performance(auth_client):
    """Coverage note (Phase 6 Task 1 -> Task 2): exercise build_summary for the
    third report type end-to-end via service.generate, alongside the
    machine_prognostic and fleet_summary happy paths already above."""
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports",
        json={"report_type": "model_performance", "scope": "fleet", "format": "json"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_type"] == "model_performance"
    assert body["summary"]["inference_count"] == 0


def test_create_unknown_machine_404(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "ghost"}
    )
    assert resp.status_code == 404


def test_create_rejects_bad_type(auth_client):
    client = auth_client("supervisor")
    resp = client.post(
        "/api/reports", json={"report_type": "nonsense", "scope": "fleet"}
    )
    assert resp.status_code == 422


def test_get_by_id_returns_stored_report(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"}
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rid
    assert resp.json()["content"]


def test_get_missing_report_404(auth_client):
    client = auth_client("supervisor")
    assert client.get("/api/reports/99999").status_code == 404


def test_download_sets_content_type_and_disposition(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "markdown"},
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert f'filename="report-{rid}.md"' in resp.headers["content-disposition"]
    assert resp.text.startswith("# Machine Prognostic Report")


def test_download_json_report(auth_client, db_path):
    _seed_prediction(db_path)
    client = auth_client("supervisor")
    created = client.post(
        "/api/reports",
        json={"report_type": "machine_prognostic", "scope": "m1", "format": "json"},
    ).json()
    rid = created["id"]
    resp = client.get(f"/api/reports/{rid}/download")
    assert resp.headers["content-type"].startswith("application/json")
    assert json.loads(resp.text)["report_type"] == "machine_prognostic"


def test_operator_cannot_read_elevated_report(auth_client):
    sup = auth_client("supervisor")
    created = sup.post(
        "/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"}
    ).json()
    rid = created["id"]
    op = auth_client("operator")
    assert op.get(f"/api/reports/{rid}").status_code == 403
    assert op.get(f"/api/reports/{rid}/download").status_code == 403


def test_list_filters_by_role(auth_client, db_path):
    _seed_prediction(db_path)
    sup = auth_client("supervisor")
    sup.post("/api/reports", json={"report_type": "machine_prognostic", "scope": "m1"})
    sup.post("/api/reports", json={"report_type": "fleet_summary", "scope": "fleet"})
    assert len(sup.get("/api/reports").json()) == 2
    op = auth_client("operator")
    rows = op.get("/api/reports").json()
    assert len(rows) == 1
    assert all(r["report_type"] == "machine_prognostic" for r in rows)
    assert all("content" not in r for r in rows)
