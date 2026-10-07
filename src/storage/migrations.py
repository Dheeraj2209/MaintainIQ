"""Versioned SQLite migration runner (design §4.8).

Replaces db.init_schema's try/except ALTER pattern with an explicit, ordered
list of (version, callable) steps recorded in a `schema_version` table.
run_migrations applies every step whose version exceeds the current version and
stamps each; a second call is a no-op.

Migration 1 establishes the XJTU-SY canonical baseline. On a legacy IMS DB the
dataset tables (machines/readings/predictions) are DROPPED and recreated in the
canonical shape rather than data-migrated: per design §4.8 the DB is regenerated
from XJTU-SY via the backfill, and the new NOT NULL readings columns have no
source in the old IMS rows, so an in-place column copy is impossible. The app
tables (alerts, maintenance_records, users, notifications) are preserved and
upgraded in place via guarded ALTERs.

Migration 2 adds the M6 live-telemetry tables (telemetry_messages,
device_status — design/M6_LIVE_TELEMETRY.md §6). It is purely additive
(CREATE ... IF NOT EXISTS), so a v1 DB keeps every row, and a fresh DB — where
migration 1 already created them via SCHEMA — just gets the version stamp.

Migration 3 adds device health (design/2026-10-06-device-health-design.md):
the device_incidents table with its one-open-incident-per-device partial
unique index, plus notifications.device_incident_id so the paging log can
link back to the incident. Additive like migration 2. It is one mixed step
(executescript, then a guarded ALTER): executescript COMMITs first, and the
ALTER is idempotent, so a crash between the two re-runs cleanly.

Migration 4 adds work orders and alert paging
(design/2026-10-07-work-orders-escalation-design.md): the work_orders and
work_order_events tables (with the one-active-order-per-alert partial unique
index) plus alerts.page_level / alerts.last_paged_at for the paging ladder.
Additive and the same mixed shape as migration 3.

Migration 5 adds prediction feedback
(design/2026-10-07-prediction-feedback-design.md): the alert_feedback table
(one editable outcome row per alert) plus four nullable alerts columns —
prediction_id, reading_id and model_version link an alert to the prediction
that opened it, and closed_by records who closed it by hand. Additive and the
same mixed shape as migrations 3 and 4.

Migration 6 adds alert explanations
(design/2026-10-07-alert-explanation-design.md): the alert_explanations
table, one evidence snapshot per alert creation and per severity escalation
(a partial unique index allows only one 'created' row per alert). Purely
additive with no ALTERs, so it is a single executescript like migration 2.

Migration 7 adds Web Push for the mobile operator view
(design/2026-10-07-mobile-operator-pwa-design.md): the push_subscriptions
table (one row per browser push endpoint) plus notifications.channel
('email' | 'push', DEFAULT 'email' so every existing row reads as an email).
Additive and the same mixed shape as migration 3.

Migration 8 adds the health-state ratchet
(docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md): the
machine_health_state table (epoch, episode, held max_state per machine),
nullable predictions.health_epoch / health_episode / instant_health_state and
alerts.health_episode stamps (pre-ratchet rows stay NULL),
maintenance_records.resets_health (DEFAULT 0), the per-epoch predictions
index, and a deploy seed that holds the level of a pre-deploy open or
human-closed real alert. Additive and the same mixed shape as migration 3.

Migrations still do not run at app start. Instead ensure_current_schema
upgrades a versioned (>= 1) but stale file the first time a request
(src/api/deps.get_db) or the paging job opens it, so new code never reads the
new alert columns from an old file. Unversioned (v0 / legacy) files are never
touched there: migration 1 drops dataset tables, and that stays an explicit
backfill decision.
"""
import logging
import sqlite3
import threading
from datetime import datetime, timezone

from src.storage.db import (
    DEVICE_HEALTH_SCHEMA,
    EXPLANATION_SCHEMA,
    FEEDBACK_SCHEMA,
    PUSH_SCHEMA,
    SCHEMA,
    TELEMETRY_SCHEMA,
    WORK_ORDER_SCHEMA,
)

logger = logging.getLogger(__name__)


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0  # schema_version table does not exist yet
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _migration_001_canonical_baseline(conn: sqlite3.Connection) -> None:
    # Dataset tables are regenerated from XJTU-SY (design §4.8), never
    # data-migrated. Drop legacy IMS-shaped tables (child-first for FK sanity)
    # so the canonical CREATEs below install the new shape.
    for table in ("predictions", "readings", "machines"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # Create every canonical table. IF NOT EXISTS preserves the app tables
    # (alerts/maintenance_records/users/notifications) and their data.
    conn.executescript(SCHEMA)
    # Upgrade pre-existing app tables that predate newer columns. On a fresh DB
    # SCHEMA already created these columns, so the ALTER raises and is ignored.
    for column in ("acknowledged_at TEXT", "acknowledged_by INTEGER"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass
    for column in (
        "alert_id INTEGER REFERENCES alerts(id)",
        "type TEXT CHECK(type IN ('preventive','corrective'))",
    ):
        try:
            conn.execute(f"ALTER TABLE maintenance_records ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass


def _migration_002_live_telemetry(conn: sqlite3.Connection) -> None:
    # Additive only: nothing existing is dropped or altered, so upgrading a
    # populated v1 DB is safe. executescript() issues its own COMMIT first;
    # that is fine here because the step has no earlier statements to keep
    # atomic with the CREATEs.
    conn.executescript(TELEMETRY_SCHEMA)


def _migration_003_device_health(conn: sqlite3.Connection) -> None:
    # Additive: a new table plus one nullable column. executescript() COMMITs
    # first; the guarded ALTER after it is idempotent, so a crash between the
    # two re-runs cleanly.
    conn.executescript(DEVICE_HEALTH_SCHEMA)
    try:
        conn.execute(
            "ALTER TABLE notifications ADD COLUMN device_incident_id INTEGER "
            "REFERENCES device_incidents(id)"
        )
    except sqlite3.OperationalError:
        pass  # fresh DB: SCHEMA already has the column


# Mirrors src/alerts/paging.MAX_PAGE_LEVEL (paging imports this module, so it
# cannot be imported from there).
PRE_LADDER_PAGE_LEVEL = 2


def _migration_004_work_orders(conn: sqlite3.Connection) -> None:
    # Additive: two new tables plus two alerts columns. executescript()
    # COMMITs first; the guarded ALTERs after it are idempotent, so a crash
    # between them re-runs cleanly (same shape as migration 3). No index on the
    # new alerts columns: on a legacy DB migration 1 runs SCHEMA against an
    # alerts table that lacks them, so an index in SCHEMA would fail there.
    conn.executescript(WORK_ORDER_SCHEMA)
    for column in ("page_level INTEGER NOT NULL DEFAULT 0", "last_paged_at TEXT"):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # fresh DB: SCHEMA already has the column
    # Open alerts that exist when the ladder arrives predate it: mark them as
    # already at the top level (src/alerts/paging.MAX_PAGE_LEVEL) so the first
    # paging tick does not page admins and supervisors at level 2 for every
    # weeks-old dataset/demo alert. last_paged_at stays NULL — "never paged by
    # the ladder" — which is how the UI tells them apart from real pages. On a
    # fresh DB this runs before any alert exists, so it touches nothing; it is
    # idempotent, so a crash after the ALTERs re-runs cleanly. Committed by
    # run_migrations together with the version stamp.
    conn.execute(
        "UPDATE alerts SET page_level = ? "
        "WHERE status = 'open' AND page_level = 0 AND last_paged_at IS NULL",
        (PRE_LADDER_PAGE_LEVEL,),
    )


def _migration_005_feedback(conn: sqlite3.Connection) -> None:
    # Additive: the alert_feedback table plus four nullable alerts columns
    # linking an alert to the prediction/reading/model that opened it and the
    # user who closed it. executescript() COMMITs first; the guarded ALTERs
    # after it are idempotent, so a crash between them re-runs cleanly (same
    # shape as migrations 3 and 4). No index on the new alerts columns, for
    # the legacy-DB reason given in migration 4.
    conn.executescript(FEEDBACK_SCHEMA)
    for column in (
        "prediction_id INTEGER REFERENCES predictions(id)",
        "reading_id INTEGER REFERENCES readings(id)",
        "model_version TEXT",
        "closed_by INTEGER",
    ):
        try:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # fresh DB: SCHEMA already has the column


def _migration_006_alert_explanations(conn: sqlite3.Connection) -> None:
    # Purely additive: one new table and its indexes, no ALTERs, so it is a
    # single executescript (same shape as migration 2). A v5 DB keeps every
    # row; a fresh DB, where migration 1 already ran SCHEMA, just gets the
    # version stamp.
    conn.executescript(EXPLANATION_SCHEMA)


def _migration_007_push(conn: sqlite3.Connection) -> None:
    # Additive: the push_subscriptions table plus a channel column on
    # notifications (DEFAULT 'email', so every existing row is labelled
    # correctly). executescript() COMMITs first; the guarded ALTER after it is
    # idempotent, so a crash between the two re-runs cleanly (same shape as
    # migration 3). No index on the new column, for the legacy-DB reason given
    # in migration 4, and no CHECK: SQLite cannot add one to an existing table.
    conn.executescript(PUSH_SCHEMA)
    try:
        conn.execute("ALTER TABLE notifications ADD COLUMN channel TEXT NOT NULL DEFAULT 'email'")
    except sqlite3.OperationalError:
        pass  # fresh DB: SCHEMA already has the column


# The held (ratcheted) health level per machine
# (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md). epoch is the
# baseline life (bumped by a repair reset), episode the alert episode (bumped
# by every reset and false-alarm re-arm), max_state the held level of the
# current episode, and model_version the artifact that derived it (NULL for a
# migration seed). A machine with no row reads as (0, 0, 'healthy').
_HEALTH_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS machine_health_state (
    machine_id         TEXT PRIMARY KEY REFERENCES machines(machine_id),
    epoch              INTEGER NOT NULL DEFAULT 0,
    episode            INTEGER NOT NULL DEFAULT 0,
    max_state          TEXT NOT NULL DEFAULT 'healthy'
                       CHECK(max_state IN ('healthy','degrading','faulty','critical')),
    model_version      TEXT,
    epoch_started_at   TEXT,
    episode_started_at TEXT,
    reset_reason       TEXT,
    updated_at         TEXT NOT NULL
);
"""

# Literals mirror src/prediction/rul_store.PREDICTION_SOURCE and
# src/telemetry/ingest.MQTT_SOURCE (live telemetry runs the same RUL model);
# migrations do not import app modules. Only real model alerts seed: demo and
# other detectors' alerts never do.
_SEED_SOURCE_SQL = "source IN ('xjtu_rul', 'mqtt')"
_STATE_RANK_SQL = (
    "CASE health_state WHEN 'critical' THEN 3 WHEN 'faulty' THEN 2 "
    "WHEN 'degrading' THEN 1 ELSE 0 END"
)
_NO_SEED_OUTCOMES = ("false_alarm",)
_NO_SEED_CAUSES = ("sensor_or_data_quality_issue",)


def _seed_alert(conn: sqlite3.Connection, machine_id: str, has_feedback: bool):
    """The one pre-deploy alert, as (id, health_state, opened_at, ...), whose
    level the machine should hold (plan D14), or None: its worst open real alert; otherwise its newest resolved
    real alert, if that was closed by a person after the machine's last
    maintenance and was not judged a false alarm or a data-quality issue."""
    open_alert = conn.execute(
        f"SELECT id, health_state, opened_at FROM alerts "
        f"WHERE machine_id = ? AND {_SEED_SOURCE_SQL} AND status = 'open' "
        f"ORDER BY {_STATE_RANK_SQL} DESC, id DESC LIMIT 1",
        (machine_id,),
    ).fetchone()
    if open_alert is not None:
        return open_alert
    newest = conn.execute(
        "SELECT id, health_state, opened_at, resolved_at, closed_by FROM alerts "
        f"WHERE machine_id = ? AND {_SEED_SOURCE_SQL} AND status = 'resolved' "
        "ORDER BY resolved_at DESC, id DESC LIMIT 1",
        (machine_id,),
    ).fetchone()
    if newest is None or newest[4] is None:
        return None
    last_maintenance = conn.execute(
        "SELECT MAX(performed_at) AS t FROM maintenance_records WHERE machine_id = ?",
        (machine_id,),
    ).fetchone()[0]
    if last_maintenance is not None and not (newest[3] or "") > last_maintenance:
        return None
    if has_feedback:
        feedback = conn.execute(
            "SELECT outcome, actual_cause FROM alert_feedback WHERE alert_id = ?",
            (newest[0],),
        ).fetchone()
        if feedback is not None and (
            feedback[0] in _NO_SEED_OUTCOMES or feedback[1] in _NO_SEED_CAUSES
        ):
            return None
    return newest


def _migration_008_health_epoch(conn: sqlite3.Connection) -> None:
    # Additive: the machine_health_state table, nullable epoch/episode stamps
    # on predictions and alerts (legacy rows stay NULL, "pre-ratchet"),
    # maintenance_records.resets_health, and the per-epoch predictions index.
    # executescript() COMMITs first; the guarded ALTERs, the CREATE INDEX IF
    # NOT EXISTS and the INSERT OR IGNORE seed are idempotent, so a crash
    # before the version stamp re-runs cleanly (same shape as migration 3).
    # The index comes after the ALTERs: SCHEMA's predictions lacks the column.
    from src.storage.db import table_exists

    conn.executescript(_HEALTH_STATE_SCHEMA)
    for table, column in (
        ("predictions", "health_epoch INTEGER"),
        ("predictions", "health_episode INTEGER"),
        ("predictions", "instant_health_state TEXT"),
        ("alerts", "health_episode INTEGER"),
        ("maintenance_records", "resets_health INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass  # re-run: the column is already there
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_predictions_machine_epoch "
        "ON predictions(machine_id, health_epoch, id)"
    )
    # Deploy seed (plan D14): hold the level of a pre-deploy open or
    # human-closed real alert, so the first cold reading neither resolves the
    # open one nor re-pages the closed one. Seeded machines start at
    # (epoch 0, episode 0); the seed alert joins episode 0.
    has_feedback = table_exists(conn, "alert_feedback")
    now = datetime.now(timezone.utc).isoformat()
    machines = [row[0] for row in conn.execute(
        f"SELECT DISTINCT machine_id FROM alerts WHERE {_SEED_SOURCE_SQL} ORDER BY machine_id",
    )]
    for machine_id in machines:
        seed = _seed_alert(conn, machine_id, has_feedback)
        if seed is None:
            continue
        inserted = conn.execute(
            "INSERT OR IGNORE INTO machine_health_state (machine_id, epoch, episode, max_state, "
            "model_version, epoch_started_at, episode_started_at, reset_reason, updated_at) "
            "VALUES (?, 0, 0, ?, NULL, NULL, ?, 'migration_008_seed', ?)",
            (machine_id, seed[1], seed[2], now),
        ).rowcount
        if inserted:
            conn.execute("UPDATE alerts SET health_episode = 0 WHERE id = ?", (seed[0],))


# Ordered list of (target_version, up_callable). Append new migrations here.
MIGRATIONS = [
    (1, _migration_001_canonical_baseline),
    (2, _migration_002_live_telemetry),
    (3, _migration_003_device_health),
    (4, _migration_004_work_orders),
    (5, _migration_005_feedback),
    (6, _migration_006_alert_explanations),
    (7, _migration_007_push),
    (8, _migration_008_health_epoch),
]


def run_migrations(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER NOT NULL, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.commit()
    version = current_version(conn)
    for target, step in MIGRATIONS:
        if target <= version:
            continue
        try:
            step(conn)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        version = target
    return version


LATEST_VERSION = MIGRATIONS[-1][0]

# DB files already known to be at LATEST_VERSION, so ensure_current_schema
# costs one PRAGMA per call instead of a version query.
_current_files: set[str] = set()
_ensure_lock = threading.Lock()


def _db_file(conn: sqlite3.Connection) -> str:
    """The main database's file path ('' for an in-memory DB)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None:
        return ""
    return row[2] or ""


def ensure_current_schema(conn: sqlite3.Connection) -> int:
    """Bring a versioned but stale DB to LATEST_VERSION; return its version.

    A file below version 1 (empty or legacy IMS) is returned untouched — see
    the module docstring. Runs the migrations under a process lock with a
    double check, so concurrent first requests stamp each version once, and
    remembers files that are current so later calls skip the version query.
    """
    path = _db_file(conn)
    if path and path in _current_files:
        return LATEST_VERSION
    previous = conn.row_factory
    conn.row_factory = sqlite3.Row  # current_version reads row["v"]
    try:
        version = current_version(conn)
        if 1 <= version < LATEST_VERSION:
            with _ensure_lock:
                version = current_version(conn)
                if 1 <= version < LATEST_VERSION:
                    logger.info("Upgrading database schema from v%s to v%s", version, LATEST_VERSION)
                    version = run_migrations(conn)
    finally:
        conn.row_factory = previous
    if path and version >= LATEST_VERSION:
        _current_files.add(path)
    return version


def _reset_current_schema_memo() -> None:
    """Tests only: forget which files are known to be current."""
    _current_files.clear()
