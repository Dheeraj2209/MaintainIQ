"""SQLite schema and access for the storage layer.

Design note (documented limitation, per this project's "flag limitations"
convention): the original plan called for separate "raw readings" and
"feature windows" tables, but the XJTU-SY ingestion/backfill path produces
pre-windowed feature rows — there is no raw waveform retained alongside them.
The `readings` table below therefore represents both at once (one row per
machine per cycle, holding its extracted features). The M6 live MQTT path
keeps that shape: it decodes each snapshot, extracts features, and stores only
the feature row (dataset='live_mqtt'); the raw waveform is not persisted.
Per-message delivery metadata lives in telemetry_messages instead.

Each XJTU-SY bearing is instrumented on two axes (horizontal + vertical), so
the columns every downstream module filters or sorts on (vibration_h_rms,
vibration_h_kurtosis, vibration_v_rms, vibration_v_kurtosis,
cross_axis_rms_ratio, cross_axis_correlation) are promoted to real columns for
queryability; the full per-window feature vector still lives in the
`features_json` blob so new features need no schema change.

See docs/DATA_MODEL.md for the full table-by-table reference.
"""
import os
import sqlite3
from pathlib import Path

# Overridable via MAINTAINIQ_DB_PATH (e.g. to point at a mounted volume in
# Docker) since the file lives outside the repo tree in that case.
DEFAULT_DB_PATH = Path(os.environ.get("MAINTAINIQ_DB_PATH", str(Path(__file__).resolve().parents[2] / "maintainiq.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id            TEXT PRIMARY KEY,
    bearing_id            TEXT,
    operating_condition   INTEGER,
    speed_rpm             REAL,
    load_kn               REAL,
    dataset               TEXT NOT NULL DEFAULT 'xjtu_sy',
    is_documented_failure INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS readings (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id             TEXT NOT NULL REFERENCES machines(machine_id),
    timestamp              TEXT NOT NULL,
    cycle                  INTEGER NOT NULL,
    elapsed_minutes        REAL NOT NULL,
    speed_rpm              REAL NOT NULL,
    load_kn                REAL NOT NULL,
    sample_rate_hz         REAL NOT NULL,
    vibration_h_rms        REAL NOT NULL,
    vibration_h_kurtosis   REAL NOT NULL,
    vibration_v_rms        REAL NOT NULL,
    vibration_v_kurtosis   REAL NOT NULL,
    cross_axis_rms_ratio   REAL NOT NULL,
    cross_axis_correlation REAL NOT NULL,
    rul_minutes            REAL,
    features_json          TEXT NOT NULL,
    dataset                TEXT NOT NULL DEFAULT 'xjtu_sy',
    UNIQUE(machine_id, cycle)
);
CREATE INDEX IF NOT EXISTS idx_readings_machine_ts ON readings(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS predictions (
    id                                 INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id                         TEXT NOT NULL REFERENCES machines(machine_id),
    reading_id                         INTEGER REFERENCES readings(id),
    timestamp                          TEXT NOT NULL,
    health_state                       TEXT NOT NULL,
    confidence                         REAL,
    source                             TEXT NOT NULL DEFAULT 'xjtu_rul',
    model_name                         TEXT,
    probable_cause                     TEXT,
    created_at                         TEXT NOT NULL DEFAULT (datetime('now')),
    predicted_rul_minutes              REAL,
    rul_estimate_kind                  TEXT,
    failure_within_horizon_probability REAL,
    prognostic_horizon_minutes         REAL,
    prediction_interval_low            REAL,
    prediction_interval_high           REAL,
    model_version                      TEXT,
    out_of_distribution                INTEGER NOT NULL DEFAULT 0,
    history_snapshots                  INTEGER,
    warnings_json                      TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_machine_ts ON predictions(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS model_inference_log (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id             TEXT NOT NULL,
    timestamp              TEXT NOT NULL,
    model_version          TEXT NOT NULL,
    latency_ms             REAL NOT NULL,
    failure_probability    REAL,
    predicted_rul_minutes  REAL,
    out_of_distribution    INTEGER NOT NULL DEFAULT 0,
    warming_up             INTEGER NOT NULL DEFAULT 0,
    warnings_count         INTEGER NOT NULL DEFAULT 0,
    status                 TEXT NOT NULL DEFAULT 'ok',
    error_message          TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_inference_model_ts ON model_inference_log(model_version, timestamp);
CREATE INDEX IF NOT EXISTS idx_inference_machine_ts ON model_inference_log(machine_id, timestamp);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version  TEXT PRIMARY KEY,
    artifact_path  TEXT NOT NULL,
    algorithm      TEXT,
    trained_at     TEXT,
    metrics_json   TEXT,
    deployed_at    TEXT,
    is_active      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type   TEXT NOT NULL,
    scope         TEXT NOT NULL,
    format        TEXT NOT NULL,
    period_start  TEXT,
    period_end    TEXT,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    generated_by  INTEGER,
    content       TEXT NOT NULL,
    summary_json  TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    severity TEXT NOT NULL,
    health_state TEXT NOT NULL,
    probable_cause TEXT,
    message TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    acknowledged_by INTEGER,
    page_level INTEGER NOT NULL DEFAULT 0,
    last_paged_at TEXT,
    -- The prediction/reading/model that opened the alert (written on INSERT
    -- only, kept through escalation) and the user who closed it, if a person
    -- did (design/2026-10-07-prediction-feedback-design.md, decisions 3-4).
    prediction_id INTEGER REFERENCES predictions(id),
    reading_id INTEGER REFERENCES readings(id),
    model_version TEXT,
    closed_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_status ON alerts(machine_id, status);

CREATE TABLE IF NOT EXISTS maintenance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    performed_at TEXT NOT NULL,
    description TEXT,
    technician TEXT,
    created_at TEXT NOT NULL,
    alert_id INTEGER REFERENCES alerts(id),
    type TEXT CHECK(type IN ('preventive','corrective'))
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','supervisor','operator')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER REFERENCES alerts(id),
    device_incident_id INTEGER REFERENCES device_incidents(id),
    recipient_email TEXT NOT NULL,
    recipient_role TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    channel TEXT NOT NULL DEFAULT 'email',
    created_at TEXT NOT NULL
);"""

# M6 live-telemetry tables (design/M6_LIVE_TELEMETRY.md §6). Kept as a separate
# constant so migration 2 can apply exactly this DDL to a DB already stamped at
# v1, while SCHEMA (below) still carries every table for fresh installs.
#
# telemetry_messages is one row per MQTT snapshot the ingest service saw —
# accepted, rejected, or errored — and is the sole source for the system KPIs
# (collection/transmission rates come from seq gaps, sync lag from
# received_at - sampled_at). UNIQUE(device_id, boot_id, seq) is the
# idempotency key: a QoS-1 redelivery or a re-flushed edge buffer must not
# double-count. device_status holds only the LATEST retained status per
# device (counters are cumulative since boot), so it is upserted, not appended.
TELEMETRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     TEXT NOT NULL,
    boot_id       TEXT,
    seq           INTEGER,
    machine_id    TEXT,
    sampled_at    TEXT,
    received_at   TEXT NOT NULL,
    buffered      INTEGER NOT NULL DEFAULT 0,
    time_synced   INTEGER NOT NULL DEFAULT 1,
    status        TEXT NOT NULL CHECK(status IN ('accepted','rejected','error')),
    error         TEXT,
    reading_id    INTEGER REFERENCES readings(id),
    latency_ms    REAL,
    payload_bytes INTEGER,
    UNIQUE(device_id, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_received ON telemetry_messages(received_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry_messages(device_id, boot_id, seq);

CREATE TABLE IF NOT EXISTS device_status (
    device_id               TEXT PRIMARY KEY,
    machine_id              TEXT,
    boot_id                 TEXT,
    online                  INTEGER NOT NULL DEFAULT 0,
    last_seen_at            TEXT NOT NULL,
    reported_at             TEXT,
    uptime_s                REAL,
    firmware                TEXT,
    snapshot_interval_s     REAL,
    heartbeat_interval_s    REAL,
    buffer_depth            INTEGER,
    buffer_capacity         INTEGER,
    buffer_dropped_total    INTEGER,
    publish_attempts_total  INTEGER,
    publish_failures_total  INTEGER,
    wifi_rssi_dbm           REAL,
    payload_json            TEXT
);
"""

# Device-health incidents (design/2026-10-06-device-health-design.md). One row
# per silence episode of a sensor node; opened and resolved only by the
# background watchdog (src/telemetry/watchdog.py), acknowledged by a human.
# Separate from `alerts` on purpose: alerts are machine-health episodes and
# the live alert path would auto-resolve or escalate a device incident.
# Applied on its own by migration 3 (and by ensure_telemetry_schema on an
# unversioned file), and folded into SCHEMA for fresh installs.
DEVICE_HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id        TEXT NOT NULL,
    machine_id       TEXT,
    kind             TEXT NOT NULL CHECK(kind IN ('silent','lwt')),
    status           TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    opened_at        TEXT NOT NULL,
    last_seen_at     TEXT,
    resolved_at      TEXT,
    acknowledged_at  TEXT,
    acknowledged_by  INTEGER,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_device_incidents_device_status ON device_incidents(device_id, status);
CREATE INDEX IF NOT EXISTS idx_device_incidents_opened ON device_incidents(opened_at);
-- At most one open incident per device: makes the watchdog's open path
-- idempotent and stops a second app instance from double-paging.
CREATE UNIQUE INDEX IF NOT EXISTS uq_device_incidents_one_open
    ON device_incidents(device_id) WHERE status = 'open';
"""

# Work orders (design/2026-10-07-work-orders-escalation-design.md): a tracked
# repair job, optionally raised from an alert, with an append-only event trail.
# At most one active (open/assigned/in_progress) order per alert, enforced by a
# partial unique index so duplicate creation is race-free. Completing an order
# writes a maintenance_records row (maintenance_record_id) so maintenance KPIs
# stay single-sourced. Applied on its own by migration 4 and folded into
# SCHEMA for fresh installs.
WORK_ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id               INTEGER REFERENCES alerts(id),
    machine_id             TEXT NOT NULL REFERENCES machines(machine_id),
    status                 TEXT NOT NULL DEFAULT 'open'
                           CHECK(status IN ('open','assigned','in_progress','done','cancelled')),
    priority               TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('low','medium','high')),
    title                  TEXT NOT NULL,
    description            TEXT,
    assigned_to            INTEGER,
    created_by             INTEGER,
    due_at                 TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    started_at             TEXT,
    completed_at           TEXT,
    cancelled_at           TEXT,
    maintenance_record_id  INTEGER REFERENCES maintenance_records(id),
    notes                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_work_orders_status ON work_orders(status, machine_id);
CREATE INDEX IF NOT EXISTS idx_work_orders_assignee ON work_orders(assigned_to, status);
-- One active order per alert; finished/cancelled orders leave the index.
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_orders_one_active_per_alert
    ON work_orders(alert_id)
    WHERE alert_id IS NOT NULL AND status IN ('open','assigned','in_progress');

CREATE TABLE IF NOT EXISTS work_order_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    work_order_id  INTEGER NOT NULL REFERENCES work_orders(id),
    event          TEXT NOT NULL
                   CHECK(event IN ('created','edited','assigned','started','completed','cancelled')),
    from_status    TEXT,
    to_status      TEXT,
    user_id        INTEGER,
    assigned_to    INTEGER,
    note           TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_order_events_order ON work_order_events(work_order_id, id);
"""

# Prediction feedback (design/2026-10-07-prediction-feedback-design.md): what
# actually happened after an alert, recorded by a person when the alert is
# closed (or later). One row per alert, editable; the source of real-world
# accuracy (src/feedback/accuracy.py) and of field episodes for retraining
# (src/feedback/export.py). Applied on its own by migration 5 and folded into
# SCHEMA for fresh installs.
FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_feedback (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id           INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
    outcome            TEXT NOT NULL
                       CHECK(outcome IN ('confirmed_failure','maintenance_prevented','false_alarm','unknown')),
    actual_cause       TEXT
                       CHECK(actual_cause IS NULL OR actual_cause IN
                             ('bearing_wear','imbalance','sensor_or_data_quality_issue','unknown','other')),
    actual_failure_at  TEXT,
    notes              TEXT,
    work_order_id      INTEGER REFERENCES work_orders(id),
    recorded_by        INTEGER NOT NULL,
    recorded_at        TEXT NOT NULL,
    updated_by         INTEGER,
    updated_at         TEXT,
    -- A failure time only makes sense for a failure.
    CHECK(actual_failure_at IS NULL OR outcome = 'confirmed_failure')
);
CREATE INDEX IF NOT EXISTS idx_alert_feedback_outcome ON alert_feedback(outcome);
"""

# Alert explanations (design/2026-10-07-alert-explanation-design.md): the
# evidence behind an alert — triggering readings, key factors, the model
# prediction and the probable-cause rule trace — captured when the alert is
# created and again at each severity escalation, because the predictor's
# rolling context is in memory only and escalation overwrites the alert's
# probable_cause. Similar incidents are not stored; they are computed when
# read. Applied on its own by migration 6 and folded into SCHEMA for fresh
# installs.
EXPLANATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_explanations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id          INTEGER NOT NULL REFERENCES alerts(id),
    kind              TEXT NOT NULL CHECK(kind IN ('created','escalated')),
    reading_id        INTEGER REFERENCES readings(id),
    prediction_id     INTEGER REFERENCES predictions(id),
    created_at        TEXT NOT NULL,
    explanation_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_explanations_alert ON alert_explanations(alert_id, id);
-- At most one creation snapshot per alert; escalations may add more rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_explanations_one_created
    ON alert_explanations(alert_id) WHERE kind = 'created';
"""

# Web Push subscriptions (design/2026-10-07-mobile-operator-pwa-design.md):
# one row per browser push endpoint, bound to the user who last subscribed
# on that device. Unsubscribing deletes the row; a 404/410 from the push
# service deactivates it (is_active = 0) so dead devices stay visible.
# Pages are logged in `notifications` with channel = 'push'. Applied on its
# own by migration 7 and folded into SCHEMA for fresh installs.
PUSH_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    endpoint        TEXT NOT NULL UNIQUE,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    user_agent      TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    last_used_at    TEXT,
    deactivated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id, is_active);
"""

SCHEMA = (SCHEMA + TELEMETRY_SCHEMA + DEVICE_HEALTH_SCHEMA + WORK_ORDER_SCHEMA + FEEDBACK_SCHEMA
          + EXPLANATION_SCHEMA + PUSH_SCHEMA)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Whether `name` is a table in conn's main database. Readers of tables
    added by a later migration guard with this: migrations don't run at app
    start, so an old file opened outside get_db may lack them."""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False: FastAPI runs sync endpoints and dependency
    # teardown across an AnyIO threadpool, so a single request's connection may
    # be created, used, and closed on different threads. Each request still gets
    # its own connection (no concurrent sharing), so this is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    # Delegates to the versioned migration runner (design §4.8). Import locally
    # to avoid a circular import: migrations.py imports SCHEMA from this module.
    from src.storage.migrations import run_migrations

    run_migrations(conn)


_READING_COLUMNS = (
    "machine_id", "timestamp", "cycle", "elapsed_minutes", "speed_rpm", "load_kn",
    "sample_rate_hz", "vibration_h_rms", "vibration_h_kurtosis", "vibration_v_rms",
    "vibration_v_kurtosis", "cross_axis_rms_ratio", "cross_axis_correlation",
    "rul_minutes", "features_json", "dataset",
)

_READING_INSERT_SQL = (
    f"INTO readings ({', '.join(_READING_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in _READING_COLUMNS)})"
)


def _normalize_reading(reading: dict) -> dict:
    """Fill a canonical reading dict: default dataset, and features_json='{}'
    when absent (the live/demo path stores no full feature vector)."""
    row = {c: reading.get(c) for c in _READING_COLUMNS}
    row["dataset"] = reading.get("dataset") or "xjtu_sy"
    if row["features_json"] is None:
        row["features_json"] = "{}"
    return row


def insert_machines(conn: sqlite3.Connection, machines: list) -> None:
    """machines: list of dicts. Required key: machine_id. Optional: bearing_id,
    operating_condition, speed_rpm, load_kn, dataset (default 'xjtu_sy'),
    is_documented_failure (default 0)."""
    conn.executemany(
        """INSERT OR IGNORE INTO machines
           (machine_id, bearing_id, operating_condition, speed_rpm, load_kn,
            dataset, is_documented_failure)
           VALUES (:machine_id, :bearing_id, :operating_condition, :speed_rpm,
                   :load_kn, :dataset, :is_documented_failure)""",
        [
            {
                "machine_id": m["machine_id"],
                "bearing_id": m.get("bearing_id"),
                "operating_condition": m.get("operating_condition"),
                "speed_rpm": m.get("speed_rpm"),
                "load_kn": m.get("load_kn"),
                "dataset": m.get("dataset") or "xjtu_sy",
                "is_documented_failure": int(m.get("is_documented_failure", 0)),
            }
            for m in machines
        ],
    )
    conn.commit()


def insert_readings(conn: sqlite3.Connection, readings: list) -> None:
    """readings: list of canonical reading dicts (see _READING_COLUMNS).
    Idempotent on (machine_id, cycle)."""
    conn.executemany(
        "INSERT OR IGNORE " + _READING_INSERT_SQL,
        [_normalize_reading(r) for r in readings],
    )
    conn.commit()


def insert_single_reading(conn: sqlite3.Connection, reading: dict) -> int:
    """Single-row counterpart to insert_readings for the live/demo path
    (src/prediction/live.py). Returns the new row's id."""
    cur = conn.execute("INSERT " + _READING_INSERT_SQL, _normalize_reading(reading))
    conn.commit()
    return cur.lastrowid


def get_reading_id_map(conn: sqlite3.Connection) -> dict:
    """(machine_id, timestamp_iso) -> reading_id, for linking predictions/alerts
    to the reading row inserted by insert_readings."""
    cur = conn.execute("SELECT id, machine_id, timestamp FROM readings")
    return {(row["machine_id"], row["timestamp"]): row["id"] for row in cur.fetchall()}


def insert_predictions(conn: sqlite3.Connection, predictions: list) -> None:
    """predictions: list of dicts with keys reading_id, machine_id, timestamp,
    health_state, confidence, source, model_name, probable_cause, created_at."""
    conn.executemany(
        """INSERT INTO predictions
           (reading_id, machine_id, timestamp, health_state, confidence, source,
            model_name, probable_cause, created_at)
           VALUES (:reading_id, :machine_id, :timestamp, :health_state, :confidence,
                   :source, :model_name, :probable_cause, :created_at)""",
        predictions,
    )
    conn.commit()


def insert_alerts(conn: sqlite3.Connection, alerts: list) -> None:
    """alerts: list of dicts with keys machine_id, opened_at, resolved_at, severity,
    health_state, probable_cause, message, status, source, created_at.

    The batch seed path (src/alerts/generation.py): historical alerts, not
    something to page anyone about. When the alerts table has the paging
    columns they are written already at the top paging level with
    last_paged_at NULL ("never paged by the ladder"), exactly like the open
    alerts migration 4 finds, so the paging job never pages them."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(alerts)")}
    if "page_level" in columns:
        from src.storage.migrations import PRE_LADDER_PAGE_LEVEL

        conn.executemany(
            """INSERT INTO alerts
               (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
                message, status, source, created_at, page_level)
               VALUES (:machine_id, :opened_at, :resolved_at, :severity, :health_state,
                       :probable_cause, :message, :status, :source, :created_at, :page_level)""",
            [{**alert, "page_level": PRE_LADDER_PAGE_LEVEL} for alert in alerts],
        )
    else:
        conn.executemany(
            """INSERT INTO alerts
               (machine_id, opened_at, resolved_at, severity, health_state, probable_cause,
                message, status, source, created_at)
               VALUES (:machine_id, :opened_at, :resolved_at, :severity, :health_state,
                       :probable_cause, :message, :status, :source, :created_at)""",
            alerts,
        )
    conn.commit()
