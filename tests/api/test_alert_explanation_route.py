"""GET /api/alerts/{id}/explanation
(design/2026-10-07-alert-explanation-design.md, "API").

Seed: alert 1 (resolved, low) and alert 2 (open, high) on m1, both
bearing_wear and neither with a snapshot, so both reconstruct.
"""
import json
import sqlite3

import pytest

from src.api.schemas import AlertExplanation


@pytest.fixture(autouse=True)
def _no_predictor(monkeypatch):
    from src.root_cause import explain

    monkeypatch.setattr(explain, "loaded_predictor", lambda: None)


def test_unknown_alert_is_404(client):
    resp = client.get("/api/alerts/999/explanation")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "unknown alert: 999"}


def test_anonymous_gets_401(anon_client):
    assert anon_client.get("/api/alerts/2/explanation").status_code == 401


@pytest.mark.parametrize("role", ["admin", "supervisor", "operator"])
def test_every_role_may_read(auth_client, role):
    assert auth_client(role).get("/api/alerts/2/explanation").status_code == 200


def test_legacy_alert_is_reconstructed_with_similar_incidents(client):
    body = client.get("/api/alerts/2/explanation").json()
    assert body["source"] == "reconstructed" and body["alert"]["id"] == 2
    assert body["synthetic"] is False
    assert body["probable_cause"]["display"] == "Probable cause: bearing wear"
    similar = {i["alert_id"]: i for i in body["similar_incidents"]}
    assert 1 in similar
    assert {"same_probable_cause", "same_machine"} <= set(similar[1]["match_reasons"])
    assert body["triggering_readings"]["locate"] == "nearest_timestamp"

    earlier = client.get("/api/alerts/1/explanation").json()
    assert 2 not in [i["alert_id"] for i in earlier["similar_incidents"]]


def test_snapshot_rows_are_served(client, db_path):
    body = {"explanation_version": 1, "synthetic": False, "triggering_readings": None,
            "key_factors": None, "prediction": None, "probable_cause": None, "notes": ["captured"]}
    conn = sqlite3.connect(db_path)
    try:
        for kind, at in (("created", "2026-10-07T13:05:11+00:00"),
                         ("escalated", "2026-10-07T14:20:03+00:00")):
            conn.execute("INSERT INTO alert_explanations (alert_id, kind, created_at, explanation_json) "
                         "VALUES (2, ?, ?, ?)", (kind, at, json.dumps(body)))
        conn.commit()
    finally:
        conn.close()
    resp = client.get("/api/alerts/2/explanation").json()
    assert resp["source"] == "snapshot" and resp["snapshot_kind"] == "escalated"
    assert [s["kind"] for s in resp["snapshots"]] == ["created", "escalated"]
    assert resp["snapshot_at"] == "2026-10-07T14:20:03+00:00"
    assert "captured" in resp["notes"]


def test_similar_limit_bounds(client):
    assert client.get("/api/alerts/2/explanation?similar_limit=0").json()["similar_incidents"] == []
    assert client.get("/api/alerts/2/explanation?similar_limit=21").status_code == 422
    assert client.get("/api/alerts/2/explanation?similar_limit=-1").status_code == 422
    assert client.get("/api/alerts/abc/explanation").status_code == 422


def test_response_validates_and_list_shape_is_unchanged(client):
    AlertExplanation.model_validate(client.get("/api/alerts/2/explanation").json())
    alerts = client.get("/api/alerts").json()
    assert alerts and all("explanation" not in a for a in alerts)
