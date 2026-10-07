"""Mutating alert / work-order routes must never block the event loop.

Their service calls write SQLite (which can wait out the 5 s busy timeout while
another connection holds the write lock) and take live._TRANSITION_LOCK. Run on
the loop thread, one slow writer anywhere froze every websocket and request.
The routes now run the service call through asyncio.to_thread; these tests hold
the write lock from another connection and check a ticker on the loop keeps
running while the route waits.
"""
import asyncio
import sqlite3
import threading
import time

import pytest

from src.api.routes import alerts as alerts_route
from src.api.routes import work_orders as work_orders_route
from src.api.schemas import AlertCloseRequest, WorkOrderCreate
from src.realtime.manager import manager

HOLD_S = 1.0


def _hold_write_lock(db_path, started: threading.Event):
    other = sqlite3.connect(db_path)
    other.execute("BEGIN IMMEDIATE")
    other.execute("UPDATE machines SET speed_rpm = speed_rpm WHERE machine_id = 'm2'")
    started.set()
    time.sleep(HOLD_S)
    other.commit()
    other.close()


def _user(conn, role):
    row = conn.execute("SELECT id, email, name, role FROM users WHERE role = ?", (role,)).fetchone()
    return dict(row)


async def _max_loop_gap(make_call):
    gaps = []
    stop = asyncio.Event()

    async def ticker():
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tick_task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    try:
        result = await make_call()
    finally:
        stop.set()
        await tick_task
    return result, max(gaps)


@pytest.fixture
def route_db(db_path, monkeypatch):
    async def _no_broadcast(event):
        return None

    monkeypatch.setattr(manager, "broadcast", _no_broadcast)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from src.storage.migrations import ensure_current_schema

    ensure_current_schema(conn)
    yield conn
    conn.close()


def _run_while_locked(db_path, make_call):
    started = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(db_path, started))
    holder.start()
    assert started.wait(5)
    try:
        return asyncio.run(_max_loop_gap(make_call))
    finally:
        holder.join()


def test_close_alert_waits_for_sqlite_off_the_loop(db_path, route_db):
    user = _user(route_db, "supervisor")
    payload = AlertCloseRequest(outcome="false_alarm")
    response, gap = _run_while_locked(
        db_path, lambda: alerts_route.close_alert(2, payload, db=route_db, user=user))
    assert response.closed is True
    assert gap < HOLD_S / 2, f"event loop blocked for {gap:.2f}s"


def test_acknowledge_waits_for_sqlite_off_the_loop(db_path, route_db):
    user = _user(route_db, "operator")
    alert, gap = _run_while_locked(
        db_path, lambda: alerts_route.acknowledge_alert(2, db=route_db, user=user))
    assert alert.acknowledged_at is not None
    assert gap < HOLD_S / 2, f"event loop blocked for {gap:.2f}s"


def test_create_work_order_waits_for_sqlite_off_the_loop(db_path, route_db):
    user = _user(route_db, "supervisor")
    payload = WorkOrderCreate(machine_id="m2", title="Inspect bearing")
    order, gap = _run_while_locked(
        db_path, lambda: work_orders_route.create_work_order(payload, db=route_db, user=user))
    assert order.machine_id == "m2"
    assert gap < HOLD_S / 2, f"event loop blocked for {gap:.2f}s"
