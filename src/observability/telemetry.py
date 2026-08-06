"""Rolling telemetry aggregates over model_inference_log.

Counts and rates are computed in SQL; latency percentiles are computed in
Python (SQLite has no PERCENTILE_CONT) from a single ordered query using the
nearest-rank method. All aggregates are windowed by `now - window_minutes`.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

DEFAULT_WINDOW_MINUTES = 60.0

_AGG_SQL = """
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
        SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
        SUM(CASE WHEN status = 'ok' THEN out_of_distribution ELSE 0 END) AS ood_sum,
        SUM(CASE WHEN status = 'ok' THEN warming_up ELSE 0 END) AS warming_sum
    FROM model_inference_log
    WHERE timestamp >= :cutoff
"""

_LATENCY_SQL = """
    SELECT latency_ms FROM model_inference_log
    WHERE timestamp >= :cutoff
    ORDER BY latency_ms ASC
"""


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile over an ascending-sorted list. None if empty."""
    n = len(sorted_values)
    if n == 0:
        return None
    rank = math.ceil(pct / 100.0 * n)
    index = min(max(rank, 1), n) - 1
    return sorted_values[index]


def compute_telemetry(conn, *, now: datetime, window_minutes: float) -> dict:
    cutoff = (now - timedelta(minutes=window_minutes)).isoformat()
    agg = conn.execute(_AGG_SQL, {"cutoff": cutoff}).fetchone()
    total = agg["total"] or 0
    errors = agg["errors"] or 0
    ok_count = agg["ok_count"] or 0
    ood_sum = agg["ood_sum"] or 0
    warming_sum = agg["warming_sum"] or 0

    latencies = [r["latency_ms"] for r in conn.execute(_LATENCY_SQL, {"cutoff": cutoff}).fetchall()]

    return {
        "window_minutes": window_minutes,
        "inference_count": total,
        "error_rate": (errors / total) if total else 0.0,
        "latency_p50_ms": _percentile(latencies, 50.0),
        "latency_p95_ms": _percentile(latencies, 95.0),
        "ood_rate": (ood_sum / ok_count) if ok_count else 0.0,
        "warming_up_rate": (warming_sum / ok_count) if ok_count else 0.0,
    }
