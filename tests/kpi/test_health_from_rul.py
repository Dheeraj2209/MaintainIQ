from src.kpi import calculations as kpi


def _insert_rul_prediction(conn, machine_id, **cols):
    defaults = {
        "timestamp": "2030-01-01T00:00:00+00:00",  # newest, so it is the latest
        "health_state": "faulty",
        "source": "xjtu_rul",
        "predicted_rul_minutes": 45.0,
        "rul_estimate_kind": "point_estimate",
        "failure_within_horizon_probability": 0.9,
        "out_of_distribution": 1,
    }
    defaults.update(cols)
    conn.execute(
        """INSERT INTO predictions
               (machine_id, timestamp, health_state, source, predicted_rul_minutes,
                rul_estimate_kind, failure_within_horizon_probability, out_of_distribution)
           VALUES (:machine_id, :timestamp, :health_state, :source, :predicted_rul_minutes,
                   :rul_estimate_kind, :failure_within_horizon_probability, :out_of_distribution)""",
        {"machine_id": machine_id, **defaults},
    )
    conn.commit()


def test_machine_health_surfaces_rul_fields(conn):
    _insert_rul_prediction(conn, "m1")
    health = kpi.machine_health_kpis(conn, "m1")[0]
    assert health["health_state"] == "faulty"
    assert health["predicted_rul_minutes"] == 45.0
    assert health["rul_estimate_kind"] == "point_estimate"
    assert health["out_of_distribution"] is True
    assert health["prediction_source"] == "xjtu_rul"


def test_machine_health_rul_fields_default_when_absent(conn):
    # m2 in the seed has only legacy predictions (no RUL columns set).
    health = kpi.machine_health_kpis(conn, "m2")[0]
    assert health["predicted_rul_minutes"] is None
    assert health["rul_estimate_kind"] is None
    # out_of_distribution defaults to 0 in the schema; surfaced as False.
    assert health["out_of_distribution"] in (False, None)


def test_machine_summary_endpoint_includes_rul_fields(client, conn):
    _insert_rul_prediction(conn, "m1")
    resp = client.get("/api/kpis/m1")
    assert resp.status_code == 200
    body = resp.json()["machine_health"]
    assert body["predicted_rul_minutes"] == 45.0
    assert body["rul_estimate_kind"] == "point_estimate"
    assert body["out_of_distribution"] is True
