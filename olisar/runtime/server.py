"""Unified backend: run the FastAPI app (serving the built dashboard) and one Discord bot on
a single asyncio event loop. ``olisar/runtime/__main__.py`` is the CLI entry.

One process is one bot. It runs two ways, which differ only in which database they pin, what
origin the console is reached at, and how they learn to stop:

  - single (``run``) — the whole install is one bot: the Docker image on a VM, a source run.
  - worker (``run_worker``) — one bot of a desktop install. The gateway
    (:mod:`olisar.runtime.gateway`) starts one per bot on a private port, with that bot's own
    data dir, and routes the console to whichever the operator is looking at.

The bot runs as a restartable background task owned by ``BotSupervisor`` rather than on the
foreground, so a bot crash never takes the API/dashboard down, and the first-run setup wizard
stays reachable before any token exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket

import uvicorn

log = logging.getLogger("olisar.runtime")

# The first line a worker prints: the gateway reads its private port from it.
WORKER_PORT_MARKER = "OLISAR_WORKER_PORT="


class BotSupervisor:
    """The discord.py bot as a lazily-started, restartable background task. One per process:
    ``profile_id`` names the bot profile this process was started for."""

    def __init__(self, profile_id: str | None = None) -> None:
        self.profile_id = profile_id
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
        from olisar import runtime_config

        # Server-hosted profiles run the bot on the operator's cloud VM — this local install
        # is only their control panel, so never launch a local bot for them.
        if await runtime_config.hosting_mode() == "server":
            log.info("profile %s is server-hosted — no local bot", self.profile_id)
            return
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


_ENV_TARGET_GUILD_ID: int | None = None


def _env_target_guild_id() -> int:
    """The ``.env``-provided home guild, snapshotted once before any per-profile mutation, so
    ``_apply_runtime_config`` can fall back to it when a profile's DB sets none."""
    global _ENV_TARGET_GUILD_ID
    if _ENV_TARGET_GUILD_ID is None:
        from olisar.config import settings

        _ENV_TARGET_GUILD_ID = int(getattr(settings, "target_guild_id", 0) or 0)
    return _ENV_TARGET_GUILD_ID


async def _apply_runtime_config() -> None:
    """Fold DB-backed runtime config into the in-memory ``settings`` singleton so the
    many synchronous ``settings.target_guild_id`` read sites (slash-command
    registration, DM handling) see the configured guild. Resolve-once: changing the
    target guild takes effect on the next bot (re)start.

    Assigns the home guild **unconditionally** (including 0), reading it straight from this
    profile's DB rather than the fallback accessor, so a DB that clears it clears it here."""
    from olisar import runtime_config
    from olisar.config import settings

    env_fallback = _env_target_guild_id()  # captured before we mutate settings below
    db_gid = await runtime_config.db_target_guild_id()
    settings.target_guild_id = db_gid or env_fallback


def bot_supervisor(app) -> BotSupervisor | None:
    """This process's ``BotSupervisor`` (None in the standalone dev API)."""
    return getattr(app.state, "bot_supervisor", None)


async def start_bot(app) -> None:
    supervisor = bot_supervisor(app)
    if supervisor is not None:
        await supervisor.start()


async def stop_bot(app) -> None:
    supervisor = bot_supervisor(app)
    if supervisor is not None:
        with contextlib.suppress(Exception):
            await supervisor.stop()


async def _init_database() -> None:
    """Create/upgrade the schema and seed guild defaults — idempotent, replaces the
    old manual ``python -m scripts.init_db`` step so a fresh install just works.

    ``create_schema`` migrates forward only (and drops a table whose primary key changed),
    so snapshot the database first whenever a *different* build is about to touch it."""
    from scripts.init_db import (
        create_schema,
        migrate_model_default,
        seed_builtins,
        seed_defaults,
    )

    from olisar.config import settings
    from olisar.runtime import dbbackup
    from olisar.updates import current_version

    version = current_version()
    dbbackup.before_migration(settings.database_path, version)
    await create_schema()
    moved = await migrate_model_default()
    if moved:
        from olisar.gemini.models import DEFAULT_CHAT_MODEL

        log.info(
            "moved %d guild(s) off the auto-updating model alias onto %s",
            moved, DEFAULT_CHAT_MODEL,
        )
    dbbackup.record_version(settings.database_path, version)
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
    """Single-bot mode: serve the install's launch-default bot until SIGINT/SIGTERM."""
    from olisar.runtime import profiles

    # Adopt the launch default as active, then pin the DB to it. Runs after bootstrap_env()
    # (which set the default DATABASE_PATH), so the profile's own path wins.
    profiles.set_active(profiles.default_id())
    profile_id = profiles.active_id()
    await serve_instance(
        profile_id=profile_id,
        db_path=str(profiles.db_path_for(profile_id)),
        host=host,
        port=port,
    )


async def run_worker(profile_id: str) -> None:
    """Worker mode: serve one bot of a desktop install on a private loopback port.

    Binds before anything else and prints the port, so the gateway knows where to look while
    the database is still being prepared. The gateway holds our stdin open for as long as it
    lives: EOF means it's gone (quit, crashed, or killed outright — on Windows that's the only
    kind of kill there is), so shut down, and don't linger past a deadline doing it. An
    orphaned worker would keep this bot logged in to Discord next to its replacement."""
    import os
    import sys
    import threading

    from olisar.runtime import paths

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    print(f"{WORKER_PORT_MARKER}{sock.getsockname()[1]}", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def watch_parent() -> None:
        # os.read on the descriptor, not sys.stdin: a daemon thread parked inside the buffered
        # reader holds its lock, and interpreter shutdown aborts on that lock if this process
        # exits for any other reason (a crash during boot) while we're still waiting here.
        with contextlib.suppress(Exception):
            fd = sys.stdin.fileno()
            while os.read(fd, 4096):
                pass
        loop.call_soon_threadsafe(stop.set)
        threading.Timer(20.0, os._exit, (0,)).start()

    threading.Thread(target=watch_parent, name="olisar-parent-watch", daemon=True).start()

    await serve_instance(
        profile_id=profile_id,
        db_path=str(paths.db_path()),
        console_url=os.environ.get("OLISAR_CONSOLE_URL") or None,
        sock=sock,
        stop=stop,
    )


async def serve_instance(
    *,
    profile_id: str,
    db_path: str,
    console_url: str | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    sock: socket.socket | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Boot the DB, then serve the API + this bot on one loop until told to stop.

    ``console_url`` is the origin the operator's console is reached at, when that isn't this
    server itself (a worker behind the gateway) — the local OAuth redirect is built from it.
    ``sock`` is an already-bound listening socket to serve on instead of ``host``/``port``."""
    from api.main import create_app
    from olisar import runtime_config
    from olisar.config import settings
    from olisar.db import engine

    listen_port = sock.getsockname()[1] if sock is not None else port
    # The browser/Discord reach us over loopback regardless of the bind host, so the
    # public URL (and thus the OAuth redirect) uses 127.0.0.1 + the chosen port.
    runtime_config.set_local_base_url(console_url or f"http://127.0.0.1:{listen_port}")
    runtime_config.set_listen_url(f"http://127.0.0.1:{listen_port}")

    engine.pin_database(db_path)
    settings.database_path = db_path  # for the few places that read it directly

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
    app.state.profile_id = profile_id
    app.state.bot_supervisor = BotSupervisor(profile_id)

    from olisar.runtime.paths import tailscale_state_dir
    from olisar.runtime.tunnel import FunnelManager

    tunnel = FunnelManager()
    app.state.tunnel = tunnel  # /api/tunnel control + tray toggle
    # Auto-start the Funnel when the operator enabled it, or in a headless server
    # deployment given a Tailscale auth key — so a cloud VM publishes its …ts.net URL
    # without the loopback-only /api/tunnel/enable call.
    token = await runtime_config.tunnel_token()
    if await runtime_config.tunnel_enabled() or (settings.headless and token):
        ok, msg = await tunnel.start(
            token,
            await runtime_config.tunnel_node() or "olisar",
            runtime_config.listen_url(),
            str(tailscale_state_dir()),
        )
        if ok and msg.startswith("http"):
            # Persist so public_base_url() / the OAuth redirect resolve to the public
            # …ts.net host. The headless auto-start never goes through /api/tunnel/enable
            # (which is what normally records this), so the console would otherwise keep
            # seeing the loopback URL — Remote access stuck on "Starting…", sidebar "off".
            # NB: a distinct name — must NOT shadow the `host` bind address passed to uvicorn.
            funnel_host = msg.replace("https://", "").replace("http://", "").rstrip("/")
            await runtime_config.save(tunnel_enabled=True, tunnel_hostname=funnel_host)
        elif not ok:
            log.warning("Funnel auto-start skipped: %s", msg)

    # Publish what only this process knows (public URL + self-check results) so an
    # out-of-band reader doesn't have to grep our logs for it. In a server deployment
    # that reader is the desktop control panel, over `docker exec … cat`.
    from olisar.runtime import state

    state.write(
        public_url=await runtime_config.public_base_url(),
        vec_ok=vec_ok,
        sandbox_ok=sandbox_ok,
        transpile_ok=transpile_ok,
        signing_ok=signing_ok,
    )

    # Trust X-Forwarded-* from the Tailscale Funnel sidecar (a local-only reverse proxy
    # in front of this server) so the OAuth flow sees the real public host/scheme.
    config = uvicorn.Config(
        app, host=host, port=listen_port, loop="asyncio", log_config=None,
        proxy_headers=True, forwarded_allow_ips="127.0.0.1",
    )
    server = uvicorn.Server(config)
    # Electron/the parent process owns lifecycle — don't let uvicorn grab the signals.
    server.install_signal_handlers = lambda: None

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # SIGTERM is absent on Windows
            loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))

    stopper: asyncio.Task | None = None
    if stop is not None:
        async def _stop_when_told() -> None:
            await stop.wait()
            server.should_exit = True

        stopper = asyncio.create_task(_stop_when_told(), name="olisar-stop-watch")

    await start_bot(app)

    # Server hosting: this install is the control panel for a bot running on the operator's
    # VM. If we've come up ahead of that VM — which is what every launch after the app
    # updated itself looks like — bring the VM onto the newest release too. This replaced
    # the daily systemd timer the VM used to run: the client is the side that knows a
    # release exists, so it's the side that applies it.
    if await runtime_config.hosting_mode() == "server":
        from olisar.runtime import remote

        remote.spawn_autoupdate()

    log.info("backend listening on %s", runtime_config.listen_url())
    try:
        await server.serve(sockets=[sock] if sock is not None else None)
    finally:
        if stopper is not None:
            stopper.cancel()
        await stop_bot(app)
        await tunnel.stop()
        with contextlib.suppress(Exception):
            await engine.get_engine().dispose()
