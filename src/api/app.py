"""MaintainIQ API + dashboard host (M4).

Serves the JSON API under /api/* and the React single-page app at /. Build the
SPA first (``cd frontend && npm run build``), then run:

    uvicorn src.api.app:app --reload

against the maintainiq.db produced by src/training/run_pipeline.py. During
frontend development, run ``npm run dev`` instead — the Vite dev server proxies
/api to this backend on :8000.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes import alerts, kpis, machines, maintenance

app = FastAPI(title="MaintainIQ API", version="0.4.0")

# API routers share the /api prefix so the dashboard mount at "/" below does
# not shadow them.
for module in (machines, alerts, maintenance, kpis):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health", tags=["meta"])
def health_check():
    return {"status": "ok"}


# Built React SPA at the site root. StaticFiles ships with Starlette (a FastAPI
# dependency) — no extra requirement. Mounted last so /api/* wins. Prefer the
# Vite build output; fall back to the legacy vanilla dashboard if it has not
# been built yet (``cd frontend && npm run build``).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPA_DIR = _REPO_ROOT / "frontend" / "dist"
_LEGACY_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "static"
_WEB_DIR = _SPA_DIR if _SPA_DIR.exists() else _LEGACY_DIR
if _WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="dashboard")
