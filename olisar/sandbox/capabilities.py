"""Host-side capability dispatch for the extension sandbox.

Every ``host.*`` call from sandboxed JS lands here as ``dispatch(inv, cap, method,
args)``. We run on the **main asyncio loop** (the runner bridges the worker thread
here), so DB sessions and httpx behave normally.

Two invariants:
* **Nothing runs without permission.** Each method checks ``inv.permissions`` first;
  an ungranted call raises, which the engine turns into a JS exception the author can
  catch — matching the existing "tool degrades politely" convention.
* **No ambient reach.** ``fetch`` blocks loopback/private/link-local targets (SSRF),
  caps size/time/method; secrets resolve by *reference* only; KB/glossary writes are
  idempotent and scoped to the invocation's guild.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar import runtime_keys
from olisar.db.models import ExtensionKV, ExtensionState, KBSource, KBSourceType, KBStatus, utcnow
from olisar.memory.facts import upsert_facts

log = logging.getLogger("olisar.sandbox.capabilities")

# host.fetch limits.
_FETCH_TIMEOUT = 15.0
_FETCH_MAX_BYTES = 5 * 1024 * 1024
_FETCH_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
_FETCH_MAX_CALLS = 30  # per invocation

# Secret refs an extension may request (permission "secret:<ref>"). Reference only —
# the author never sees the value at authoring time, only at runtime if granted.
_SECRET_GETTERS = {
    "uex_api_key": runtime_keys.uex_api_key,
    "gemini_api_key": runtime_keys.gemini_api_key,
    "cloudflare_account_id": runtime_keys.cloudflare_account_id,
    "cloudflare_api_token": runtime_keys.cloudflare_api_token,
}


class DiscordBridge(Protocol):
    """Implemented by the slash-command cog for the lifetime of one interaction."""
    async def reply(self, payload: Any) -> None: ...
    async def follow_up(self, payload: Any) -> None: ...
    async def modal(self, spec: Any) -> dict: ...
    async def await_component(self, opts: Any) -> dict: ...
    # Persistent-component handlers (button/select clicks) only:
    async def update(self, payload: Any) -> None: ...
    async def defer_update(self) -> None: ...


@dataclass
class Invocation:
    """Everything a capability needs for one handler run."""
    ext_key: str
    permissions: set[str]
    guild_id: int
    session: AsyncSession | None = None
    discord: DiscordBridge | None = None
    fetch_calls: int = field(default=0)
    # First-party (built-in or locally-authored) vs. third-party (imported/marketplace).
    # Third-party code is barred from the host's configured secrets regardless of grants.
    trusted: bool = False


class PermissionError_(Exception):
    pass


def _require(inv: Invocation, perm: str) -> None:
    if perm not in inv.permissions:
        raise PermissionError_(
            f"this extension isn't allowed to use '{perm}' — add it to the permissions list."
        )


async def dispatch(inv: Invocation, cap: str, method: str, args: list) -> Any:
    args = args or []
    if cap == "log":
        log.info("ext[%s]: %s", inv.ext_key, args[0] if args else "")
        return None
    if cap == "fetch":
        return await _fetch(inv, *args)
    if cap == "secret":
        return await _secret(inv, args[0] if args else "")
    if cap == "kv":
        return await _kv(inv, method, args)
    if cap == "settings":
        return await _settings(inv, method, args)
    if cap == "kb":
        return await _kb_add_source(inv, args[0] if args else {})
    if cap == "glossary":
        return await _glossary_add(inv, args[0] if args else {})
    if cap == "discord":
        return await _discord(inv, method, args)
    raise ValueError(f"unknown capability: {cap}")


# ── fetch (with SSRF guard) ──────────────────────────────────────────────────
def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return False
    return True


async def _fetch(inv: Invocation, url: str, init: dict | None = None) -> dict:
    _require(inv, "fetch")
    init = init or {}
    inv.fetch_calls += 1
    if inv.fetch_calls > _FETCH_MAX_CALLS:
        raise RuntimeError("too many network calls in one run")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are allowed")
    if not parts.hostname or not _is_public_host(parts.hostname):
        raise ValueError("that host isn't allowed (private/loopback addresses are blocked)")
    method = str(init.get("method") or "GET").upper()
    if method not in _FETCH_METHODS:
        raise ValueError(f"method {method} not allowed")
    headers = {str(k): str(v) for k, v in (init.get("headers") or {}).items()}
    body = init.get("body")
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True, max_redirects=5) as c:
        resp = await c.request(method, url, headers=headers, content=body)
        raw = resp.content[:_FETCH_MAX_BYTES]
        return {
            "status": resp.status_code,
            "headers": {k.lower(): v for k, v in resp.headers.items()},
            "body": raw.decode(resp.encoding or "utf-8", errors="replace"),
        }


# ── secret (by reference) ────────────────────────────────────────────────────
async def _secret(inv: Invocation, ref: str) -> str | None:
    _require(inv, f"secret:{ref}")
    getter = _SECRET_GETTERS.get(ref)
    if getter is None:
        raise ValueError(f"unknown secret reference: {ref}")
    # Host-configured keys (Gemini/Cloudflare/UEX) are infrastructure the operator pays
    # for. First-party extensions (built-in or locally-authored) may use them once granted;
    # imported/marketplace code must NOT — even if the operator clicked "grant" at install —
    # so a third-party extension can never exfiltrate the host's keys. (A per-extension
    # secret vault for third-party code can come later; for now they get none.)
    if not inv.trusted:
        raise PermissionError_(
            f"'{ref}' is a host secret — imported or marketplace extensions can't use it"
        )
    return (await getter()) or None


# ── kv (per extension + guild) ───────────────────────────────────────────────
async def _kv(inv: Invocation, method: str, args: list) -> Any:
    _require(inv, "kv")
    if inv.session is None:
        raise RuntimeError("no storage available in this context")
    key = str(args[0]) if args else ""
    pk = (inv.ext_key, inv.guild_id, key)
    if method == "get":
        row = await inv.session.get(ExtensionKV, pk)
        return row.v if row is not None else None
    if method == "set":
        value = args[1] if len(args) > 1 else None
        row = await inv.session.get(ExtensionKV, pk)
        if row is None:
            inv.session.add(ExtensionKV(ext_key=inv.ext_key, guild_id=inv.guild_id, k=key, v=value))
        else:
            row.v = value
            row.updated_at = utcnow()
        return None
    if method == "delete":
        await inv.session.execute(
            sa_delete(ExtensionKV).where(
                ExtensionKV.ext_key == inv.ext_key,
                ExtensionKV.guild_id == inv.guild_id,
                ExtensionKV.k == key,
            )
        )
        return None
    raise ValueError(f"unknown kv method: {method}")


# ── settings (read-only view of the extension's per-guild config) ────────────
async def _settings(inv: Invocation, method: str, args: list) -> Any:
    """Read what an admin entered in this extension's settings pane (stored per-guild
    in ExtensionState.settings). Read-only and ungated — it's the extension's own
    operator-provided config. ``get()`` returns the whole object; ``get(key)`` one value."""
    if method != "get":
        raise ValueError(f"unknown settings method: {method}")
    if inv.session is None:
        raise RuntimeError("no settings available in this context")
    row = await inv.session.get(ExtensionState, (inv.guild_id, inv.ext_key))
    settings = (row.settings if row is not None else None) or {}
    if args and args[0] is not None:
        return settings.get(str(args[0]))
    return settings


# ── knowledge base + glossary (idempotent, guild-scoped) ─────────────────────
async def _kb_add_source(inv: Invocation, seed: dict) -> bool:
    _require(inv, "kb.write")
    if inv.session is None:
        raise RuntimeError("no storage available in this context")
    uri = str(seed.get("uri") or "").strip()
    if not uri:
        raise ValueError("kb.addSource needs a uri")
    existing = await inv.session.scalar(
        select(KBSource).where(KBSource.guild_id == inv.guild_id, KBSource.uri == uri)
    )
    if existing is not None:
        return False  # idempotent — already queued/ingested
    kind = str(seed.get("type") or "url").lower()
    kb_type = KBSourceType.website if kind == "website" else KBSourceType.url
    inv.session.add(KBSource(
        guild_id=inv.guild_id, type=kb_type, uri=uri,
        title=str(seed.get("title") or uri)[:200], status=KBStatus.pending,
    ))
    log.info("ext[%s] queued KB source %s for guild %s", inv.ext_key, uri, inv.guild_id)
    return True


async def _glossary_add(inv: Invocation, item: dict) -> int:
    _require(inv, "glossary.write")
    if inv.session is None:
        raise RuntimeError("no storage available in this context")
    return await upsert_facts(
        inv.session, guild_id=inv.guild_id, channel_id=None,
        items=[{"subject": item.get("subject"), "fact": item.get("fact")}],
    )


# ── discord (slash-command flows) ────────────────────────────────────────────
async def _discord(inv: Invocation, method: str, args: list) -> Any:
    if inv.discord is None:
        raise RuntimeError("Discord actions aren't available here")
    payload = args[0] if args else {}
    if method == "reply":
        _require(inv, "discord.reply")
        return await inv.discord.reply(payload)
    if method == "followUp":
        _require(inv, "discord.reply")
        return await inv.discord.follow_up(payload)
    if method == "modal":
        _require(inv, "discord.modal")
        return await inv.discord.modal(payload)
    if method == "awaitComponent":
        _require(inv, "discord.components")
        return await inv.discord.await_component(payload)
    if method == "update":  # edit the source message of a persistent component
        _require(inv, "discord.components")
        return await inv.discord.update(payload)
    if method == "deferUpdate":  # ack a component click with no visible change
        _require(inv, "discord.components")
        return await inv.discord.defer_update()
    raise ValueError(f"unknown discord method: {method}")
