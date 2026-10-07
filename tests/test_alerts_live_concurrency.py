"""src/alerts/live.apply_reading is called concurrently by the live MQTT
ingest worker, replay threads and the /demo route, each on its own SQLite
connection. Its read-then-insert must be serialised, or two callers that both
see "no open alert" each insert one (M6 review regression)."""
import sqlite3
import threading
import time

from src.alerts import live


def _conn(db_path):
    c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def test_concurrent_abnormal_readings_open_exactly_one_alert(db_path, monkeypatch):
    real_open_alert = live._open_alert

    def slow_open_alert(conn, machine_id, **kind):
        found = real_open_alert(conn, machine_id, **kind)
        time.sleep(0.1)  # widen the read->insert window both threads would race in
        return found

    monkeypatch.setattr(live, "_open_alert", slow_open_alert)
    setup = _conn(db_path)
    setup.execute("UPDATE alerts SET status = 'resolved' WHERE machine_id = 'm2'")
    setup.commit()

    results = []
    barrier = threading.Barrier(2)

    def worker(source):
        conn = _conn(db_path)
        try:
            barrier.wait()
            results.append(live.apply_reading(conn, "m2", "faulty", None, source, "2026-10-06T12:00:00Z"))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(s,)) for s in ("mqtt", "xjtu_rul")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    open_count = setup.execute(
        "SELECT COUNT(*) FROM alerts WHERE machine_id = 'm2' AND status = 'open'").fetchone()[0]
    setup.close()
    assert open_count == 1
    events = [r[0] if r else None for r in results]
    assert events.count("alert_created") == 1 and events.count(None) == 1



def test_alert_columns_include_ack_and_paging(conn):
    conn.execute("UPDATE alerts SET page_level = 1, last_paged_at = 'x' WHERE id = 2")
    conn.commit()
    found = live._open_alert(conn, "m1", demo=False)
    assert found["page_level"] == 1 and found["last_paged_at"] == "x"
    assert "acknowledged_at" in found and "acknowledged_by" in found


def test_created_alert_dict_matches_api_shape(conn):
    event, alert = live.apply_reading(conn, "m2", "faulty", None, "ml", "2026-10-07T12:00:00Z")
    assert event == "alert_created"
    assert alert["page_level"] == 0
    assert alert["last_paged_at"] is None
    assert alert["acknowledged_at"] is None and alert["acknowledged_by"] is None


def test_noop_acknowledge_does_not_hold_the_write_lock(db_path):
    """A repeat (or unknown-id) acknowledge matches 0 rows, but the UPDATE
    still opens an implicit transaction and takes SQLite's RESERVED lock.
    acknowledge_alert must end it, or every other writer (replay, MQTT ingest,
    the paging tick) stalls until the request connection is closed."""
    conn = _conn(db_path)
    other = sqlite3.connect(db_path, timeout=0.2)
    try:
        _, changed = live.acknowledge_alert(conn, 2, 1)
        assert changed
        for alert_id in (2, 9999):  # already acknowledged; unknown id
            _, changed = live.acknowledge_alert(conn, alert_id, 2)
            assert not changed
            assert not conn.in_transaction
            other.execute("UPDATE alerts SET message = message WHERE id = 1")
            other.commit()
    finally:
        other.close()
        conn.close()
