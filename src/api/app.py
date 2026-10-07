"""MaintainIQ API + dashboard host (M4).

Serves the JSON API under /api/* and the React single-page app at /. Build the
SPA first (``cd frontend && npm run build``), then run:

    uvicorn src.api.app:app --reload

against the maintainiq.db produced by src/training/run_pipeline.py. During
frontend development, run ``npm run dev`` instead — the Vite dev server proxies
/api to this backend on :8000.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.api.routes import (
    alerts,
    auth,
    demo,
    ingestion,
    kpis,
    machines,
    maintenance,
    model,
    notifications,
    predictions,
    push,
    reports,
    telemetry,
    users,
    work_orders,
)
from src.auth.deps import get_current_user
from src.background import scheduler as background
from src.realtime.manager import manager
from src.realtime.routes import realtime
from src.storage.db import get_connection

logger = logging.getLogger(__name__)

# Where background jobs get their connections. Module-level so tests can
# monkeypatch it: the scheduler must never touch the developer's real
# maintainiq.db from the suite (get_db overrides do not reach it).
_scheduler_connection_factory = get_connection


def _start_mqtt_ingest():
    """Start live MQTT ingest iff MQTT_BROKER_HOST is set (M6, contract §7).

    Never fatal: a malformed MQTT_* variable is logged and ingest stays off,
    and an unreachable broker only means paho keeps retrying in the background
    (connect_async) — the dashboard and API must come up regardless.
    """
    from src.telemetry.config import TelemetrySettings
    from src.telemetry.mqtt_service import build_service

    try:
        settings = TelemetrySettings.from_env()
    except ValueError:
        logger.exception("Invalid MQTT configuration; live telemetry ingest disabled")
        return None
    if not settings.enabled:
        return None
    try:
        service = build_service(settings)
        service.start()
    except Exception:
        logger.exception("Could not start MQTT ingest; live telemetry disabled")
        return None
    telemetry.set_mqtt_service(service)
    return service


def _start_background_jobs(mqtt_service):
    """Start the background scheduler iff MAINTAINIQ_SWEEP_INTERVAL_S > 0
    (device-health design, decision 7) with the device-silence watchdog and
    the alert paging ladder (work-orders design, decision 7). Returns
    (scheduler, delivery worker), or (None, None) when background jobs are
    off. Both jobs hand their pages to the delivery worker's thread.

    Never fatal, like _start_mqtt_ingest: a malformed value is logged and
    background jobs stay off. The watchdog only acts while live MQTT ingest is
    connected and past its grace window, so with ingest disabled it simply
    idles (it cannot tell a silent node from our own deafness). Invalid
    ESCALATION_* values fall back to the default ladder rather than turning
    paging off."""
    from src.alerts import paging
    from src.telemetry import watchdog

    try:
        settings = background.SchedulerSettings.from_env()
    except ValueError:
        logger.exception("Invalid %s; background jobs disabled", background.INTERVAL_ENV)
        return None, None
    if not settings.enabled:
        return None, None
    from src.background.delivery import NotificationWorker

    # Pages are sent on their own thread so a slow SMTP server never stalls a
    # tick (and with it every broadcast and the other jobs).
    delivery = NotificationWorker(connection_factory=_scheduler_connection_factory)
    try:
        watcher = watchdog.DeviceSilenceWatcher(
            grace_s=watchdog.WatchdogSettings.from_env_or_default().grace_s,
            ingest_connected_for=(mqtt_service.connected_for_s if mqtt_service is not None
                                  else (lambda: None)),
            deliver=delivery.submit,
        )
        scheduler = background.BackgroundScheduler(
            interval_s=settings.interval_s,
            connection_factory=_scheduler_connection_factory,
        )
        pager = paging.AlertPager(policy=paging.PagingPolicy.from_env_or_default(),
                                  deliver=delivery.submit)
        scheduler.register(watchdog.JOB_NAME, watcher.tick)
        scheduler.register(paging.JOB_NAME, pager.tick)
        scheduler.start()
    except Exception:
        logger.exception("Could not start background jobs")
        delivery.stop(timeout=0)
        return None, None
    watchdog.set_watcher(watcher)
    paging.set_pager(pager)
    background.set_scheduler(scheduler)
    return scheduler, delivery


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Hand the running event loop to the realtime manager.

    Replay ingestion predicts on plain worker threads, which have no loop of
    their own and so cannot await a broadcast. Binding here is what lets a
    prediction made on one of those threads reach connected dashboards; without
    it the alerts still land in the database but no browser hears about them
    until it refreshes.
    """
    manager.bind_loop(asyncio.get_running_loop())
    mqtt_service = _start_mqtt_ingest()
    scheduler, delivery = _start_background_jobs(mqtt_service)
    try:
        yield
    finally:
        # Background jobs first: the watchdog's gate reads the MQTT service.
        if scheduler is not None:
            from src.alerts import paging
            from src.telemetry import watchdog

            await scheduler.stop()
            # Let queued pages go out (bounded) before the process exits.
            await asyncio.to_thread(delivery.stop)
            background.set_scheduler(None)
            watchdog.set_watcher(None)
            paging.set_pager(None)
        # Then MQTT: its worker produces predictions (and broadcasts) just
        # like replay does, and its realtime listener must be unregistered
        # before the loop it is called from goes away.
        if mqtt_service is not None:
            mqtt_service.stop()
            telemetry.set_mqtt_service(None)
        # Stop replay workers before the loop goes away, so in-flight
        # broadcasts don't target a closed loop on shutdown.
        from src.api.routes.ingestion import get_replay_service

        get_replay_service().stop_all()
        manager.bind_loop(None)


app = FastAPI(title="MaintainIQ API", version="0.5.0", lifespan=lifespan)

# auth.router is reachable while logged out (you can't require a session to
# create one). users.router, notifications.router, demo.router, and
# realtime.router bake their own role requirement in via `dependencies=` on
# the APIRouter itself (admin-only, admin+supervisor, admin-only, session-only
# respectively). Every other API router just requires get_current_user — push
# included: every role may subscribe its own devices (mobile operator PWA
# design, decision 10). API routers share the /api prefix so the SPA
# catch-all below does not shadow them.
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(demo.router, prefix="/api")
for module in (machines, alerts, maintenance, kpis, predictions, ingestion, model, reports, telemetry,
               work_orders, push):
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_current_user)])


@app.get("/api/health", tags=["meta"])
def health_check():
    return {"status": "ok"}


# Built React SPA at the site root. StaticFiles ships with Starlette (a FastAPI
# dependency) — no extra requirement. Registered last so /api/* wins. Requires
# the Vite build to exist (``cd frontend && npm run build``); until then, only
# /api/* routes are served.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "frontend" / "dist"

# Files whose MIME type and caching must not be left to guesswork
# (mobile operator PWA design, decision 9). Python's mimetypes reads the
# Windows registry, which on many machines maps .js to text/plain — and
# browsers refuse to register a service worker served that way. sw.js and
# the manifest are revalidated on every load so a deploy is picked up.
_PWA_FILES = {
    "sw.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json",
}
_HTML = "text/html; charset=utf-8"
_NO_CACHE = {"Cache-Control": "no-cache"}


def spa_file_response(web_dir: Path, full_path: str) -> Response:
    """Serve `full_path` from the built SPA in `web_dir`.

    A real file inside web_dir is served as-is (PWA files with explicit MIME
    and no-cache); any other path gets index.html (no-cache) so client-side
    routes like /m/alerts/42 survive a hard refresh. Paths that resolve
    outside web_dir also get index.html, never the file. Unknown /api paths
    are a JSON 404: the service worker and the API client both assume /api
    is never HTML.
    """
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    root = web_dir.resolve()
    candidate = (web_dir / full_path).resolve()
    if full_path and candidate.is_relative_to(root) and candidate.is_file():
        relative = candidate.relative_to(root).as_posix()
        if relative in _PWA_FILES:
            return FileResponse(candidate, media_type=_PWA_FILES[relative], headers=_NO_CACHE)
        if relative == "index.html":
            return FileResponse(candidate, media_type=_HTML, headers=_NO_CACHE)
        return FileResponse(candidate)
    return FileResponse(root / "index.html", media_type=_HTML, headers=_NO_CACHE)


if _WEB_DIR.exists():
    _ASSETS_DIR = _WEB_DIR / "assets"
    if _ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

    # Catch-all so client-side routes (e.g. /machines/m1) survive a hard
    # refresh or direct link: StaticFiles(html=True) alone only falls back to
    # index.html for directory-like paths, not arbitrary sub-routes.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        return spa_file_response(_WEB_DIR, full_path)
