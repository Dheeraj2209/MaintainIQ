"""The DB authority for a machine's health epoch, alert episode and held level
(docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md).

machine_health_state (migration 008) holds one row per machine:
- epoch: the baseline life. Bumped only by a repair reset (_reset_locked),
  which restarts commissioning.
- episode: the alert episode. Bumped by every reset and every false-alarm
  re-arm (_rearm_locked). Monotonic.
- max_state: the held (ratcheted) level of the current episode. Only
  record_level raises it, and only for a result of the current episode.
- model_version: the artifact that derived max_state (plan D10).

No row means (epoch 0, episode 0, 'healthy'). Every write here is one UPSERT
statement, so two connections racing on a machine without a row never hit
an IntegrityError, and a stale-episode write is a no-op inside SQLite rather
than a read-then-write in Python.

Resets and re-arms also touch alerts, so they run under
live._TRANSITION_LOCK, inside the caller's transaction (the _locked
functions do not commit). reset_machine_health is the self-contained
wrapper. The broadcast of the alerts a reset resolved (announce_resolved)
runs after the commit and outside the lock.

Every predictor call goes through predict_synced, which syncs the in-memory
predictor to this row and predicts under one per-machine lock (plan D4, D5,
D10). Lock order: _SYNC_LOCKS[machine] -> live._TRANSITION_LOCK.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from src.alerts import live
from src.storage.db import table_exists

logger = logging.getLogger(__name__)

HEALTH_RANK = {"healthy": 0, "degrading": 1, "faulty": 2, "critical": 3}
# Feedback that re-arms the episode instead of keeping the held level (D12).
REARM_OUTCOMES = {"false_alarm"}
REARM_CAUSES = {"sensor_or_data_quality_issue"}

_TABLE = "machine_health_state"


@dataclass(frozen=True)
class HealthRow:
    epoch: int
    episode: int
    max_state: str
    model_version: str | None
    epoch_started_at: str | None


_DEFAULT = HealthRow(0, 0, "healthy", None, None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rank_sql(column: str) -> str:
    return (f"CASE {column} WHEN 'critical' THEN 3 WHEN 'faulty' THEN 2 "
            f"WHEN 'degrading' THEN 1 ELSE 0 END")


def _stored(conn, machine_id: str) -> HealthRow | None:
    """machine_id's row, or None when it has none."""
    row = conn.execute(
        f"SELECT epoch, episode, max_state, model_version, epoch_started_at "
        f"FROM {_TABLE} WHERE machine_id = ?",
        (machine_id,),
    ).fetchone()
    return HealthRow(*row) if row is not None else None


def current(conn, machine_id: str) -> HealthRow | None:
    """machine_id's health row: the (0, 0, 'healthy') default when it has
    none, None when the table is missing (an old DB opened outside get_db)."""
    if not table_exists(conn, _TABLE):
        return None
    return _stored(conn, machine_id) or _DEFAULT


def record_level(conn, machine_id: str, episode: int, state: str, model_version) -> None:
    """Raise the held level to `state` if the machine is still at `episode`
    and `state` is worse. One statement: a stale episode or a lower state
    leaves the row alone. With no row (episode 0) episode 0 inserts it and
    any other episode does nothing. Does not commit."""
    if state not in HEALTH_RANK:
        raise ValueError(f"unknown health state {state!r}")
    now = _now()
    # INSERT ... SELECT so the no-row case inserts only at episode 0; the
    # EXISTS arm makes an existing row reach the conditional DO UPDATE.
    conn.execute(
        f"""INSERT INTO {_TABLE} (machine_id, epoch, episode, max_state, model_version, updated_at)
            SELECT :m, 0, 0, :state, :model, :now
            WHERE :episode = 0 OR EXISTS (SELECT 1 FROM {_TABLE} WHERE machine_id = :m)
            ON CONFLICT(machine_id) DO UPDATE SET
              max_state = CASE WHEN {_TABLE}.episode = :episode
                                AND {_rank_sql('excluded.max_state')} > {_rank_sql(f'{_TABLE}.max_state')}
                               THEN excluded.max_state ELSE {_TABLE}.max_state END,
              model_version = CASE WHEN {_TABLE}.episode = :episode
                                   THEN COALESCE(excluded.model_version, {_TABLE}.model_version)
                                   ELSE {_TABLE}.model_version END,
              updated_at = CASE WHEN {_TABLE}.episode = :episode
                                THEN excluded.updated_at ELSE {_TABLE}.updated_at END""",
        {"m": machine_id, "state": state, "model": model_version, "now": now, "episode": episode},
    )


def _resolved_alerts(conn, machine_id: str, now: str) -> list[dict]:
    """System-resolve machine_id's open real alert(s) at `now` (closed_by
    NULL: nobody closed it) and return them in the _ALERT_COLUMNS shape."""
    ids = [row[0] for row in conn.execute(
        f"""SELECT id FROM alerts WHERE machine_id = ? AND status = 'open'
              AND COALESCE(source, '') != '{live.DEMO_SOURCE}' ORDER BY id""",
        (machine_id,),
    )]
    resolved = []
    for alert_id in ids:
        conn.execute(
            "UPDATE alerts SET status = 'resolved', resolved_at = ?, closed_by = NULL "
            "WHERE id = ? AND status = 'open'",
            (now, alert_id),
        )
        alert = live.get_alert(conn, alert_id)
        if alert is not None:
            resolved.append(alert)
    return resolved


def _reset_locked(conn, machine_id: str, *, reason: str, now: str) -> tuple[HealthRow, list[dict]]:
    """A repair reset: epoch+1, episode+1, held level 'healthy', and the open
    real alert system-resolved at `now` (wall clock, never the maintenance
    performed_at). The caller holds live._TRANSITION_LOCK and commits."""
    conn.execute(
        f"""INSERT INTO {_TABLE} (machine_id, epoch, episode, max_state, epoch_started_at,
                                  episode_started_at, reset_reason, updated_at)
            VALUES (:m, 1, 1, 'healthy', :now, :now, :reason, :now)
            ON CONFLICT(machine_id) DO UPDATE SET
              epoch = {_TABLE}.epoch + 1, episode = {_TABLE}.episode + 1,
              max_state = 'healthy', epoch_started_at = excluded.epoch_started_at,
              episode_started_at = excluded.episode_started_at,
              reset_reason = excluded.reset_reason, updated_at = excluded.updated_at""",
        {"m": machine_id, "now": now, "reason": reason},
    )
    row = _stored(conn, machine_id)
    return row, _resolved_alerts(conn, machine_id, now)


def _rearm_locked(conn, machine_id: str, *, now: str) -> HealthRow:
    """A false-alarm re-arm (D12): episode+1 and held level 'healthy'; the
    epoch, baseline and commissioning are untouched. The caller holds
    live._TRANSITION_LOCK and commits."""
    conn.execute(
        f"""INSERT INTO {_TABLE} (machine_id, epoch, episode, max_state, episode_started_at, updated_at)
            VALUES (:m, 0, 1, 'healthy', :now, :now)
            ON CONFLICT(machine_id) DO UPDATE SET
              episode = {_TABLE}.episode + 1, max_state = 'healthy',
              episode_started_at = excluded.episode_started_at,
              updated_at = excluded.updated_at""",
        {"m": machine_id, "now": now},
    )
    return _stored(conn, machine_id)


def reset_machine_health(conn, machine_id: str, *, reason: str,
                         now: str | None = None) -> tuple[HealthRow | None, list[dict]]:
    """_reset_locked under live._TRANSITION_LOCK, committed (or rolled back
    on failure). Call announce_resolved with the returned alerts afterwards.
    Without the table (an old DB opened outside get_db) it is a no-op
    returning (None, [])."""
    if not table_exists(conn, _TABLE):
        return None, []
    now = now or _now()
    with live._TRANSITION_LOCK:
        try:
            result = _reset_locked(conn, machine_id, reason=reason, now=now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return result


def announce_resolved(conn, alerts, *, at: str) -> None:
    """Broadcast each alert a reset resolved, after the commit. A failure is
    logged and never undoes the committed reset."""
    from src.prediction import pipeline

    for alert in alerts:
        try:
            pipeline.fan_out(conn, ("alert_resolved", alert), at=at)
        except Exception:
            logger.exception("Failed to announce the reset-resolved alert %s", alert.get("id"))


def effective_state(conn, machine_id: str, latest_pred: dict | None) -> dict:
    """The machine's current state for readers of the latest prediction (D6).

    After a reset or re-arm with no new reading, the latest prediction is
    from an older episode (or pre-ratchet, NULL, while the machine has a
    row): the current state is then the held level, flagged
    reset_pending_reading. Otherwise it is the prediction's own state. A
    demo prediction (/demo/simulate-fault) carries no episode and is never
    pending: it shows its own state, so fault injection stays visible."""
    pred_state = latest_pred.get("health_state") if latest_pred else None
    pred_episode = latest_pred.get("health_episode") if latest_pred else None
    if not table_exists(conn, _TABLE):
        return {"health_state": pred_state, "reset_pending_reading": False,
                "health_episode": pred_episode}
    stored = _stored(conn, machine_id)
    row = stored or _DEFAULT
    is_demo = bool(latest_pred) and latest_pred.get("source") == live.DEMO_SOURCE
    if is_demo:
        pending = False
    elif pred_episode is None:  # no prediction yet, or a pre-ratchet one
        # Every reset or re-arm bumps the episode above 0, so episode 0 means
        # nothing was reset (e.g. a migration 008 seed): the held level is the
        # state, but no reading is awaited.
        pending = stored is not None and row.episode > 0
    else:
        pending = pred_episode < row.episode
    held_without_episode = not is_demo and pred_episode is None and stored is not None
    return {
        "health_state": row.max_state if pending or held_without_episode else pred_state,
        "reset_pending_reading": pending,
        "health_episode": row.episode,
    }


# One lock per machine, held across sync and predict (D4): the only in-memory
# resets and restores happen inside _prepare_locked, under it.
_SYNC_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def predict_synced(predictor, conn, machine_id: str, call):
    """Sync `predictor` to machine_id's DB health row, then run `call()` (the
    prediction), atomically per machine. A predictor without restore_health
    (a test fake) is just called. The result can still raise StaleEpoch if
    an in-memory reset slips in anyway; the caller logs it and skips persist."""
    if not hasattr(predictor, "restore_health"):
        return call()
    with _SYNC_LOCKS[machine_id]:
        _prepare_locked(predictor, conn, machine_id)
        return call()


def _prepare_locked(predictor, conn, machine_id: str) -> None:
    """Bring the predictor to the DB epoch, episode and held level.

    - A model change re-derives the held level (D10).
    - A NULL model_version (a seed) adopts the current model, keeping the level.
    - Same epoch, other episode (a re-arm): restore the held level only.
    - Other epoch (a restart or a reset): reset, rehydrate the epoch
      (bounded, D5) and restore. A failed rehydrate is logged and the epoch
      starts from empty history, still synced, so it is not retried.
    """
    from src.prediction import rul_store

    row = current(conn, machine_id)
    if row is None:
        return
    model = predictor.artifact["model_version"]
    if row.model_version not in (None, model):
        _rederive_for_model(predictor, conn, machine_id, row, model)
        return
    if row.model_version is None and row != _DEFAULT:
        _adopt_model(conn, machine_id, row.episode, model)
    if predictor.epoch_of(machine_id) == row.epoch:
        if predictor.episode_of(machine_id) != row.episode:
            predictor.restore_health(machine_id, epoch=row.epoch, episode=row.episode,
                                     max_state=row.max_state)
        return
    predictor._reset_state(machine_id)
    try:
        rul_store.rehydrate(predictor, conn, machine_id, epoch=row.epoch)
    except Exception:
        logger.exception("rehydrate failed for %s; starting the epoch from empty history", machine_id)
        predictor._reset_state(machine_id)
    predictor.restore_health(machine_id, epoch=row.epoch, episode=row.episode,
                             max_state=row.max_state)


def _adopt_model(conn, machine_id: str, episode: int, model: str) -> None:
    """Stamp a seed row (model_version NULL) with the current model and keep
    its held level (D10). Conditional on the episode; commits."""
    with live._TRANSITION_LOCK:
        try:
            conn.execute(
                f"UPDATE {_TABLE} SET model_version = ?, updated_at = ? "
                f"WHERE machine_id = ? AND episode = ? AND model_version IS NULL",
                (model, _now(), machine_id, episode),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _rederive_for_model(predictor, conn, machine_id: str, row: HealthRow, model: str) -> None:
    """The held level was derived by another model (D10). Replay the whole
    epoch once, take the level the current model latches over the current
    episode's rows, floor it at the open real alert's state (a model change
    never silently resolves an alert), write it with the new model_version
    (conditional on the episode; commits) and restore the predictor to it."""
    from src.prediction import rul_store

    levels = []

    def collect(stored, result):
        if stored["health_episode"] == row.episode and rul_store._latches(result):
            levels.append(result["instant_health_state"])

    predictor._reset_state(machine_id)
    try:
        rul_store.rehydrate(predictor, conn, machine_id, epoch=row.epoch, full=True,
                            on_result=collect)
    except Exception:
        # Cannot re-derive: keep the stored level rather than drop it.
        logger.exception("re-derive failed for %s; keeping the held level %s",
                         machine_id, row.max_state)
        predictor._reset_state(machine_id)
        levels = [row.max_state]
    open_states = conn.execute(
        f"""SELECT health_state FROM alerts WHERE machine_id = ? AND status = 'open'
              AND COALESCE(source, '') != '{live.DEMO_SOURCE}'""",
        (machine_id,),
    ).fetchall()
    levels += [r[0] for r in open_states if r[0] in HEALTH_RANK]
    level = max(levels, key=HEALTH_RANK.__getitem__, default="healthy")
    with live._TRANSITION_LOCK:
        try:
            written = conn.execute(
                f"UPDATE {_TABLE} SET max_state = ?, model_version = ?, updated_at = ? "
                f"WHERE machine_id = ? AND episode = ?",
                (level, model, _now(), machine_id, row.episode),
            ).rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if not written:
        # A reset or re-arm moved the episode meanwhile: sync to the new row.
        _prepare_locked(predictor, conn, machine_id)
        return
    predictor.restore_health(machine_id, epoch=row.epoch, episode=row.episode, max_state=level)
