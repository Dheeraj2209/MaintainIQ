"""predict_synced holds one per-machine lock across sync and predict, so a
reset can never land inside a prediction (plan D4, D6, Task 6).

The classifier of predictor P blocks on an event between the predictor's
first and second locked sections, so a test can act while a prediction is in
flight.
"""
import sqlite3
import threading

import numpy as np
import pytest

from src.prediction import health_epoch, rul_store
from src.prediction.rul_realtime import StaleEpoch
from tests.prediction.test_health_epoch import M, _make, _synced


class _Gated:
    """Late life on every row, but the gated call waits for `release`."""

    def __init__(self):
        self.gate = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def predict_proba(self, X):
        if self.gate:
            self.gate = False
            self.entered.set()
            assert self.release.wait(10)
        return np.tile([0.0, 1.0], (len(X), 1))


def _conn(db_path):
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def gated(tmp_path, conn):
    """A predictor past commissioning at (epoch 0, episode 0)."""
    classifier = _Gated()
    predictor = _make(tmp_path)
    predictor.classifiers = [classifier]
    for state in ["healthy", "healthy", "healthy"]:
        rul_store.persist_prediction(conn, _synced(predictor, conn, state))
    return predictor, classifier


def _run(fn):
    out = {}

    def target():
        try:
            out["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            out["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, out


def test_reset_from_another_connection_waits_and_the_stale_result_is_dropped(db_path, gated):
    predictor, classifier = gated
    conn_a, conn_b = _conn(db_path), _conn(db_path)
    try:
        classifier.gate = True
        a, out_a = _run(lambda: _synced(predictor, conn_a, "critical"))
        assert classifier.entered.wait(10)

        health_epoch.reset_machine_health(conn_b, M, reason="test")
        b, out_b = _run(lambda: _synced(predictor, conn_b, "healthy"))
        b.join(0.3)
        assert b.is_alive()  # B waits on the machine's sync lock until A is done

        classifier.release.set()
        a.join(10)
        b.join(10)
        assert "error" not in out_a and "error" not in out_b

        result_a = out_a["result"]
        assert result_a["health_state"] == "critical"
        assert result_a["health_episode"] == 0
        before = conn_a.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        assert rul_store.persist_prediction(conn_a, result_a) is None
        assert conn_a.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == before
        assert conn_a.execute(
            "SELECT COUNT(*) FROM alerts WHERE machine_id = ? AND status = 'open'", (M,)
        ).fetchone()[0] == 0
        assert health_epoch.current(conn_a, M).max_state == "healthy"

        result_b = out_b["result"]
        assert result_b["history_snapshots"] == 1
        assert (result_b["health_epoch"], result_b["health_episode"]) == (1, 1)
    finally:
        conn_a.close()
        conn_b.close()


def test_in_memory_reset_mid_prediction_raises_stale_epoch(db_path, gated):
    predictor, classifier = gated
    conn_a = _conn(db_path)
    try:
        classifier.gate = True
        a, out_a = _run(lambda: _synced(predictor, conn_a, "critical"))
        assert classifier.entered.wait(10)

        predictor._reset_state(M)
        classifier.release.set()
        a.join(10)

        assert isinstance(out_a.get("error"), StaleEpoch)
        assert M not in predictor._probability_history
        assert M not in predictor._warning_history
        assert M not in predictor._max_state
    finally:
        conn_a.close()

