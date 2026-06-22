"""FastAPI admin backend.

Run with:  uv run uvicorn api.main:app --host 127.0.0.1 --port 8000

Shares the bot's SQLite DB, so edits made here are read live by the running bot
(next reply / next proactive scan) with no restart.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.auth.oauth import router as auth_router
from api.routers.admin import router as admin_router
from api.routers.bot import router as bot_router
from api.routers.extensions import router as extensions_router
from api.routers.knowledge import router as knowledge_router
from api.routers.marketplace import router as marketplace_router
from api.routers.settings import router as settings_router
from api.routers.setup import router as setup_router
from api.routers.tunnel import router as tunnel_router
from olisar.runtime.paths import web_dist_dir


def create_app() -> FastAPI:
    app = FastAPI(title="Olisar Admin API")

    # The dashboard is served same-origin in the desktop app/production (StaticFiles
    # below) and through the tunnel, so CORS only needs to admit the dev Vite server
    # on whatever loopback port it picked. A regex keeps that origin-agnostic.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(bot_router)
    app.include_router(extensions_router)
    app.include_router(marketplace_router)
    app.include_router(knowledge_router)
    app.include_router(setup_router)
    app.include_router(tunnel_router)
    app.include_router(settings_router)

    @app.get("/api/health")
    async def health(request: Request):
        # ``vec``/``sandbox`` are None when the unified runtime hasn't run its self-checks
        # (e.g. the standalone dev API); True/False once it has, so the tray can warn on a
        # bad bundle (missing sqlite-vec or the QuickJS extension sandbox).
        return {
            "ok": True,
            "vec": getattr(request.app.state, "vec_ok", None),
            "sandbox": getattr(request.app.state, "sandbox_ok", None),
            "transpile": getattr(request.app.state, "transpile_ok", None),
            "signing": getattr(request.app.state, "signing_ok", None),
        }

    # Serve the built dashboard at the same origin (desktop app + production).
    # Mounted LAST so the API/auth routers above win; ``html=True`` returns
    # index.html at ``/``. Skipped in dev when there's no build (the Vite dev
    # server serves the UI on its own port instead).
    dist = web_dist_dir()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


app = create_app()
