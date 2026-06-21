"""Unified backend: run the FastAPI app (serving the built dashboard) and the
Discord bot on a single asyncio event loop, so the desktop app supervises ONE
process instead of three. ``olisar/runtime/__main__.py`` is the CLI entry.

The bot runs as a restartable background task owned by ``BotSupervisor`` rather
than on the foreground, so a bot crash never takes the API/dashboard down, and the
first-run setup wizard stays reachable before any token exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import uvicorn

log = logging.getLogger("olisar.runtime")


class BotSupervisor:
    """The discord.py bot as a lazily-started, restartable background task."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._bot = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def bot(self):
        """The live discord.py client, or None when the bot isn't running. Used by the
        API to re-check an admin's Manage-Server permission against the guild in real time."""
        return self._bot

    async def start(self) -> None:
        """Start the bot if a token is configured; otherwise idle (awaiting setup)."""
        if self.running:
            return
        await _apply_runtime_config()  # fold DB config into settings before cogs load
        token = await _resolve_token()
        if not token:
            log.warning("no Discord token configured — bot idle, awaiting setup")
            return
        self._task = asyncio.create_task(self._run(token), name="olisar-bot")
        log.info("bot task started")

    async def _run(self, token: str) -> None:
        from bot.client import OlisarBot

        bot = self._bot = OlisarBot()
        try:
            async with bot:
                await bot.start(token)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("bot task crashed")
        finally:
            self._bot = None

    async def stop(self) -> None:
        task, bot = self._task, self._bot
        self._task, self._bot = None, None
        if bot is not None:
            with contextlib.suppress(Exception):
                await bot.close()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        log.info("bot task stopped")

    async def restart(self) -> None:
        """Stop and re-start the bot — used when the token/target guild changes."""
        await self.stop()
        await self.start()


async def _resolve_token() -> str:
    from olisar import runtime_config

    return await runtime_config.discord_token()


async def _apply_runtime_config() -> None:
    """Fold DB-backed runtime config into the in-memory ``settings`` singleton so the
    many synchronous ``settings.target_guild_id`` read sites (slash-command
    registration, DM handling) see the configured guild. Resolve-once: changing the
    target guild takes effect on the next bot (re)start."""
    from olisar import runtime_config
    from olisar.config import settings

    gid = await runtime_config.target_guild_id()
    if gid:
        settings.target_guild_id = gid


async def _init_database() -> None:
    """Create/upgrade the schema and seed guild defaults — idempotent, replaces the
    old manual ``python -m scripts.init_db`` step so a fresh install just works."""
    from scripts.init_db import create_schema, seed_builtins, seed_defaults

    await create_schema()
    await seed_defaults()
    await seed_builtins()


async def _self_check() -> bool:
    """Confirm sqlite-vec + FTS5 loaded (the #1 packaging risk). Returns True on
    success; the result is surfaced on ``/api/health`` (``vec``) so the tray can warn
    when a packaged bundle is missing the native extension."""
    from sqlalchemy import text

    from olisar.db.engine import session_scope

    try:
        async with session_scope() as session:
            ver = await session.scalar(text("SELECT vec_version()"))
            # FTS5 smoke test: build a throwaway virtual table (rolled back).
            await session.execute(text("CREATE VIRTUAL TABLE temp._vec_check USING fts5(x)"))
            await session.execute(text("DROP TABLE temp._vec_check"))
        log.info("sqlite-vec + FTS5 loaded (vec_version=%s)", ver)
        return True
    except Exception:
        log.exception("sqlite-vec/FTS5 self-check FAILED — vector search will not work")
        return False


async def run(host: str, port: int) -> None:
    """Boot the DB, then serve the API + bot on one loop until SIGINT/SIGTERM."""
    from api.main import create_app
    from olisar import runtime_config

    # The browser/Discord reach us over loopback regardless of the bind host, so the
    # public URL (and thus the OAuth redirect) uses 127.0.0.1 + the chosen port.
    runtime_config.set_local_base_url(f"http://127.0.0.1:{port}")

    await _init_database()
    await _apply_runtime_config()
    vec_ok = await _self_check()

    from olisar import sandbox

    sandbox_ok = sandbox.self_check()  # the #2 packaging risk: the QuickJS extension VM
    if not sandbox_ok:
        log.error("extension sandbox self-check FAILED — SDK extensions (incl. built-ins) won't run")

    from olisar.sandbox import transpile

    transpile_ok = transpile.self_check()  # vendored TS compiler — needed to author/import
    if not transpile_ok:
        log.error("transpile self-check FAILED — authoring/importing extensions won't work")

    from olisar.extensions import signing

    signing_ok = signing.self_check()  # Ed25519 — needed to sign exports / verify imports
    if not signing_ok:
        log.error("signing self-check FAILED — .olx bundles won't be signed/verified")

    app = create_app()
    app.state.vec_ok = vec_ok  # surfaced on /api/health for the tray
    app.state.sandbox_ok = sandbox_ok
    app.state.transpile_ok = transpile_ok
    app.state.signing_ok = signing_ok
    supervisor = BotSupervisor()
    app.state.bot_supervisor = supervisor  # setup wizard (Phase 2) restarts via this

    from olisar.runtime.paths import tailscale_state_dir
    from olisar.runtime.tunnel import FunnelManager

    tunnel = FunnelManager()
    app.state.tunnel = tunnel  # /api/tunnel control + tray toggle
    if await runtime_config.tunnel_enabled():
        ok, msg = await tunnel.start(
            await runtime_config.tunnel_token(),
            await runtime_config.tunnel_node(),
            runtime_config.local_base_url(),
            str(tailscale_state_dir()),
        )
        if not ok:
            log.warning("Funnel auto-start skipped: %s", msg)

    # Trust X-Forwarded-* from the Tailscale Funnel sidecar (a local-only reverse proxy
    # in front of this server) so the OAuth flow sees the real public host/scheme.
    config = uvicorn.Config(
        app, host=host, port=port, loop="asyncio", log_config=None,
        proxy_headers=True, forwarded_allow_ips="127.0.0.1",
    )
    server = uvicorn.Server(config)
    # Electron/the parent process owns lifecycle — don't let uvicorn grab the signals.
    server.install_signal_handlers = lambda: None

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # SIGTERM is absent on Windows
            loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))

    await supervisor.start()
    log.info("backend listening on http://%s:%d", host, port)
    try:
        await server.serve()
    finally:
        await supervisor.stop()
        await tunnel.stop()
        from olisar.db.engine import get_engine

        with contextlib.suppress(Exception):
            await get_engine().dispose()
