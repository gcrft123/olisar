"""The tool PIN: a 4-digit code that has to be entered in Discord before Olisar runs a
gated tool call.

Four parts live here, all Discord-agnostic so the bot, the API and the tests share them:

* **the secret** — stored as a salted scrypt hash in the single-row ``tool_pin`` table, set
  and changed from the console. A 4-digit PIN is trivially brute-forced by anyone holding
  the database, so the hash is not the defence; what it buys is that the PIN never sits in
  a backup, a log line, or an API response in a form anyone can read off.
* **the gate** — :func:`gate` decides whether a tool call has to be confirmed. Each
  server picks the actions that need the PIN on the console's Access page
  (``GuildConfig.pin_actions``); ``OLISAR_PIN_GATED_TOOLS`` can gate any single tool on
  top of that, for driving the flow in testing.
* **the lockout** — :func:`lockout` stops the prompts once too many wrong PINs have been
  entered within an hour, by one person or by everyone together. A prompt's own three
  tries start over with every prompt, so without it a member could keep asking again and
  guess the four digits three at a time. Changing the PIN lifts it.
* **the refusal** — :func:`denial_note` is what the model reads back when the PIN never
  arrived. It is phrased the way a coding agent is told a tool call was denied, because
  that is the behaviour we want: say plainly that it didn't run, don't retry it, carry on
  with whatever the rest of the answer can be.

Verification is deliberately *not* on the reply path's session: the bot verifies from the
interaction handler with its own session, so a reply that's parked waiting on a human isn't
holding a write transaction open while it waits (see :func:`olisar.tools.execute_tool`).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.db.models import GuildConfig, ToolPin, ToolPinFailure, utcnow

log = logging.getLogger("olisar.toolpin")

PIN_LENGTH = 4
# Tries allowed on one prompt before it's refused outright. Three is the cash-machine
# convention. It resets with every prompt, so on its own it doesn't stop anyone asking
# again for three more; the counts below do.
MAX_ATTEMPTS = 3
# Wrong entries allowed across prompts, within LOCK_WINDOW, before prompts stop being
# posted: per person, and for the whole install (one person with several accounts).
LOCK_WINDOW = timedelta(hours=1)
USER_LOCK_AFTER = 5
INSTALL_LOCK_AFTER = 15
DEFAULT_TIMEOUT_SEC = 120
MIN_TIMEOUT_SEC = 15
MAX_TIMEOUT_SEC = 900

# What a server can put behind the PIN, and the tools each one covers. An action rather
# than a tool is what the operator decides about, and what one PIN entry confirms: Olisar
# changing its own settings is two tools, and nobody should have to know that to turn it on.
# tests/test_tool_pin.py fails if a settings write tool is added without being listed here.
ACTIONS: dict[str, frozenset[str]] = {
    "self_edit": frozenset({"change_setting", "settings_action"}),
}
# What a server has before anyone chooses. Self-edit is on: someone rewriting the system
# prompt from chat is what the PIN was built to stop, so it doesn't start out open.
# GuildConfig.pin_actions carries the same default for the rows it creates.
DEFAULT_ACTIONS = ("self_edit",)
_ACTION_OF = {tool: action for action, tools in ACTIONS.items() for tool in tools}

# scrypt parameters. n=2**14 keeps a verify at ~50ms on the hardware Olisar runs on, which
# is irrelevant to a legitimate entry and ruinous to an offline sweep of 10,000 candidates.
_N, _R, _P, _DKLEN, _SALT_BYTES = 2**14, 8, 1, 32, 16

# What a confirmation request came back with. The Discord layer returns one of these and
# the tool path maps it to either "run it" or a refusal the model can read.
APPROVED = "approved"
TIMEOUT = "timeout"
WRONG = "wrong"
REFUSED = "refused"        # someone with Manage Server said no, rather than letting it lapse
UNAVAILABLE = "unavailable"  # nowhere to ask (no Discord surface, or no PIN is set)
LOCKED = "locked"          # too many wrong PINs lately (see lockout)

_DENIALS = {
    TIMEOUT: (
        "DENIED: the {tool} call was not confirmed — nobody entered the PIN before the "
        "prompt expired."
    ),
    WRONG: (
        "DENIED: the {tool} call was not confirmed — the PIN entered was wrong "
        "{attempts} times."
    ),
    REFUSED: "DENIED: an admin refused the {tool} call.",
    LOCKED: (
        "DENIED: the {tool} call was not confirmed — too many wrong PINs have been entered "
        "lately, so PIN prompts are paused for now."
    ),
    UNAVAILABLE: (
        "DENIED: the {tool} call needs a PIN confirmation, and there is no way to ask for "
        "one here."
    ),
}

_DENIAL_TAIL = (
    " Do not call {tool} again in this reply and do not ask for the PIN yourself. Tell the "
    "user plainly that you didn't run it because it wasn't confirmed, then carry on with "
    "whatever you can answer without it."
)


@dataclass(frozen=True)
class PinState:
    """What the console needs to render the PIN card."""

    is_set: bool
    timeout_sec: int
    updated_at: datetime | None = None


def normalize(pin: object) -> str:
    """The digits of ``pin``, or "" if it isn't exactly ``PIN_LENGTH`` of them.

    Accepts the spacing and separators a human types into a phone-style field ("1 2 3 4",
    "12-34") and rejects everything else, so a 5-digit or alphabetic entry never reaches
    the hash — where it would be indistinguishable from a wrong PIN.
    """
    raw = re.sub(r"[\s\-_.]", "", str(pin or ""))
    return raw if re.fullmatch(rf"\d{{{PIN_LENGTH}}}", raw) else ""


def hash_pin(pin: str) -> str:
    """``scrypt$<n>$<r>$<p>$<salt_b64>$<key_b64>`` — self-describing, so the parameters can
    be raised later without stranding the PINs hashed under the old ones."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "scrypt${}${}${}${}${}".format(
        _N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(key).decode()
    )


def check_pin(pin: str, encoded: str) -> bool:
    """Constant-time check of ``pin`` against a stored hash. False on anything malformed."""
    if not pin or not encoded:
        return False
    try:
        scheme, n, r, p, salt_b64, key_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            pin.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p),
            dklen=len(base64.b64decode(key_b64)),
        )
    except Exception:  # noqa: BLE001 — a corrupt row must read as "wrong PIN", not crash a reply
        log.exception("stored tool PIN is unreadable")
        return False
    return secrets.compare_digest(key, base64.b64decode(key_b64))


async def _row(session: AsyncSession, *, create: bool = False) -> ToolPin | None:
    row = await session.get(ToolPin, 1)
    if row is None and create:
        row = ToolPin(id=1)
        session.add(row)
    return row


async def get_state(session: AsyncSession) -> PinState:
    row = await _row(session)
    if row is None:
        return PinState(is_set=False, timeout_sec=DEFAULT_TIMEOUT_SEC)
    return PinState(
        is_set=bool(row.pin_hash),
        timeout_sec=int(row.timeout_sec or DEFAULT_TIMEOUT_SEC),
        updated_at=row.updated_at,
    )


async def set_pin(session: AsyncSession, pin: str, *, actor: object = "") -> None:
    """Store a new PIN. Raises ``ValueError`` if it isn't four digits."""
    digits = normalize(pin)
    if not digits:
        raise ValueError(f"the PIN has to be exactly {PIN_LENGTH} digits")
    row = await _row(session, create=True)
    row.pin_hash = hash_pin(digits)
    row.set_by = str(actor or "")
    await clear_failures(session)


async def set_timeout(session: AsyncSession, seconds: int) -> int:
    """Clamp and store how long a prompt waits. Returns what was stored."""
    value = max(MIN_TIMEOUT_SEC, min(int(seconds), MAX_TIMEOUT_SEC))
    row = await _row(session, create=True)
    row.timeout_sec = value
    return value


async def clear_pin(session: AsyncSession) -> None:
    row = await _row(session)
    if row is not None:
        row.pin_hash = ""
        row.set_by = ""
    await clear_failures(session)


async def verify(session: AsyncSession, pin: object) -> bool:
    """Whether ``pin`` matches the stored one. False when no PIN is set — an unset PIN
    confirms nothing, rather than confirming everything."""
    row = await _row(session)
    if row is None or not row.pin_hash:
        return False
    return check_pin(normalize(pin), row.pin_hash)


async def record_failure(session: AsyncSession, *, user_id: int, guild_id: int = 0) -> None:
    """Count a wrong PIN entered by ``user_id``, and drop the ones too old to matter."""
    await session.execute(
        delete(ToolPinFailure).where(ToolPinFailure.at < utcnow() - LOCK_WINDOW)
    )
    session.add(ToolPinFailure(user_id=int(user_id), guild_id=int(guild_id or 0)))
    await session.flush()


async def lockout(session: AsyncSession, user_id: int) -> str:
    """Why PIN prompts are refused right now: "install" when everyone together has entered
    ``INSTALL_LOCK_AFTER`` wrong PINs within ``LOCK_WINDOW``, "user" when ``user_id`` alone
    has entered ``USER_LOCK_AFTER``, or "" when neither has. It lifts as entries age out of
    the window, or at once when the PIN is changed or removed."""
    since = utcnow() - LOCK_WINDOW
    recent = select(func.count()).select_from(ToolPinFailure).where(ToolPinFailure.at >= since)
    if (await session.scalar(recent) or 0) >= INSTALL_LOCK_AFTER:
        return "install"
    mine = await session.scalar(recent.where(ToolPinFailure.user_id == int(user_id or 0)))
    return "user" if (mine or 0) >= USER_LOCK_AFTER else ""


async def clear_failures(session: AsyncSession) -> None:
    """Forget every wrong entry. Guesses at a PIN that's been replaced tell nobody anything
    about the new one, so an admin changing it is also how a lockout is lifted early."""
    await session.execute(delete(ToolPinFailure))


def gated_tools() -> frozenset[str]:
    """The tools ``OLISAR_PIN_GATED_TOOLS`` gates on every server, whatever each one chose.

    Empty in every shipped configuration. It exists so the flow can be driven end to end
    against a real Discord server for a tool no action covers.
    """
    raw = getattr(settings, "pin_gated_tools", "") or ""
    return frozenset(t.strip() for t in raw.replace(" ", ",").split(",") if t.strip())


async def server_actions(session: AsyncSession, guild_id: int) -> list[str]:
    """The actions ``guild_id`` has put behind the PIN."""
    cfg = await session.get(GuildConfig, guild_id)
    if cfg is None:
        return list(DEFAULT_ACTIONS)
    return list(cfg.pin_actions or [])


async def gate(session: AsyncSession, guild_id: int, tool_name: str) -> str:
    """What a call to ``tool_name`` has to be confirmed as, or "" if it runs unconfirmed.

    That's the action covering the tool when this server has put the action behind the
    PIN, or the tool's own name when ``OLISAR_PIN_GATED_TOOLS`` names it. The server's
    config is only read for a tool some action covers, so every other call costs nothing.
    """
    action = _ACTION_OF.get(tool_name)
    if action and action in await server_actions(session, guild_id):
        return action
    return tool_name if tool_name in gated_tools() else ""


# How much of a value the prompt quotes. Enough to tell one change from another; a prompt
# is a Discord message, and a whole system prompt wouldn't fit in one.
_QUOTE_CHARS = 200


def _quote(value: object) -> str:
    """``value`` on one line, cut short, in quotes, with Markdown defused so it reads as
    the text it is rather than as formatting around the prompt's own words."""
    text = " ".join(str(value if value is not None else "").split())
    if len(text) > _QUOTE_CHARS:
        text = f"{text[:_QUOTE_CHARS]}… ({len(text):,} chars)"
    return '"' + re.sub(r"([\\*`~|>\[\]])", r"\\\1", text) + '"'


def describe(tool: str, args: dict) -> str:
    """What one gated call would do, in words, for the PIN prompt. The person typing the
    PIN is the one authorizing it, and "run change_setting" doesn't say which setting or
    what it becomes."""
    args = args or {}
    if tool == "change_setting":
        key = _quote(args.get("key") or "")
        value = args.get("value")
        if str(args.get("find") or "").strip():
            return f"replace {_quote(args['find'])} with {_quote(value)} in {key}"
        if str(args.get("append") or "").strip().lower() in ("true", "1", "yes", "on"):
            return f"add {_quote(value)} to the end of {key}"
        if value in (None, ""):
            return f"reset {key} to its default"
        return f"change {key} to {_quote(value)}"
    if tool == "settings_action":
        out = _quote(args.get("action") or "")
        if str(args.get("target") or "").strip():
            out += f" on {_quote(args['target'])}"
        extra = [f"{k} {_quote(args[k])}" for k in ("depth", "pages", "hours") if args.get(k)]
        return ", ".join([out, *extra])
    return ", ".join(f"{k} {_quote(v)}" for k, v in args.items()) or "no arguments"


def denial_note(tool: str, outcome: str, *, attempts: int = MAX_ATTEMPTS) -> str:
    """What the model is told when a gated call wasn't confirmed."""
    head = _DENIALS.get(outcome, _DENIALS[REFUSED])
    return (head + _DENIAL_TAIL).format(tool=tool, attempts=attempts)
