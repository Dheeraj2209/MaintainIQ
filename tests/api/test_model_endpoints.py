"""API tests for the Phase 5 observability endpoints."""
import sqlite3
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc)


def _log(db_path, *, status="ok", latency_ms=10.0, ood=0, warming=0,
         seconds_ago=30.0, model_version="v1"):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT INTO model_inference_log
                   (machine_id, timestamp, model_version, latency_ms, failure_probability,
                    predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                    status, error_message)
               VALUES ('m1', ?, ?, ?, NULL, NULL, ?, ?, 0, ?, ?)""",
            (
                (_now() - timedelta(seconds=seconds_ago)).isoformat(),
                model_version, latency_ms, ood, warming, status,
                None if status == "ok" else "boom",
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO model_registry (model_version, artifact_path, is_active)
               VALUES (?, 'models/xjtu_rul.joblib', 1)""",
            (model_version,),
        )
        conn.commit()
    finally:
        conn.close()


def test_health_requires_auth(anon_client):
    resp = anon_client.get("/api/model/health")
    assert resp.status_code == 401


def test_telemetry_requires_auth(anon_client):
    resp = anon_client.get("/api/model/telemetry")
    assert resp.status_code == 401


def test_health_healthy_after_recent_inference(client, db_path):
    _log(db_path, status="ok", seconds_ago=30.0, model_version="v1")
    resp = client.get("/api/model/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["model_version"] == "v1"
    assert body["active"] is True
    assert body["last_inference_at"] is not None
    assert body["seconds_since_last_inference"] >= 0.0


def test_health_stale_with_tight_window_env(client, db_path, monkeypatch):
    _log(db_path, status="ok", seconds_ago=300.0, model_version="v1")
    monkeypatch.setenv("MAINTAINIQ_MODEL_HEARTBEAT_SECONDS", "60")
    resp = client.get("/api/model/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "stale"   # 300s ago > 60s window


def test_telemetry_counts_and_rates(client, db_path):
    _log(db_path, status="ok", latency_ms=10.0, ood=0, warming=0, seconds_ago=10)
    _log(db_path, status="ok", latency_ms=20.0, ood=1, warming=0, seconds_ago=11)
    _log(db_path, status="ok", latency_ms=30.0, ood=0, warming=1, seconds_ago=12)
    _log(db_path, status="ok", latency_ms=40.0, ood=1, warming=1, seconds_ago=13)
    _log(db_path, status="error", latency_ms=50.0, seconds_ago=14)
    resp = client.get("/api/model/telemetry?window_minutes=60")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inference_count"] == 5
    assert body["error_rate"] == 0.2
    assert body["ood_rate"] == 0.5
    assert body["warming_up_rate"] == 0.5
    assert body["latency_p50_ms"] == 30.0
    assert body["latency_p95_ms"] == 50.0


def test_telemetry_rejects_nonpositive_window(client, db_path):
    resp = client.get("/api/model/telemetry?window_minutes=0")
    assert resp.status_code == 422
