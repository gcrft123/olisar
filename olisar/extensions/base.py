"""The bot-extension framework.

An *extension* is a togglable package of extra features. Each one is declared in
code (see ``builtin.py``) and registered here; the database only records which are
switched on (``ExtensionState``). The pipeline reads the enabled set live on every
reply, so flipping an extension in the dashboard takes effect on Olisar's next
message — no restart.

An extension can contribute:
* **tools** — function-calling tools the model gains while the extension is on
  (each a ``FunctionDeclaration`` + an async handler ``(args, ctx) -> str``), and/or
* a **system_note** — a line folded into the system prompt that shapes behaviour.

Adding a new feature is self-contained: write a module that builds an ``Extension``
and calls ``register(...)``. Nothing in the core needs to change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import ExtensionState

if TYPE_CHECKING:  # avoid a runtime import cycle with olisar.tools
    from olisar.tools import ToolContext

# A handler receives the parsed tool args and the live ToolContext (DB session,
# guild, Discord actions, …) and returns a short string fed back to the model.
ToolHandler = Callable[[dict, "ToolContext"], Awaitable[str]]


@dataclass(frozen=True)
class ExtensionTool:
    declaration: types.FunctionDeclaration
    handler: ToolHandler


@dataclass(frozen=True)
class Extension:
    key: str  # stable id, used in the DB + API
    name: str
    description: str
    category: str = "General"
    default_enabled: bool = False
    tools: tuple[ExtensionTool, ...] = ()
    system_note: str = ""
    # Optional async hook run once when the extension transitions OFF -> ON (from
    # the API toggle). Use it to seed durable state, e.g. add knowledge-base
    # sources. Signature: (session, guild_id) -> None. Must be idempotent.
    on_enable: "Callable[[AsyncSession, int], Awaitable[None]] | None" = None


_REGISTRY: dict[str, Extension] = {}


def register(ext: Extension) -> Extension:
    """Add an extension to the catalog (call at import time). Raises on dup keys."""
    if ext.key in _REGISTRY:
        raise ValueError(f"duplicate extension key {ext.key!r}")
    _REGISTRY[ext.key] = ext
    return ext


def _user_cache() -> dict[str, Extension]:
    """Last-loaded SDK extensions (lazy import avoids an import cycle)."""
    from olisar.extensions import user_registry

    return user_registry.cached()


async def _catalog(session: AsyncSession) -> dict[str, Extension]:
    """Built-in (Python) extensions merged with the live SDK extensions. A user key
    that matches a built-in is rejected at create time, so built-ins always win."""
    from olisar.extensions import user_registry

    user = await user_registry.load(session)
    return {**user, **_REGISTRY}


def all_extensions() -> list[Extension]:
    merged = {**_user_cache(), **_REGISTRY}
    return sorted(merged.values(), key=lambda e: (e.category, e.name))


def get_extension(key: str) -> Extension | None:
    return _REGISTRY.get(key) or _user_cache().get(key)


async def enabled_keys(session: AsyncSession, guild_id: int) -> set[str]:
    """The keys of extensions enabled for this guild (DB overrides each default)."""
    catalog = await _catalog(session)
    rows = {
        r.key: r.enabled
        for r in (
            await session.scalars(select(ExtensionState).where(ExtensionState.guild_id == guild_id))
        ).all()
    }
    return {e.key for e in catalog.values() if rows.get(e.key, e.default_enabled)}


async def is_enabled(session: AsyncSession, guild_id: int, key: str) -> bool:
    """Whether one extension is on for this guild (DB flag overrides its default)."""
    catalog = await _catalog(session)
    ext = catalog.get(key)
    if ext is None:
        return False
    row = await session.get(ExtensionState, (guild_id, key))
    return row.enabled if row is not None else ext.default_enabled


@dataclass
class GatheredExtensions:
    declarations: list = field(default_factory=list)
    handlers: dict = field(default_factory=dict)  # tool name -> handler
    notes: list = field(default_factory=list)


async def gather_enabled(session: AsyncSession, guild_id: int) -> GatheredExtensions:
    """Collect the tools + system notes from this guild's enabled extensions."""
    catalog = await _catalog(session)
    keys = await enabled_keys(session, guild_id)
    out = GatheredExtensions()
    for ext in catalog.values():
        if ext.key not in keys:
            continue
        for tool in ext.tools:
            out.declarations.append(tool.declaration)
            out.handlers[tool.declaration.name] = tool.handler
        if ext.system_note:
            out.notes.append(ext.system_note)
    return out
