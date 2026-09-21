"""The tool PIN: a 4-digit code that has to be entered in Discord before Olisar runs a
gated tool call.

Three parts live here, all Discord-agnostic so the bot, the API and the tests share them:

* **the secret** — stored as a salted scrypt hash in the single-row ``tool_pin`` table, set
  and changed from the console. A 4-digit PIN is trivially brute-forced by anyone holding
  the database, so the hash is not the defence; what it buys is that the PIN never sits in
  a backup, a log line, or an API response in a form anyone can read off.
* **the gate** — :func:`requires_pin` decides whether a tool call has to be confirmed.
  Today it answers False for everything unless ``OLISAR_PIN_GATED_TOOLS`` names a tool:
  the confirmation machinery ships, the policy that would make it compulsory does not.
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
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.db.models import ToolPin

log = logging.getLogger("olisar.toolpin")

PIN_LENGTH = 4
# Tries allowed on one prompt before it's refused outright. Three is the cash-machine
# convention, and with 10,000 combinations it's the only thing standing between a 4-digit
# secret and someone guessing it in a channel.
MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SEC = 120
MIN_TIMEOUT_SEC = 15
MAX_TIMEOUT_SEC = 900

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


async def verify(session: AsyncSession, pin: object) -> bool:
    """Whether ``pin`` matches the stored one. False when no PIN is set — an unset PIN
    confirms nothing, rather than confirming everything."""
    row = await _row(session)
    if row is None or not row.pin_hash:
        return False
    return check_pin(normalize(pin), row.pin_hash)


def gated_tools() -> frozenset[str]:
    """The tools that require a PIN right now.

    Empty in every shipped configuration. ``OLISAR_PIN_GATED_TOOLS`` (comma-separated tool
    names) exists so the flow can be exercised end to end against a real Discord server
    before any of it is wired to a setting an operator can turn on. When per-tool gating
    becomes a real setting it reads from the database here, and this stays as the override.
    """
    raw = getattr(settings, "pin_gated_tools", "") or ""
    return frozenset(t.strip() for t in raw.replace(" ", ",").split(",") if t.strip())


def requires_pin(tool_name: str) -> bool:
    return tool_name in gated_tools()


def denial_note(tool: str, outcome: str, *, attempts: int = MAX_ATTEMPTS) -> str:
    """What the model is told when a gated call wasn't confirmed."""
    head = _DENIALS.get(outcome, _DENIALS[REFUSED])
    return (head + _DENIAL_TAIL).format(tool=tool, attempts=attempts)
