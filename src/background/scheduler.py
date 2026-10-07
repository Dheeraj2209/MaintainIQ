"""Generic in-process background scheduler (device-health design, decision 7).

Some things can only be noticed by the passage of time — a sensor node that
went silent, an alert nobody acknowledged — so something has to look
periodically. This is that something, kept deliberately small:

* Jobs are plain sync callables ``job(conn, now) -> list[dict]``. Each tick
  runs every registered job in order on a worker thread
  (``asyncio.to_thread``), each with its own connection from the injectable
  ``connection_factory`` (default ``src.storage.db.get_connection``; never
  the request-scoped ``get_db``), closed afterwards whatever happens.
* The events a job returns are broadcast from the loop with
  ``await broadcast(event)`` (default: the realtime manager), so jobs stay
  free of asyncio.
* Ticks never overlap: the loop awaits one tick before sleeping again.
* The first tick happens one interval after start, not immediately. That
  avoids racing app startup, and a lifespan test with a long interval never
  touches a database.
* A failing job, connection or broadcast is logged and never stops the loop
  or the other jobs.

Disabled unless MAINTAINIQ_SWEEP_INTERVAL_S > 0 (the app lifespan only builds
one then), so a plain API start and the test suite run no background work.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from src.storage.db import get_connection

logger = logging.getLogger(__name__)

INTERVAL_ENV = "MAINTAINIQ_SWEEP_INTERVAL_S"

Job = Callable[[sqlite3.Connection, datetime], list]


@dataclass(frozen=True)
class SchedulerSettings:
    interval_s: float = 0.0  # 0 ⇒ disabled

    @property
    def enabled(self) -> bool:
        return self.interval_s > 0

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        """MAINTAINIQ_SWEEP_INTERVAL_S; unset/empty ⇒ 0 (disabled). Raises
        ValueError for a non-numeric, negative or non-finite value."""
        raw = os.environ.get(INTERVAL_ENV, "").strip()
        if not raw:
            return cls()
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(f"{INTERVAL_ENV} must be a number of seconds, got {raw!r}") from None
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{INTERVAL_ENV} must be >= 0 and finite, got {raw!r}")
        return cls(interval_s=value)


async def _default_broadcast(event: dict) -> None:
    from src.realtime.manager import manager

    await manager.broadcast(event)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundScheduler:
    def __init__(
        self,
        *,
        interval_s: float,
        connection_factory: Callable[[], sqlite3.Connection] = get_connection,
        broadcast: Callable[[dict], Awaitable[None]] = _default_broadcast,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not interval_s or interval_s <= 0:
            raise ValueError("interval_s must be > 0; a disabled scheduler is simply not built")
        self.interval_s = float(interval_s)
        self._connection_factory = connection_factory
        self._broadcast = broadcast
        self._now = now_fn
        self._jobs: list[tuple[str, Job]] = []
        self._task: asyncio.Task | None = None

    @property
    def jobs(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._jobs)

    def register(self, name: str, job: Job) -> None:
        self._jobs.append((name, job))

    # --- One tick ---------------------------------------------------------------

    def _run_job(self, name: str, job: Job, now: datetime) -> list:
        """Runs on a worker thread: own connection, always closed."""
        try:
            conn = self._connection_factory()
        except Exception:
            logger.exception("Background job %s: could not open a DB connection", name)
            return []
        try:
            return list(job(conn, now) or [])
        except Exception:
            logger.exception("Background job %s failed", name)
            return []
        finally:
            try:
                conn.close()
            except Exception:
                logger.debug("Closing the connection of job %s failed", name, exc_info=True)

    async def run_once(self) -> list[dict]:
        """One tick of every job, in registration order. Returns the events it
        broadcast (tests use this; the loop ignores it)."""
        now = self._now()
        events: list[dict] = []
        for name, job in self._jobs:
            job_events = await asyncio.to_thread(self._run_job, name, job, now)
            for event in job_events:
                try:
                    await self._broadcast(event)
                except Exception:
                    logger.exception("Broadcasting %s event from job %s failed",
                                     event.get("type") if isinstance(event, dict) else event, name)
                events.append(event)
        return events

    # --- Loop ---------------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # run_once already guards each job; belt and braces
                logger.exception("Background scheduler tick failed")

    def start(self) -> None:
        """Create the loop task on the running event loop; idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="background-scheduler")
        logger.info("Background scheduler started (every %.1f s): %s",
                    self.interval_s, ", ".join(self.jobs) or "no jobs")

    async def stop(self) -> None:
        """Cancel the loop and wait for it; idempotent. A job already running
        on its worker thread finishes there, but nothing it returns is
        broadcast after stop()."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Background scheduler ended with an error")
        logger.info("Background scheduler stopped")


_scheduler: BackgroundScheduler | None = None


def set_scheduler(scheduler: BackgroundScheduler | None) -> None:
    """Called by the app lifespan when the scheduler starts / stops."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> BackgroundScheduler | None:
    """The running scheduler, or None when background jobs are disabled."""
    return _scheduler
