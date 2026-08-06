"""MaintainIQ API + dashboard host (M4).

Serves the JSON API under /api/* and the React single-page app at /. Build the
SPA first (``cd frontend && npm run build``), then run:

    uvicorn src.api.app:app --reload

against the maintainiq.db produced by src/training/run_pipeline.py. During
frontend development, run ``npm run dev`` instead — the Vite dev server proxies
/api to this backend on :8000.
"""
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import (
    alerts,
    auth,
    demo,
    ingestion,
    kpis,
    machines,
    maintenance,
    notifications,
    predictions,
    users,
)
from src.auth.deps import get_current_user
from src.realtime.routes import realtime

app = FastAPI(title="MaintainIQ API", version="0.5.0")

# auth.router is reachable while logged out (you can't require a session to
# create one). users.router, notifications.router, demo.router, and
# realtime.router bake their own role requirement in via `dependencies=` on
# the APIRouter itself (admin-only, admin+supervisor, admin-only, session-only
# respectively). Every other API router just requires get_current_user. API
# routers share the /api prefix so the SPA catch-all below does not shadow
# them.
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(demo.router, prefix="/api")
for module in (machines, alerts, maintenance, kpis, predictions, ingestion):
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

if _WEB_DIR.exists():
    _ASSETS_DIR = _WEB_DIR / "assets"
    if _ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

    # Catch-all so client-side routes (e.g. /machines/m1) survive a hard
    # refresh or direct link: StaticFiles(html=True) alone only falls back to
    # index.html for directory-like paths, not arbitrary sub-routes. Anything
    # that matches a real file (favicon, manifest, etc.) is served directly;
    # everything else gets index.html and React Router takes over client-side.
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        candidate = _WEB_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_DIR / "index.html")
