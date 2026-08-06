"""Unit tests for the observability compute functions (Phase 5 Task 1)."""
from datetime import datetime, timedelta, timezone

from src.observability import model_health, telemetry

NOW = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _log(conn, *, status="ok", latency_ms=10.0, ood=0, warming=0,
         minutes_ago=5.0, model_version="v1"):
    conn.execute(
        """INSERT INTO model_inference_log
               (machine_id, timestamp, model_version, latency_ms, failure_probability,
                predicted_rul_minutes, out_of_distribution, warming_up, warnings_count,
                status, error_message)
           VALUES ('m1', :ts, :mv, :lat, NULL, NULL, :ood, :warming, 0, :status, :err)""",
        {
            "ts": _iso(NOW - timedelta(minutes=minutes_ago)),
            "mv": model_version,
            "lat": latency_ms,
            "ood": ood,
            "warming": warming,
            "status": status,
            "err": None if status == "ok" else "boom",
        },
    )
    conn.commit()


def _register(conn, model_version="v1"):
    conn.execute(
        """INSERT INTO model_registry (model_version, artifact_path, is_active)
           VALUES (?, 'models/xjtu_rul.joblib', 1)""",
        (model_version,),
    )
    conn.commit()


# ---- telemetry ----

def _seed_telemetry_fixture(conn):
    # 5 rows inside a 60-min window + 1 old row outside it.
    _log(conn, status="ok", latency_ms=10.0, ood=0, warming=0, minutes_ago=10)
    _log(conn, status="ok", latency_ms=20.0, ood=1, warming=0, minutes_ago=11)
    _log(conn, status="ok", latency_ms=30.0, ood=0, warming=1, minutes_ago=12)
    _log(conn, status="ok", latency_ms=40.0, ood=1, warming=1, minutes_ago=13)
    _log(conn, status="error", latency_ms=50.0, minutes_ago=14)
    _log(conn, status="ok", latency_ms=9999.0, ood=1, warming=1, minutes_ago=120)  # outside window


def test_telemetry_counts_and_rates_match_hand_computed(conn):
    _seed_telemetry_fixture(conn)
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    assert t["window_minutes"] == 60.0
    assert t["inference_count"] == 5           # the old row is excluded
    assert t["error_rate"] == 0.2              # 1 error / 5
    assert t["ood_rate"] == 0.5                # 2 ood / 4 ok
    assert t["warming_up_rate"] == 0.5         # 2 warming / 4 ok


def test_telemetry_percentiles_nearest_rank(conn):
    _seed_telemetry_fixture(conn)
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    # sorted latencies in window: [10, 20, 30, 40, 50]
    # p50: ceil(0.50*5)=3 -> index 2 -> 30 ; p95: ceil(0.95*5)=5 -> index 4 -> 50
    assert t["latency_p50_ms"] == 30.0
    assert t["latency_p95_ms"] == 50.0


def test_telemetry_empty_window_is_zeroed(conn):
    t = telemetry.compute_telemetry(conn, now=NOW, window_minutes=60.0)
    assert t["inference_count"] == 0
    assert t["error_rate"] == 0.0
    assert t["ood_rate"] == 0.0
    assert t["warming_up_rate"] == 0.0
    assert t["latency_p50_ms"] is None
    assert t["latency_p95_ms"] is None


def test_percentile_helper_edges():
    assert telemetry._percentile([], 50) is None
    assert telemetry._percentile([42.0], 50) == 42.0
    assert telemetry._percentile([42.0], 95) == 42.0
    assert telemetry._percentile([10.0, 20.0], 50) == 10.0   # ceil(1.0)=1 -> idx 0
    assert telemetry._percentile([10.0, 20.0], 95) == 20.0   # ceil(1.9)=2 -> idx 1


# ---- health ----

def test_health_healthy_within_window(conn):
    _register(conn, "v1")
    _log(conn, status="ok", minutes_ago=5.0, model_version="v1")  # 300s ago
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["status"] == "healthy"
    assert h["model_version"] == "v1"
    assert h["active"] is True
    assert h["last_inference_at"] == _iso(NOW - timedelta(minutes=5.0))
    assert h["seconds_since_last_inference"] == 300.0


def test_health_stale_when_last_inference_older_than_window(conn):
    _register(conn, "v1")
    _log(conn, status="ok", minutes_ago=5.0)  # 300s ago
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=120.0)
    assert h["status"] == "stale"              # 300 > 120
    assert h["seconds_since_last_inference"] == 300.0


def test_health_ignores_error_rows_for_heartbeat(conn):
    _register(conn, "v1")
    _log(conn, status="error", minutes_ago=1.0)   # recent, but a failure
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["last_inference_at"] is None          # no successful inference
    assert h["seconds_since_last_inference"] is None
    assert h["status"] == "stale"


def test_health_no_registry_no_inferences(conn):
    h = model_health.compute_health(conn, now=NOW, stale_after_seconds=900.0)
    assert h["status"] == "stale"
    assert h["model_version"] is None
    assert h["active"] is False
    assert h["last_inference_at"] is None
    assert h["seconds_since_last_inference"] is None
