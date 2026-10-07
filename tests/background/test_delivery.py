"""NotificationWorker: pages are delivered off the scheduler tick."""
import sqlite3

from src.background.delivery import NotificationWorker


class Conn:
    def __init__(self):
        self.closed = False
        self.row_factory = None

    def close(self):
        self.closed = True


def test_runs_tasks_in_order_each_on_its_own_closed_connection():
    conns, seen = [], []

    def factory():
        conns.append(Conn())
        return conns[-1]

    worker = NotificationWorker(connection_factory=factory)
    try:
        for i in range(3):
            worker.submit(lambda c, i=i: seen.append((i, c)), f"task {i}")
        assert worker.join(5)
    finally:
        worker.stop()
    assert [i for i, _ in seen] == [0, 1, 2]
    assert len({id(c) for _, c in seen}) == 3
    assert all(c.closed for c in conns)
    assert all(c.row_factory is sqlite3.Row for c in conns)


def test_a_failing_task_or_factory_does_not_stop_the_worker(caplog):
    seen = []
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return Conn()

    worker = NotificationWorker(connection_factory=factory)
    try:
        worker.submit(lambda c: seen.append("never"), "first")
        worker.submit(lambda c: 1 / 0, "second")
        worker.submit(lambda c: seen.append("third"), "third")
        assert worker.join(5)
    finally:
        worker.stop()
    assert seen == ["third"]
    assert "could not open a DB connection" in caplog.text
    assert "Delivery of second failed" in caplog.text


def test_stop_drains_the_queue_then_drops_later_submits(caplog):
    seen = []
    worker = NotificationWorker(connection_factory=Conn)
    worker.submit(lambda c: seen.append(1), "queued")
    worker.stop(timeout=5)
    worker.submit(lambda c: seen.append(2), "late")
    worker.stop()  # idempotent
    assert seen == [1]
    assert "dropping late" in caplog.text


def test_unused_worker_starts_no_thread():
    worker = NotificationWorker(connection_factory=Conn)
    assert worker.join(0.1)
    worker.stop()
