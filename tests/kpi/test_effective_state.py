"""Current-state readers honour health resets (plan Task 11b, D6) and the
abnormal-event count uses the instant state (review R2-7)."""
import json
import sqlite3

import pytest

from src.kpi import calculations as kpi
from src.prediction import health_epoch
from src.reports import generators
from src.storage.db import init_schema


@pytest.fixture
def hconn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute(
        "INSERT INTO machines (machine_id, dataset, is_documented_failure) "
        "VALUES ('m1', 'xjtu_sy', 1)"
    )
    conn.commit()
    yield conn
    conn.close()


def _pred(conn, *, n, state, instant=None, episode=0, epoch=0, warnings=None, rul=30.0):
    conn.execute(
        """INSERT INTO predictions
               (machine_id, timestamp, health_state, source, predicted_rul_minutes,
                rul_estimate_kind, model_version, health_epoch, health_episode,
                instant_health_state, warnings_json)
           VALUES ('m1', ?, ?, 'xjtu_rul', ?, 'point_estimate', 'v1', ?, ?, ?, ?)""",
        (f"2030-01-01T00:{n:02d}:00+00:00", state, rul, epoch, episode,
         instant if instant is not None else state, json.dumps(warnings or [])),
    )
    conn.commit()


def _level(conn, state):
    health_epoch.record_level(conn, "m1", 0, state, "v1")
    conn.commit()


def test_machine_health_after_reset_without_a_new_reading_is_healthy(hconn):
    _pred(hconn, n=1, state="critical")
    _level(hconn, "critical")
    health_epoch.reset_machine_health(hconn, "m1", reason="test")

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_state"] == "healthy"
    assert health["reset_pending_reading"] is True
    assert health["health_state_held"] is False
    assert health["instant_health_state"] is None
    assert health["risk_score"] == 0.0
    # The last prediction scored the replaced component: none of its
    # prediction-derived fields describe the machine now (it showed the old
    # bearing's "1.6 min left" on a freshly repaired machine).
    assert health["predicted_rul_minutes"] is None
    assert health["rul_estimate_kind"] is None
    assert health["confidence"] is None
    assert health["probable_cause"] is None
    assert health["out_of_distribution"] is None


def test_machine_health_flags_a_held_row(hconn):
    _pred(hconn, n=1, state="critical", instant="healthy",
          warnings=["condition_receded: instant state healthy; holding critical"])
    _level(hconn, "critical")

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_state"] == "critical"
    assert health["instant_health_state"] == "healthy"
    assert health["health_state_held"] is True
    assert health["reset_pending_reading"] is False
    assert health["commissioning"] is None


def test_machine_health_reports_commissioning(hconn):
    _pred(hconn, n=1, state="healthy", instant="faulty",
          warnings=["commissioning: 7/20 snapshots; learning the baseline, "
                    "instant state faulty is not held"])

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["commissioning"] == {"seen": 7, "of": 20}
    assert health["instant_health_state"] == "faulty"


def test_machine_health_legacy_prediction_is_unchanged(hconn):
    hconn.execute(
        "INSERT INTO predictions (machine_id, timestamp, health_state, source) "
        "VALUES ('m1', '2030-01-01T00:00:00+00:00', 'faulty', 'xjtu_rul')"
    )
    hconn.commit()

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_state"] == "faulty"
    assert health["health_state_held"] is False
    assert health["reset_pending_reading"] is False
    assert health["abnormal_event_count"] == 1


def test_abnormal_event_count_uses_the_instant_state(hconn):
    _pred(hconn, n=0, state="critical", instant="critical")
    for n in range(1, 11):
        _pred(hconn, n=n, state="critical", instant="healthy")

    assert kpi.machine_health_kpis(hconn, "m1")[0]["abnormal_event_count"] == 1


def test_open_ended_prognostic_report_uses_the_effective_state(hconn):
    _pred(hconn, n=1, state="critical")
    _level(hconn, "critical")
    health_epoch.reset_machine_health(hconn, "m1", reason="test")

    report = generators.machine_prognostic(hconn, scope="m1")
    assert report["current"]["health_state"] == "healthy"
    assert report["current"]["reset_pending_reading"] is True
    # The replaced component's RUL must not drive a "service within N
    # minutes" recommendation for the repaired machine.
    assert report["current"]["predicted_rul_minutes"] is None
    assert report["current"]["rul_estimate_kind"] is None
    assert report["recommended_maintenance"] == {"within_minutes": None, "by_timestamp": None}


def test_bounded_prognostic_report_describes_the_past(hconn):
    _pred(hconn, n=1, state="critical")
    _level(hconn, "critical")
    health_epoch.reset_machine_health(hconn, "m1", reason="test")

    report = generators.machine_prognostic(
        hconn, scope="m1", period_end="2030-12-31T00:00:00+00:00")
    assert report["current"]["health_state"] == "critical"


def test_open_ended_fleet_summary_uses_the_effective_state(hconn):
    _pred(hconn, n=1, state="critical")
    _level(hconn, "critical")
    health_epoch.reset_machine_health(hconn, "m1", reason="test")

    summary = generators.fleet_summary(hconn)
    assert summary["health_distribution"] == {"healthy": 1}
    assert summary["top_at_risk"][0]["health_state"] == "healthy"
    assert summary["top_at_risk"][0]["predicted_rul_minutes"] is None

    bounded = generators.fleet_summary(hconn, period_end="2030-12-31T00:00:00+00:00")
    assert bounded["health_distribution"] == {"critical": 1}
    assert bounded["top_at_risk"][0]["predicted_rul_minutes"] == 30.0


def test_held_since_is_when_the_held_level_was_first_reached(hconn):
    _pred(hconn, n=1, state="healthy")
    _pred(hconn, n=2, state="critical", instant="critical")
    _pred(hconn, n=3, state="critical", instant="healthy")
    _level(hconn, "critical")

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_state_held"] is True
    assert health["held_since"] == "2030-01-01T00:02:00+00:00"


def test_held_since_is_none_when_not_held(hconn):
    _pred(hconn, n=1, state="critical", instant="critical")

    assert kpi.machine_health_kpis(hconn, "m1")[0]["held_since"] is None


def test_health_warnings_keep_only_the_ratchet_warnings(hconn):
    _pred(hconn, n=1, state="critical", instant="healthy", warnings=[
        "condition_receded: instant state healthy; holding critical",
        "ood_not_latched: instant state faulty on out-of-distribution input",
        "something else entirely",
    ])

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_warnings"] == [
        "condition_receded: instant state healthy; holding critical",
        "ood_not_latched: instant state faulty on out-of-distribution input",
    ]


def test_machine_summary_serializes_the_health_fields(hconn):
    from src.api.schemas import MachineSummary

    _pred(hconn, n=1, state="healthy", instant="faulty",
          warnings=["commissioning: 7/20 snapshots; learning the baseline"])
    dumped = MachineSummary(**kpi.machine_health_kpis(hconn, "m1")[0]).model_dump()
    assert dumped["commissioning"] == {"seen": 7, "of": 20}
    assert dumped["instant_health_state"] == "faulty"
    assert dumped["health_state_held"] is True
    assert dumped["reset_pending_reading"] is False
    assert dumped["held_since"] is not None
    assert dumped["health_warnings"] == ["commissioning: 7/20 snapshots; learning the baseline"]


def _demo_pred(conn, *, n, state):
    # What /demo/simulate-fault writes (prediction.live.evaluate_new_reading).
    conn.execute(
        "INSERT INTO predictions (machine_id, timestamp, health_state, source, model_name) "
        "VALUES ('m1', ?, ?, 'demo', 'demo_simulator')",
        (f"2030-01-01T00:{n:02d}:00+00:00", state),
    )
    conn.commit()


def test_a_demo_prediction_shows_its_own_state_on_a_machine_with_a_health_row(hconn):
    _pred(hconn, n=1, state="healthy")
    _level(hconn, "healthy")
    _demo_pred(hconn, n=2, state="critical")

    health = kpi.machine_health_kpis(hconn, "m1")[0]
    assert health["health_state"] == "critical"
    assert health["reset_pending_reading"] is False

    report = generators.machine_prognostic(hconn, scope="m1")
    assert report["current"]["health_state"] == "critical"
    assert report["current"]["reset_pending_reading"] is False
    assert generators.fleet_summary(hconn)["health_distribution"] == {"critical": 1}
