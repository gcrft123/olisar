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

import base64
import ipaddress
import logging
import re
import socket
import time
from collections import deque
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
from olisar.persona import strip_breaks

log = logging.getLogger("olisar.sandbox.capabilities")

# File / fetch size caps.
# * BASE64 path (host.files.read, FileOut text/contentB64, fetch body text): 20 MB —
#   balances Discord's bot upload limit with sandbox memory (base64 inflates ~33%).
# * BLOB path (host.files.ingest / from, fetch bodyBlobId / responseBlob, FileOut blobId):
#   25 MB — Discord's normal bot attachment ceiling; bytes never enter QuickJS.
MAX_SDK_BASE64_BYTES = 20 * 1024 * 1024
MAX_SDK_BLOB_BYTES = 25 * 1024 * 1024
_MAX_BLOBS_PER_INV = 8
_MAX_BLOB_TOTAL_BYTES = 50 * 1024 * 1024  # sum of host-held blobs for one handler run
_MAX_ATTACHMENT_OPS = 5  # ingest + read calls against slash attachments per run

# host.fetch limits.
_FETCH_TIMEOUT = 15.0
_FETCH_BLOB_TIMEOUT = 90.0  # large bodyBlobId / responseBlob transfers
_FETCH_MAX_BYTES = MAX_SDK_BASE64_BYTES
_FETCH_BLOB_MAX_BYTES = MAX_SDK_BLOB_BYTES
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

# host.discord.send rate limit (per extension, per guild). A sliding window so a
# third-party extension can't blast a channel; generous enough that built-ins (which
# post rarely) never hit it. Runtime guardrail that bounds an opened-up capability.
_SEND_WINDOW_SECONDS = 60.0
_SEND_MAX_PER_WINDOW = 5
_send_history: dict[tuple[str, int], deque[float]] = {}


def _send_rate_ok(ext_key: str, guild_id: int) -> bool:
    """True if this (extension, guild) is under the post limit; records the post if so."""
    now = time.monotonic()
    dq = _send_history.setdefault((ext_key, guild_id), deque())
    while dq and now - dq[0] > _SEND_WINDOW_SECONDS:
        dq.popleft()
    if len(dq) >= _SEND_MAX_PER_WINDOW:
        return False
    dq.append(now)
    return True


class DiscordBridge(Protocol):
    """Implemented by the slash-command cog for the lifetime of one interaction."""
    async def reply(self, payload: Any) -> None: ...
    async def follow_up(self, payload: Any) -> None: ...
    async def modal(self, spec: Any) -> dict: ...
    async def await_component(self, opts: Any) -> dict: ...
    # Persistent-component handlers (button/select clicks) only:
    async def update(self, payload: Any) -> None: ...
    async def defer_update(self) -> None: ...
    # Event handlers only (no interaction to reply to) — post to a channel by id:
    async def send(self, channel_id: str, payload: Any) -> None: ...
    # Slash-command attachment → raw bytes (command bridge only; never JSON-settled):
    async def fetch_attachment_bytes(
        self, option_name: str,
    ) -> tuple[bytes, str, str | None]: ...


@dataclass
class BlobRecord:
    """Host-held file bytes for one handler run. Opaque to the sandbox (only blobId)."""
    data: bytes
    filename: str
    content_type: str | None = None


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
    # Host-side blob store (shared with the Discord bridge for FileOut blobId resolution).
    blobs: dict[str, BlobRecord] = field(default_factory=dict)
    blob_seq: int = 0
    blob_total_bytes: int = 0
    attachment_ops: int = 0


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
    if cap == "files":
        return await _files(inv, method, args)
    if cap == "generate":
        return await _generate(inv, args[0] if args else {})
    raise ValueError(f"unknown capability: {cap}")


# ── host blobs (opaque file handles; never enter QuickJS as bytes) ───────────
def _safe_blob_filename(name: str) -> str:
    base = re.sub(r"[\\/]+", "/", str(name or "file")).rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "file"
    return cleaned[:200]


def store_blob(
    inv: Invocation,
    data: bytes,
    *,
    filename: str = "file",
    content_type: str | None = None,
    max_bytes: int = MAX_SDK_BLOB_BYTES,
) -> dict:
    """Put bytes in the invocation blob store; return JSON-safe metadata + blobId."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("blob data must be bytes")
    data = bytes(data)
    if len(data) > max_bytes:
        raise ValueError(
            f"file too large (max {max_bytes // (1024 * 1024)} MB)"
        )
    if len(inv.blobs) >= _MAX_BLOBS_PER_INV:
        raise RuntimeError(f"too many blobs in one run (max {_MAX_BLOBS_PER_INV})")
    if inv.blob_total_bytes + len(data) > _MAX_BLOB_TOTAL_BYTES:
        raise RuntimeError(
            f"total blob storage for this run exceeds "
            f"{_MAX_BLOB_TOTAL_BYTES // (1024 * 1024)} MB"
        )
    inv.blob_seq += 1
    blob_id = f"b{inv.blob_seq}"
    fname = _safe_blob_filename(filename)
    inv.blobs[blob_id] = BlobRecord(
        data=data, filename=fname, content_type=content_type,
    )
    inv.blob_total_bytes += len(data)
    return {
        "blobId": blob_id,
        "filename": fname,
        "size": len(data),
        "contentType": content_type,
    }


def get_blob(inv: Invocation, blob_id: str) -> BlobRecord:
    rec = inv.blobs.get(str(blob_id or ""))
    if rec is None:
        raise ValueError(f"unknown blobId {blob_id!r}")
    return rec


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

    # Request body: plain string, or host-held blob (no base64 through the sandbox).
    body_blob_id = init.get("bodyBlobId")
    body: bytes | str | None
    if body_blob_id:
        body = get_blob(inv, str(body_blob_id)).data
    else:
        body = init.get("body")

    response_blob = bool(init.get("responseBlob"))
    max_bytes = _FETCH_BLOB_MAX_BYTES if response_blob else _FETCH_MAX_BYTES
    timeout = _FETCH_BLOB_TIMEOUT if (body_blob_id or response_blob) else _FETCH_TIMEOUT

    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, max_redirects=5,
    ) as c:
        async with c.stream(method, url, headers=headers, content=body) as resp:
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"response too large (max {max_bytes // (1024 * 1024)} MB"
                        + ("; use responseBlob: false for smaller text APIs"
                           if response_blob else
                           "; use responseBlob: true to store as a host blob")
                        + ")"
                    )
                chunks.append(chunk)
            raw = b"".join(chunks)
            status = resp.status_code
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            content_type = resp_headers.get("content-type")
            encoding = resp.encoding

    if response_blob:
        # Prefer Content-Disposition filename when present.
        fname = "download"
        cd = resp_headers.get("content-disposition") or ""
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
        if m:
            fname = m.group(1).strip()
        ref = store_blob(
            inv, raw, filename=fname, content_type=content_type,
            max_bytes=_FETCH_BLOB_MAX_BYTES,
        )
        return {
            "status": status,
            "headers": resp_headers,
            "body": "",
            "blobId": ref["blobId"],
            "size": ref["size"],
            "contentType": content_type,
        }

    return {
        "status": status,
        "headers": resp_headers,
        "body": raw.decode(encoding or "utf-8", errors="replace"),
        "blobId": None,
        "size": len(raw),
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
    if method == "send":  # post to a channel from a tool/event handler (no interaction)
        # Operator-grantable for any extension (third-party included); risk is bounded by
        # install-time consent + the no-@mentions guard on the post + this rate limit.
        _require(inv, "discord.send")
        if not _send_rate_ok(inv.ext_key, inv.guild_id):
            return ("I'm posting too frequently right now — that channel post was rate-limited. "
                    "Try again in a moment.")
        channel_id = str(args[0]) if args else ""
        body = args[1] if len(args) > 1 else {}
        return await inv.discord.send(channel_id, body)
    raise ValueError(f"unknown discord method: {method}")


# ── files (slash attachments + host blobs) ───────────────────────────────────
async def _load_attachment(inv: Invocation, option_name: str) -> tuple[bytes, str, str | None]:
    if inv.discord is None:
        raise RuntimeError(
            "file access isn't available here — host.files.read/ingest only work "
            "inside a slash-command handler"
        )
    if not option_name:
        raise ValueError("needs the attachment option name")
    inv.attachment_ops += 1
    if inv.attachment_ops > _MAX_ATTACHMENT_OPS:
        raise RuntimeError(
            f"too many attachment operations in one command (max {_MAX_ATTACHMENT_OPS})"
        )
    return await inv.discord.fetch_attachment_bytes(option_name)


async def _files(inv: Invocation, method: str, args: list) -> Any:
    """File capabilities:

    * ``read(optionName)`` — base64 into the sandbox (≤ 20 MB). Fine for small files.
    * ``ingest(optionName)`` — store on the host as a blobId (≤ 25 MB). Prefer for
      API pipelines so bytes never enter QuickJS.
    * ``from({ name, text|contentB64 })`` — create a host blob from sandbox data.
    """
    if method == "read":
        option_name = str(args[0]) if args else ""
        data, filename, ctype = await _load_attachment(inv, option_name)
        if len(data) > MAX_SDK_BASE64_BYTES:
            raise ValueError(
                f"file too large for host.files.read "
                f"(max {MAX_SDK_BASE64_BYTES // (1024 * 1024)} MB) — "
                "use host.files.ingest(optionName) and pass blobId to fetch/reply"
            )
        return {
            "filename": filename,
            "contentType": ctype,
            "size": len(data),
            "contentB64": base64.b64encode(data).decode("ascii"),
        }
    if method == "ingest":
        option_name = str(args[0]) if args else ""
        data, filename, ctype = await _load_attachment(inv, option_name)
        return store_blob(
            inv, data, filename=filename, content_type=ctype,
            max_bytes=MAX_SDK_BLOB_BYTES,
        )
    if method == "from":
        # Create a blob from sandbox-provided text/base64 (still size-capped).
        spec = args[0] if args else {}
        if not isinstance(spec, dict):
            raise ValueError("host.files.from needs { name, text|contentB64 }")
        name = str(spec.get("name") or "file")
        ctype = spec.get("contentType")
        ctype_s = str(ctype) if ctype is not None else None
        if spec.get("text") is not None:
            data = str(spec["text"]).encode("utf-8")
        elif spec.get("contentB64") is not None:
            try:
                data = base64.b64decode(str(spec["contentB64"]), validate=False)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("invalid contentB64") from exc
        else:
            raise ValueError("host.files.from needs text or contentB64")
        if len(data) > MAX_SDK_BASE64_BYTES:
            raise ValueError(
                f"payload too large for host.files.from "
                f"(max {MAX_SDK_BASE64_BYTES // (1024 * 1024)} MB)"
            )
        return store_blob(
            inv, data, filename=name, content_type=ctype_s,
            max_bytes=MAX_SDK_BASE64_BYTES,
        )
    raise ValueError(f"unknown files method: {method}")


# ── model generation (spends the installing operator's own model quota) ──────
_GENERATE_MAX_TOKENS = 1200  # hard ceiling regardless of what the extension asks for


async def _persona_system(inv: Invocation) -> str:
    """The guild's persona as a system instruction, so generated text stays in character.
    Falls back to the default persona when the guild hasn't customised it."""
    from olisar.db.models import Persona
    from olisar.persona import (
        DEFAULT_PERSONA_NAME,
        DEFAULT_SYSTEM_PROMPT,
        DEFAULT_TONE_NOTES,
        build_system_prompt,
    )

    persona = await inv.session.get(Persona, inv.guild_id) if inv.session is not None else None
    if persona is None:
        return build_system_prompt(
            persona_name=DEFAULT_PERSONA_NAME, system_prompt=DEFAULT_SYSTEM_PROMPT,
            tone_notes=DEFAULT_TONE_NOTES,
        )
    return build_system_prompt(
        persona_name=persona.name, system_prompt=persona.system_prompt,
        tone_notes=persona.tone_notes,
    )


async def _generate(inv: Invocation, opts: dict) -> str:
    """Generate text in the server's persona voice. Spends the *installing operator's* own
    model quota (their Gemini key), so it's operator-grantable for any extension — they
    consent to the cost at install. Bounded by _GENERATE_MAX_TOKENS and the model rate
    limiter. (Unlike host.secret, which stays first-party — it would leak the host's keys.)"""
    _require(inv, "model.generate")
    opts = opts or {}
    task = str(opts.get("task") or "").strip()
    if not task:
        raise ValueError("host.generate needs a task")
    try:
        max_tokens = int(opts.get("maxTokens") or 600)
    except (TypeError, ValueError):
        max_tokens = 600
    max_tokens = max(1, min(max_tokens, _GENERATE_MAX_TOKENS))
    system = await _persona_system(inv)
    note = str(opts.get("systemNote") or "").strip()
    if note:
        system = system + "\n\n── For this generation ──\n" + note

    from google.genai import types

    from olisar.gemini.client import get_gemini

    result = await get_gemini().generate(
        contents=[types.Content(role="user", parts=[types.Part(text=task)])],
        system_instruction=system, max_output_tokens=max_tokens,
        source="extension",
    )
    # Fold the delivery marker before it leaves the host. The generation runs on the
    # persona system prompt, which teaches the model to separate beats with SPLIT_MARKER
    # for bot/replies.py to turn into consecutive messages — but an extension gets a plain
    # string back and has no idea the marker means anything. welcome.js passed one straight
    # to host.discord.send and "[[break]]" appeared verbatim in the server.
    #
    # Folded rather than split because this boundary returns one string: there is nowhere
    # for a second message to go, and an extension may well be putting this text somewhere
    # that cannot be several messages at all — an embed body, a button label, a settings
    # field. A newline is the honest rendering of a beat break in a single string.
    return strip_breaks((result.text or "").strip())
