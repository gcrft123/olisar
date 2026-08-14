"""HTTP client for the arena instance's admin console API.

Everything the console UI can change, the agent changes through here — persona, tone,
proactivity, channel modes, command replies, model choice — because that is the surface a
real operator has, and a finding that requires editing SQLite by hand is not a finding an
operator could ever act on.

Sessions are minted directly against the arena database (see ``_mint.py``) rather than
walked through Discord OAuth, and cached until they stop working. A 401 triggers exactly
one re-mint and retry; a second failure is reported, because the alternative is a client
that spins on a genuinely broken instance.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from arena.config import REPO_ROOT, ArenaConfig

log = logging.getLogger("arena.dashboard")


class DashboardError(RuntimeError):
    def __init__(self, status: int, body: str, path: str) -> None:
        super().__init__(f"{status} on {path}: {body[:400]}")
        self.status = status
        self.body = body


def _session_path(cfg: ArenaConfig) -> Path:
    return cfg.data_dir / "console_session.json"


def mint_session(cfg: ArenaConfig) -> str:
    """Create a fresh console session, returning the signed cookie value."""
    cfg.require("operator_id", "guild_id")
    proc = subprocess.run(
        [sys.executable, "-m", "arena.control._mint"],
        cwd=REPO_ROOT,
        env=cfg.child_env(),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if proc.returncode != 0:
        raise DashboardError(500, proc.stderr or proc.stdout, "mint-session")
    try:
        cookie = json.loads(proc.stdout.strip().splitlines()[-1])["cookie"]
    except (ValueError, KeyError, IndexError) as exc:
        raise DashboardError(500, f"unparseable mint output: {proc.stdout[:300]}", "mint") from exc
    path = _session_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookie": cookie}), encoding="utf-8")
    return cookie


def _cached_cookie(cfg: ArenaConfig) -> str:
    try:
        return json.loads(_session_path(cfg).read_text(encoding="utf-8")).get("cookie", "")
    except (OSError, ValueError, AttributeError):
        return ""


class Dashboard:
    """The console API, scoped to the arena guild."""

    def __init__(self, cfg: ArenaConfig) -> None:
        self._cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self._cookie = ""

    async def __aenter__(self) -> "Dashboard":
        self._cookie = _cached_cookie(self._cfg) or mint_session(self._cfg)
        self._client = httpx.AsyncClient(
            base_url=self._cfg.api_base,
            timeout=httpx.Timeout(120.0),  # a sandbox-chat reply can run tools first
            headers={"X-Guild-Id": str(self._cfg.guild_id)},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, method: str, path: str, *, retry_auth: bool = True, **kw: Any) -> Any:
        if self._client is None:
            raise RuntimeError("Dashboard used outside its async context")
        response = await self._client.request(
            method, path, cookies={"olisar_session": self._cookie}, **kw
        )
        if response.status_code in (401, 403) and retry_auth:
            log.info("console session rejected (%s) — re-minting", response.status_code)
            self._cookie = mint_session(self._cfg)
            return await self._call(method, path, retry_auth=False, **kw)
        if response.status_code >= 400:
            raise DashboardError(response.status_code, response.text, path)
        if not response.content:
            return None
        return response.json()

    # ── health & observability ────────────────────────────────────────────

    async def health(self) -> dict:
        return await self._call("GET", "/api/health")

    async def stats(self) -> dict:
        return await self._call("GET", "/api/admin/stats")

    async def usage(self) -> dict:
        return await self._call("GET", "/api/usage/summary")

    async def logs(self) -> Any:
        return await self._call("GET", "/api/settings/logs")

    async def audit(self) -> Any:
        return await self._call("GET", "/api/audit")

    # ── the custom-instructions lane ──────────────────────────────────────

    async def get_persona(self) -> dict:
        return await self._call("GET", "/api/admin/persona")

    async def set_persona(self, **fields: Any) -> dict:
        """Update name / system_prompt / tone_notes / desired_bio. This is the operator's
        half of the prompt — the half the research is meant to produce advice about."""
        return await self._call("PUT", "/api/admin/persona", json=fields)

    # ── behaviour knobs ───────────────────────────────────────────────────

    async def get_config(self) -> dict:
        return await self._call("GET", "/api/admin/config")

    async def set_config(self, **fields: Any) -> dict:
        return await self._call("PUT", "/api/admin/config", json=fields)

    async def get_proactivity(self) -> dict:
        return await self._call("GET", "/api/admin/proactivity")

    async def set_proactivity(self, **fields: Any) -> dict:
        return await self._call("PUT", "/api/admin/proactivity", json=fields)

    async def get_channels(self) -> Any:
        return await self._call("GET", "/api/admin/channels")

    async def set_channels(self, body: Any) -> Any:
        return await self._call("PUT", "/api/admin/channels", json=body)

    async def get_messages(self) -> Any:
        return await self._call("GET", "/api/admin/messages")

    async def set_messages(self, body: dict) -> Any:
        return await self._call("PUT", "/api/admin/messages", json=body)

    # ── the fast lane ─────────────────────────────────────────────────────

    async def chat(self, messages: list[dict]) -> str:
        """The console's enclosed test chat: live persona, knowledge base, and tools, but
        no memory read or written and no Discord actions (``generate_sandbox_reply``).

        This is the harness's cheap, deterministic lane. It exercises the prompt without a
        Discord round-trip and without polluting the instance's memory, so a variant can be
        swept over a hundred cases in the time one live scenario takes. What it cannot
        exercise is exactly what it excludes: memory, recall, proactivity, and the Discord
        action tools. Those need the live lane.
        """
        payload = await self._call("POST", "/api/admin/sandbox/chat", json={"messages": messages})
        return (payload or {}).get("reply", "")

    async def ask(self, text: str) -> str:
        """One-shot convenience over :meth:`chat`."""
        return await self.chat([{"role": "user", "content": text}])

    # ── destructive, and deliberately explicit ────────────────────────────

    async def clear_memory(self) -> dict:
        """Wipe everything the arena instance has *learned* (memory, summaries, search
        index, facts, knowledge base) while keeping persona and behaviour. Run between
        experiments so one scenario's memories can't confound the next one's scores."""
        return await self._call("POST", "/api/admin/clear-memory")


async def wait_until_healthy(cfg: ArenaConfig, timeout: float = 90.0) -> dict:
    """Poll ``/api/health`` until the instance answers. Returns the health payload.

    Used after every start and restart: the process exists well before the API binds and
    the bot finishes connecting, and a scenario that starts posting into that window just
    records Olisar saying nothing.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    async with httpx.AsyncClient(base_url=cfg.api_base, timeout=5.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get("/api/health")
                if response.status_code == 200:
                    return response.json()
            except Exception as exc:  # noqa: BLE001 — connection refused while booting
                last_error = exc
            await asyncio.sleep(1.0)
    raise TimeoutError(
        f"arena instance did not become healthy within {timeout:.0f}s "
        f"({last_error or 'no response'}). Check `arena logs`."
    )
