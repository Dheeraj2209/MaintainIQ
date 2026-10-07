"""Off-tick delivery of pages (email + push) for background jobs.

A scheduler job must only do quick database work: BackgroundScheduler runs
its jobs one after another and broadcasts a job's events only when the job
returns, and the next tick waits for the whole run. Sending email inline from
a job (smtplib, up to SMTP_TIMEOUT per recipient) therefore held every
alert_paged event until the sweep ended, delayed newly due pages behind a
backlog, and kept the device watchdog from ticking.

So jobs commit their state change, return the event at once and hand the
slow part to a NotificationWorker: one daemon thread draining a FIFO queue.
Each task gets its own connection from the injectable connection factory
(the tick's connection is closed as soon as the job returns) and a failing
task is logged and never stops the worker. One thread keeps pages in order
and caps how many SMTP connections MaintainIQ opens at once.
"""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from typing import Callable

from src.storage.db import get_connection

logger = logging.getLogger(__name__)

Task = Callable[[sqlite3.Connection], object]

_STOP = object()


class NotificationWorker:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection] = get_connection,
        name: str = "notification-delivery",
    ) -> None:
        self._connection_factory = connection_factory
        self._name = name
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def submit(self, task: Task, description: str = "notification") -> None:
        """Queue `task(conn)`; returns immediately. Dropped (logged) after stop()."""
        with self._lock:
            if self._stopped:
                logger.warning("Delivery worker stopped; dropping %s", description)
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._queue.put((task, description))

    def join(self, timeout: float | None = None) -> bool:
        """Wait until every queued task has run (tests); True if drained."""
        done = threading.Event()

        def _mark(_conn):
            done.set()

        with self._lock:
            if self._thread is None:
                return True
            self._queue.put((_mark, "join marker"))
        return done.wait(timeout)

    def stop(self, timeout: float = 10.0) -> None:
        """Run what is already queued (bounded by `timeout`), then end the
        thread. Idempotent; later submits are dropped."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
            if thread is not None:
                self._queue.put((_STOP, "stop"))
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("Delivery worker still busy after %.0f s; leaving it to finish",
                               timeout)

    def _run(self) -> None:
        while True:
            task, description = self._queue.get()
            if task is _STOP:
                return
            self._run_one(task, description)

    def _run_one(self, task: Task, description: str) -> None:
        try:
            conn = self._connection_factory()
        except Exception:
            logger.exception("Delivery of %s: could not open a DB connection", description)
            return
        try:
            conn.row_factory = sqlite3.Row
            task(conn)
        except Exception:
            logger.exception("Delivery of %s failed", description)
        finally:
            try:
                conn.close()
            except Exception:
                logger.debug("Closing the delivery connection failed", exc_info=True)
