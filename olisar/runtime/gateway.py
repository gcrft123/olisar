"""The desktop gateway: every bot on this install running at once, behind one console.

Electron starts this (``olisar-backend --gateway --port 8723``). It runs no bot itself. For each
bot profile it starts a worker (``--worker <id>``): a complete Olisar backend (API, Discord bot,
Tailscale Funnel) on a private loopback port, pinned to that bot's own data directory. The
workers share nothing but this parent, so one bot can't read another's settings or keys, can't
take another down when it crashes, and can't stall another's event loop.

The gateway serves the dashboard and forwards the console's API and sign-in traffic to the bot
the operator is looking at (the registry's ``active`` profile). Switching bots only changes
where that traffic goes, and every bot keeps running. It answers ``/api/bots`` itself: the
list, create/rename/delete, and the operations that involve two bots, like lending one bot's VM
to another.

Forwarding is transparent on purpose. A worker sees the operator's own Host header and cookies,
and no forwarding headers for a loopback client, so it behaves exactly as it did when the
console talked to it directly:
  - the local OAuth redirect stays ``http://127.0.0.1:<gateway port>/auth/callback``, the URL
    every bot's Discord application already has registered
  - the loopback-only routes (setup, server control, remote access) stay loopback-only
  - each bot's session cookies carry a per-bot suffix (see ``api/auth/sessions.py``), so every
    bot keeps its own sign-in, and the original bot's cookies keep their old names
A sign-in finishes in the operator's browser, which doesn't know which bot started it, so the
gateway pins those round trips to the bot they began on with a short-lived cookie.

Remote visitors never come through here. Each worker publishes its own Funnel pointing at its
own port, so a member reaching bot B's public URL reaches bot B, whichever bot the console
happens to show.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from api.trust import LOOPBACK, require_local_request
from olisar.runtime import profiles
from olisar.runtime.console_files import ConsoleFiles
from olisar.runtime.paths import home_dir, web_dist_dir
from olisar.runtime.server import WORKER_PORT_MARKER

log = logging.getLogger("olisar.gateway")

INTERNAL_HEADER = "x-olisar-gateway"

# Sign-in round trips that finish in the operator's browser: the start sets the cookie, the
# callback follows it back to the bot that started.
ROUTE_COOKIE = "olisar_bot_route"
_ROUTE_START = frozenset({"/auth/login", "/api/marketplace/verify/start"})
_ROUTE_CALLBACK = frozenset({"/auth/callback", "/api/marketplace/verify/callback"})

# Configuration that belongs to one bot. A developer's environment configures the original
# bot; every other bot starts without these, so it can't inherit that bot's token or keys
# through the .env fallbacks.
_BOT_SPECIFIC_ENV = (
    "DISCORD_TOKEN", "DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET", "TARGET_GUILD_ID",
    "GEMINI_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "UEX_API_KEY",
    "TAILSCALE_AUTH", "OLISAR_FUNNEL_HOSTNAME", "SESSION_SECRET", "PUBLIC_BASE_URL",
)

_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "trailers", "transfer-encoding", "upgrade",
})

READY_TIMEOUT = 180.0   # a worker that hasn't answered /api/health by then is restarted
FAILED_AFTER = 3        # consecutive failed starts before a bot is reported as failed
STOP_GRACE = 15.0       # how long a worker gets to close its Discord session on shutdown
# A bot that ran this long before exiting counts as having worked; one that exits sooner is
# restarted on a growing backoff, capped here. Every restart logs in to Discord again, and a
# bot that crashes a few seconds after each login would otherwise spend Discord's daily
# session-start allowance in hours.
STABLE_AFTER = 300.0
MAX_BACKOFF = 600.0


def _in_thread(fn, *args) -> asyncio.Future:
    """Run a blocking call on its own daemon thread. Not ``asyncio.to_thread``: waiting on a
    bot's process lasts as long as the bot does, and one wait per bot would fill the default
    pool (min(32, CPUs + 4) threads) and stall everything else that uses it."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(ok: bool, value) -> None:
        if not future.done():
            (future.set_result if ok else future.set_exception)(value)

    def run() -> None:
        try:
            result = fn(*args)
        except BaseException as exc:  # noqa: BLE001 — handed to the awaiting coroutine
            loop.call_soon_threadsafe(settle, False, exc)
        else:
            loop.call_soon_threadsafe(settle, True, result)

    threading.Thread(target=run, name=f"olisar-{getattr(fn, '__name__', 'call')}", daemon=True).start()
    return future


# ── one bot's process ──────────────────────────────────────────────────────────


class Worker:
    """One bot's backend process, kept running: started, watched, restarted with backoff."""

    def __init__(self, profile_id: str, *, console_url: str, token: str) -> None:
        self.profile_id = profile_id
        self.console_url = console_url
        self.token = token
        self.port = 0
        self.state = "stopped"      # starting | ready | restarting | failed | stopped
        self.tail: collections.deque[str] = collections.deque(maxlen=40)
        self.client: httpx.AsyncClient | None = None
        self._proc: subprocess.Popen | None = None
        self._ready = asyncio.Event()
        self._settled = asyncio.Event()  # the first start attempt came up, or didn't
        self._retry = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.create_task(self._supervise(), name=f"olisar-worker-{self.profile_id}")

    async def wait_ready(self, timeout: float) -> bool:
        if self.ready:
            return True
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)
        return self.ready

    async def wait_settled(self, timeout: float) -> None:
        """Until this bot is up or its first attempt has failed — whichever comes first."""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._settled.wait(), timeout)

    def retry(self) -> None:
        """Skip the rest of a restart backoff (the operator pressed Retry)."""
        self._retry.set()

    async def stop(self) -> None:
        self._stopping = True
        self._retry.set()
        await _in_thread(self._terminate)
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, STOP_GRACE + 10)
        # Again: a process spawned while we were stopping the last one has to go too.
        await _in_thread(self._terminate)
        self.state = "stopped"
        await self._close_client()

    # ── internals ──

    def _label(self) -> str:
        p = profiles.get(self.profile_id) or {}
        return (p.get("name") or self.profile_id)[:24]

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):  # the PyInstaller bundle is the backend binary itself
            return [sys.executable, "--worker", self.profile_id]
        return [sys.executable, "-m", "olisar.runtime", "--worker", self.profile_id]

    def _env(self) -> dict[str, str]:
        legacy = profiles.is_legacy(self.profile_id)
        env = dict(os.environ)
        if not legacy:
            for key in _BOT_SPECIFIC_ENV:
                env.pop(key, None)
            env["OLISAR_NO_DOTENV"] = "1"
        env.update({
            "OLISAR_HOME": str(home_dir()),
            "OLISAR_DATA_DIR": str(profiles.data_dir_for(self.profile_id)),
            "DATABASE_PATH": str(profiles.db_path_for(self.profile_id)),
            "OLISAR_PROFILE_ID": self.profile_id,
            "OLISAR_CONSOLE_URL": self.console_url,
            "OLISAR_GATEWAY_TOKEN": self.token,
            "OLISAR_COOKIE_SUFFIX": "" if legacy else f"_{self.profile_id}",
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        env.pop("OLISAR_PORT", None)  # a worker picks its own port
        return env

    def _spawn(self) -> subprocess.Popen:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            self._command(),
            env=self._env(),
            stdin=subprocess.PIPE,  # held open for our lifetime; EOF tells the worker we're gone
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        self._proc = proc  # before the reader starts: it checks the port line is from this one
        threading.Thread(
            target=self._pump, args=(proc, self._label()), name=f"olisar-worker-log-{self.profile_id}",
            daemon=True,
        ).start()
        return proc

    def _pump(self, proc: subprocess.Popen, label: str) -> None:
        """Relay the worker's output to ours, and pick its port out of the first line."""
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith(WORKER_PORT_MARKER):
                with contextlib.suppress(ValueError, RuntimeError):
                    port = int(line[len(WORKER_PORT_MARKER):].strip())
                    if self._loop is not None:
                        self._loop.call_soon_threadsafe(self._on_port, proc, port)
                continue
            self.tail.append(line)
            with contextlib.suppress(Exception):
                sys.stdout.write(f"[{label}] {line}\n")
                sys.stdout.flush()

    def _on_port(self, proc: subprocess.Popen, port: int) -> None:
        if proc is self._proc:
            self.port = port

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        with contextlib.suppress(Exception):
            proc.stdin.close()  # graceful: the worker closes its Discord session and exits
        try:
            proc.wait(STOP_GRACE)
            return
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(Exception):
            proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(Exception):
                proc.kill()

    async def _close_client(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        failures = 0
        while not self._stopping:
            self.port = 0
            self._ready.clear()
            self.state = "starting" if failures < FAILED_AFTER else "failed"
            try:
                proc = await _in_thread(self._spawn)
            except Exception as exc:  # noqa: BLE001 — the binary is missing, or not executable
                self.tail.append(f"couldn't start: {exc}")
                log.exception("couldn't start the worker for bot %s", self.profile_id)
                self._settled.set()
                failures += 1
            else:
                if self._stopping:  # stopped while it was being spawned
                    await _in_thread(self._terminate)
                    break
                started = loop.time()
                watcher = asyncio.create_task(self._watch_ready(proc))
                code = await _in_thread(proc.wait)
                watcher.cancel()
                self._settled.set()
                self._ready.clear()
                await self._close_client()
                if self._stopping:
                    break
                log.warning("bot %s's process exited (code %s)", self.profile_id, code)
                failures = 1 if loop.time() - started >= STABLE_AFTER else failures + 1
            if self._stopping:
                break
            self.state = "failed" if failures >= FAILED_AFTER else "restarting"
            # A Retry pressed while the last attempt was still going counts: it isn't cleared
            # until it has been acted on.
            if not self._retry.is_set():
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._retry.wait(), min(MAX_BACKOFF, 2.0 ** failures))
            if self._retry.is_set() and not self._stopping:
                self._retry.clear()
                failures = min(failures, FAILED_AFTER - 1)  # a Retry gets a fresh "starting"
        self.state = "stopped"

    async def _watch_ready(self, proc: subprocess.Popen) -> None:
        """Poll the worker's health until it answers, then open it to the console."""
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT
        async with httpx.AsyncClient(timeout=2.0) as probe:
            while asyncio.get_running_loop().time() < deadline:
                if proc.poll() is not None:
                    return
                if self.port:
                    with contextlib.suppress(httpx.HTTPError):
                        r = await probe.get(f"http://127.0.0.1:{self.port}/api/health")
                        if self._stopping or proc is not self._proc:
                            return
                        if r.status_code == 200:
                            self.client = httpx.AsyncClient(
                                base_url=f"http://127.0.0.1:{self.port}",
                                timeout=httpx.Timeout(None, connect=10.0),
                                limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
                            )
                            self.state = "ready"
                            self._ready.set()
                            self._settled.set()
                            self._retry.clear()  # nothing left for a pending Retry to do
                            p = profiles.get(self.profile_id)
                            if p is not None and not p.get("created"):
                                profiles.mark_created(self.profile_id)
                            return
                await asyncio.sleep(0.3)
        log.error("bot %s never became healthy — restarting it", self.profile_id)
        await _in_thread(self._terminate)


class Pool:
    """A worker per bot profile in the registry."""

    def __init__(self, console_url: str) -> None:
        self.console_url = console_url
        self.token = secrets.token_urlsafe(32)
        self.workers: dict[str, Worker] = {}

    def get(self, profile_id: str | None) -> Worker | None:
        return self.workers.get(profile_id or "")

    def ensure(self, profile_id: str) -> Worker:
        worker = self.workers.get(profile_id)
        if worker is None:
            worker = Worker(profile_id, console_url=self.console_url, token=self.token)
            self.workers[profile_id] = worker
        worker.start()
        return worker

    async def start_all(self) -> None:
        """Bring every bot up — the one the console opens on first, so the window isn't
        waiting behind the others' database checks."""
        first = self.ensure(profiles.active_id())
        await first.wait_settled(30.0)
        for p in profiles.list():
            self.ensure(p["id"])

    async def remove(self, profile_id: str) -> None:
        worker = self.workers.pop(profile_id, None)
        if worker is not None:
            await worker.stop()

    async def stop_all(self) -> None:
        workers = list(self.workers.values())
        self.workers.clear()
        await asyncio.gather(*(w.stop() for w in workers), return_exceptions=True)


# ── upgrading from one bot at a time ──────────────────────────────────────────


def _tunnel_enabled(db: Path) -> bool:
    """Whether a bot's database has remote access turned on (read-only, stdlib sqlite)."""
    if not db.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        row = con.execute("SELECT tunnel_enabled FROM app_config WHERE id = 1").fetchone()
        return bool(row and row[0])
    except sqlite3.Error:
        return False
    finally:
        con.close()


def adopt_shared_tailscale() -> str | None:
    """Hand the install's Tailscale node to the bot that was actually using it.

    When bots ran one at a time they shared one node (``home_dir()/tailscale``), which is the
    original bot's directory. Now each bot has its own, and two bots can't run one node at
    once. If the original bot never turned remote access on but exactly one other bot did, that
    node is the other bot's web address — its OAuth redirect is registered against it — so move
    it into that bot's directory rather than let the bot come up as a new device. Returns the
    bot it went to, if any. Runs before any bot starts, so nothing holds the node open."""
    shared = home_dir() / "tailscale"
    if not shared.is_dir():
        return None
    bots = profiles.list()
    legacy = next((p["id"] for p in bots if p.get("legacy")), None)
    if legacy and _tunnel_enabled(profiles.db_path_for(legacy)):
        return None  # the original bot's own, as it always was
    candidates = [
        p["id"] for p in bots
        if not p.get("legacy")
        and _tunnel_enabled(profiles.db_path_for(p["id"]))
        and not (profiles.data_dir_for(p["id"]) / "tailscale").exists()
    ]
    if len(candidates) != 1:
        return None  # nobody, or no way to tell whose: each gets a node of its own
    target = profiles.data_dir_for(candidates[0]) / "tailscale"
    try:
        shutil.move(str(shared), str(target))
    except OSError as exc:
        log.warning("couldn't hand the Tailscale node to bot %s: %s", candidates[0], exc)
        return None
    log.info("moved the shared Tailscale node to bot %s, which was using it", candidates[0])
    return candidates[0]


# ── forwarding ─────────────────────────────────────────────────────────────────


def _foreign_origin(request: Request) -> bool:
    """A browser request that changes something, sent by a page that isn't this console.

    Any website the operator has open can send a simple POST to 127.0.0.1 (``no-cors``), and a
    body-less one — reset a bot, reconnect the server's bot — gets through without CORS ever being
    asked. Browsers stamp those with the page's Origin, and the console's own pages are always
    on loopback, so anything else is refused before it reaches a bot. Requests with no Origin
    (the desktop shell, curl) aren't from a web page and pass."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return False
    host = (urlsplit(origin).hostname or "").lower()
    return host not in LOOPBACK


def require_console_origin(request: Request) -> None:
    if _foreign_origin(request):
        raise HTTPException(status_code=403, detail="not from this console")


def _request_headers(request: Request) -> list[tuple[str, str]]:
    """The client's headers, as the worker should see them.

    Host passes through untouched: the worker builds its OAuth redirect from it. Nothing is
    added for a loopback client, so the worker's loopback-only routes treat the operator at
    this machine exactly as before; anyone else is marked with X-Forwarded-For, which those
    routes refuse."""
    headers = [
        (k, v) for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "content-length"
    ]
    client = request.client.host if request.client else ""
    if client not in LOOPBACK:
        headers.append(("x-forwarded-for", client or "unknown"))
    return headers


async def _await_or_disconnect(request: Request, task: asyncio.Task):
    """Wait for the worker's answer, abandoning it if the browser goes away first. Closing
    our connection is how the worker learns the operator cancelled (a marketplace publish
    stops its security review and never ships when that happens)."""
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.5)
        if done:
            return task.result()
        if await request.is_disconnected():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            return None


async def forward(request: Request, worker: Worker) -> Response:
    """Send one request to ``worker`` and stream its answer back unchanged."""
    client = worker.client  # once: a restart swaps it out from under a request in flight
    if client is None:
        return JSONResponse({"detail": "This bot is still starting. Try again in a moment."}, status_code=503)
    body = await request.body()
    # The path as the browser sent it, still percent-encoded, so an escaped "/" in an
    # extension key stays one path segment.
    raw_path = request.scope.get("raw_path") or request.url.path.encode()
    query = request.scope.get("query_string") or b""
    target = raw_path.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")
    upstream = client.build_request(
        request.method, target, headers=_request_headers(request), content=body,
    )
    try:
        task = asyncio.create_task(client.send(upstream, stream=True))
        resp = await _await_or_disconnect(request, task)
    except (httpx.HTTPError, RuntimeError):  # RuntimeError: the client closed as the bot restarted
        log.warning("bot %s didn't answer %s %s", worker.profile_id, request.method, request.url.path)
        return JSONResponse({"detail": "This bot's backend isn't responding."}, status_code=502)
    if resp is None:
        return Response(status_code=499)

    async def relay():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    out = StreamingResponse(relay(), status_code=resp.status_code)
    # Raw header pairs, so repeated ones (several Set-Cookie) survive intact.
    out.raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in resp.headers.multi_items()
        if k.lower() not in _HOP_BY_HOP
    ]
    if request.url.path in _ROUTE_START:
        out.raw_headers.append((
            b"set-cookie",
            f"{ROUTE_COOKIE}={worker.profile_id}; Max-Age=900; Path=/; HttpOnly; SameSite=Lax".encode(),
        ))
    return out


async def internal(worker: Worker, method: str, path: str, *, json: dict | None = None,
                   timeout: float | None = 10.0) -> dict:
    """Call one of a worker's gateway-only routes (``/api/instance``) or its own API."""
    client = worker.client
    if client is None:
        raise HTTPException(status_code=503, detail="that bot is still starting")
    try:
        r = await client.request(
            method, path, json=json, headers={INTERNAL_HEADER: worker.token},
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"that bot didn't answer ({exc.__class__.__name__})")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(status_code=r.status_code, detail=detail or "that bot refused the request")
    return data if isinstance(data, dict) else {}


# ── the app ────────────────────────────────────────────────────────────────────


class CreateIn(BaseModel):
    name: str = ""


class IdIn(BaseModel):
    id: str


class RenameIn(BaseModel):
    id: str
    name: str


class MoveIn(BaseModel):
    target: str            # 'local' | 'server'
    host: str | None = ""
    user: str | None = "ubuntu"


class ShareIn(BaseModel):
    from_id: str
    to_id: str | None = None


def _bots_router(pool: Pool) -> APIRouter:
    router = APIRouter(
        prefix="/api/bots", tags=["bots"],
        dependencies=[Depends(require_local_request), Depends(require_console_origin)],
    )

    def need(profile_id: str) -> Worker:
        if profiles.get(profile_id) is None:
            raise HTTPException(status_code=404, detail="unknown bot")
        worker = pool.get(profile_id)
        if worker is None:
            raise HTTPException(status_code=503, detail="that bot isn't running")
        return worker

    async def view(p: dict) -> dict:
        worker = pool.get(p["id"])
        out = {
            "id": p["id"],
            "name": p.get("name") or p["id"],
            "created": bool(p.get("created")),
            "created_at": p.get("created_at"),
            "state": worker.state if worker else "stopped",
        }
        if worker is not None and worker.ready:
            with contextlib.suppress(HTTPException):
                out.update(await internal(worker, "GET", "/api/instance", timeout=4.0))
        if worker is not None and worker.state == "failed":
            out["log"] = "\n".join(worker.tail)
        out.pop("profile_id", None)
        return out

    @router.get("")
    async def list_bots() -> dict:
        items = profiles.list()
        views = await asyncio.gather(*(view(p) for p in items))
        return {"profiles": views, "active_id": profiles.active_id(), "default_id": profiles.default_id()}

    @router.get("/active")
    async def active_bot() -> dict:
        p = profiles.active()
        return {**(await view(p)), "active_id": p["id"]}

    @router.post("")
    async def create_bot(body: CreateIn) -> dict:
        """Register a new, unconfigured bot and start its process (which builds its DB)."""
        p = profiles.create(body.name)
        pool.ensure(p["id"])
        return await view(p)

    @router.post("/switch")
    async def switch_bot(body: IdIn) -> dict:
        """Point the console at another bot. Nothing stops or restarts — every bot keeps
        running; the console reloads onto this one."""
        if profiles.get(body.id) is None:
            raise HTTPException(status_code=404, detail="unknown bot")
        profiles.set_active(body.id)
        worker = pool.ensure(body.id)
        await worker.wait_settled(20.0)
        return {"ok": True, **(await view(profiles.active()))}

    @router.post("/rename")
    async def rename_bot(body: RenameIn) -> dict:
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="a name is required")
        if profiles.get(body.id) is None:
            raise HTTPException(status_code=404, detail="unknown bot")
        profiles.rename(body.id, name)
        return {"ok": True}

    @router.post("/default")
    async def default_bot(body: IdIn) -> dict:
        """Pin which bot the console opens on at launch."""
        try:
            profiles.set_default(body.id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"ok": True, "default_id": profiles.default_id()}

    @router.post("/{profile_id}/restart")
    async def restart_bot(profile_id: str) -> dict:
        """Start a bot's process again now, instead of waiting out its restart backoff."""
        worker = need(profile_id)
        if worker.ready:
            await pool.remove(profile_id)
            worker = pool.ensure(profile_id)
        else:
            worker.retry()
        return {"ok": True, "state": worker.state}

    @router.post("/{profile_id}/reset")
    async def reset_bot(profile_id: str) -> dict:
        """Clear a bot's Discord credentials, API keys and hosting setup (keeps its learned
        data + SSH key). Returns ``{active, hosting_mode}`` so the console can route a reset
        server bot to Reconnect and a local one to the setup wizard."""
        r = await internal(need(profile_id), "POST", "/api/instance/reset", timeout=60.0)
        return {"ok": True, "active": profile_id == profiles.active_id(), "hosting_mode": r.get("hosting_mode") or "local"}

    @router.post("/{profile_id}/move")
    async def move_bot(profile_id: str, body: MoveIn) -> dict:
        """Move a bot between hosts (local ↔ cloud VM). Long-running — no timeout. Any bot
        can move, not just the one on screen: each moves inside its own process."""
        return await internal(
            need(profile_id), "POST", "/api/instance/move", json=body.model_dump(), timeout=None,
        )

    @router.get("/{profile_id}/pubkey")
    async def bot_pubkey(profile_id: str) -> dict:
        """A bot's own SSH public key — for moving a bot that isn't the one on screen."""
        return await internal(need(profile_id), "GET", "/api/server/pubkey", timeout=30.0)

    @router.post("/share-server")
    async def share_server(body: ShareIn) -> dict:
        """Get a bot ready to deploy onto the VM another bot already runs on, so the operator
        doesn't set up a server twice: that bot lets the new bot's SSH key in, and hands back
        the host and what the new install should reuse (Tailscale key, who may sign in)."""
        to_id = body.to_id or profiles.active_id()
        if body.from_id == to_id:
            raise HTTPException(status_code=400, detail="pick a different bot's server")
        source, target = need(body.from_id), need(to_id)
        key = await internal(target, "GET", "/api/server/pubkey", timeout=30.0)
        granted = await internal(
            source, "POST", "/api/instance/authorize-key",
            json={"pubkey": key.get("public_key", "")}, timeout=90.0,
        )
        if not granted.get("ok"):
            return {"ok": False, "error": granted.get("error") or "Couldn't add this bot's key to that server."}
        return await internal(source, "GET", "/api/instance/server", timeout=90.0)

    @router.delete("/{profile_id}")
    async def delete_bot(profile_id: str) -> dict:
        """Stop a bot and delete it and its data. Refuses the bot on screen and the last one."""
        p = profiles.get(profile_id)
        if p is None:
            raise HTTPException(status_code=404, detail="unknown bot")
        if profile_id == profiles.active_id():
            raise HTTPException(status_code=409, detail="switch to another bot before deleting this one")
        if len(profiles.list()) <= 1:
            raise HTTPException(status_code=409, detail="cannot delete the only bot")
        await pool.remove(profile_id)
        try:
            profiles.delete(profile_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"ok": True}

    return router


def create_app(pool: Pool) -> FastAPI:
    app = FastAPI(title="Olisar Gateway")
    app.state.pool = pool
    # Same rule as the bot's own API: admit the dev Vite server on any loopback port. Its
    # headers replace (not duplicate) the ones a forwarded response already carries.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(_bots_router(pool))

    def target(request: Request) -> Worker | None:
        if request.url.path in _ROUTE_CALLBACK:
            pinned = pool.get(request.cookies.get(ROUTE_COOKIE))
            if pinned is not None:
                return pinned
        return pool.get(profiles.active_id())

    @app.get("/api/health")
    async def health(request: Request) -> Response:
        """The bot on screen's health. 503 while it starts, so the desktop shell waits for it
        before opening the window; a bot that keeps failing answers anyway, so the window opens
        and the console can say what's wrong (and offer the other bots)."""
        worker = target(request)
        if worker is not None and worker.ready:
            return await forward(request, worker)
        if worker is not None and worker.state == "failed":
            return JSONResponse({"ok": True, "bot_failed": True, "vec": None})
        return JSONResponse({"ok": False, "starting": True}, status_code=503)

    @app.api_route("/api/instance", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.api_route("/api/instance/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def not_forwarded(rest: str = "") -> Response:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

    @app.api_route("/api/{rest:path}", methods=methods)
    @app.api_route("/auth/{rest:path}", methods=methods)
    async def to_bot(request: Request, rest: str = "") -> Response:
        if _foreign_origin(request):
            return JSONResponse({"detail": "not from this console"}, status_code=403)
        worker = target(request)
        if worker is None:
            return JSONResponse({"detail": "No bot is selected."}, status_code=503)
        if worker.state == "failed":  # no point holding the request for a bot that keeps failing
            return JSONResponse({"detail": "This bot couldn't start."}, status_code=503)
        if not await worker.wait_ready(60.0):
            return JSONResponse({"detail": "This bot is still starting. Try again in a moment."}, status_code=503)
        return await forward(request, worker)

    # The dashboard itself, as the bot's own API serves it.
    dist = web_dist_dir()
    if dist.is_dir():
        app.mount("/", ConsoleFiles(directory=str(dist), html=True), name="spa")
    return app


async def run(host: str, port: int) -> None:
    """Serve the console and run every bot until SIGINT/SIGTERM, then stop them all."""
    profiles.set_active(profiles.default_id())  # the console opens on the launch default
    adopt_shared_tailscale()

    # Each bot logs its own requests; the gateway's copy of every line would only double them.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    pool = Pool(console_url=f"http://127.0.0.1:{port}")
    app = create_app(pool)
    config = uvicorn.Config(app, host=host, port=port, loop="asyncio", log_config=None, access_log=False)
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # SIGTERM is absent on Windows
            loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))

    # The desktop shell holds our stdin open and closes it to stop us — the one graceful
    # signal Windows has — and if the shell dies, the pipe closing stops us all the same.
    if os.environ.get("OLISAR_PARENT_PIPE"):
        def watch_parent() -> None:
            with contextlib.suppress(Exception):
                fd = sys.stdin.fileno()
                while os.read(fd, 4096):
                    pass
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        threading.Thread(target=watch_parent, name="olisar-shell-watch", daemon=True).start()

    starting = asyncio.create_task(pool.start_all(), name="olisar-start-bots")
    log.info("gateway listening on http://%s:%d", host, port)
    try:
        await server.serve()
    finally:
        starting.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await starting
        await pool.stop_all()
