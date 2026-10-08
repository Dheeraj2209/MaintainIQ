"""Model heartbeat: is a successful inference recent enough to call the model live?

Reports the true last successful inference and the active registered model; the
`status` field ('healthy'/'stale') is decided against a configurable window so a
caller can tune sensitivity without changing what is measured.
"""
from __future__ import annotations

from datetime import datetime

from src.storage.model_registry import active_model

DEFAULT_STALE_AFTER_SECONDS = 900.0

_LAST_OK_SQL = """
    SELECT MAX(timestamp) AS last_ts FROM model_inference_log WHERE status = 'ok'
"""



def compute_health(conn, *, now: datetime, stale_after_seconds: float) -> dict:
    last_row = conn.execute(_LAST_OK_SQL).fetchone()
    last_ts = last_row["last_ts"] if last_row else None

    active_row = active_model(conn)
    model_version = active_row["model_version"] if active_row else None

    seconds_since = None
    if last_ts is not None:
        seconds_since = (now - datetime.fromisoformat(last_ts)).total_seconds()

    healthy = seconds_since is not None and seconds_since <= stale_after_seconds

    return {
        "status": "healthy" if healthy else "stale",
        "model_version": model_version,
        "last_inference_at": last_ts,
        "seconds_since_last_inference": seconds_since,
        "active": active_row is not None,
    }
