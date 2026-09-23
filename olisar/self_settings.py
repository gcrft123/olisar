"""Olisar's own console settings, as tools it can use from a conversation.

Covers what the console's Persona, Behavior, Command replies, Knowledge and Members pages
can change. Built to cost next to nothing on the replies that never touch it, which is
almost all of them:

* Only ``open_settings`` is declared up front, and its declaration is short. Calling it
  unlocks ``change_setting`` and ``settings_action`` for the rest of that reply (see
  ``olisar.pipeline._run_tool_loop``), so their declarations are only sent on replies that
  are about settings.
* Key names, ranges and choices live in what ``open_settings`` returns, not in any
  declaration. A change needs a read first anyway, and the read is what teaches the keys.
* A listing cuts long text short, and asking for one key returns it whole.
  ``change_setting`` can swap one passage (``find``) or add to the end (``append``), so
  changing a sentence of the system prompt doesn't mean re-typing all of it.

Who may call these isn't decided here. The writes sit behind the tool PIN on any server
that keeps "self_edit" in its ``pin_actions`` (the default), and ``olisar.toolpin.gate``
checks each call before it reaches this module. ``pin_actions`` itself is deliberately not
one of the keys below: the setting that guards these tools can't be one they change.

Every change is committed as soon as it's made, rather than with the rest of the reply.
The model tells the user "done" from the result string, so the result has to be true
already; and two of the actions (the glossary mines) write through sessions of their own,
which would otherwise wait on this reply's lock.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from google.genai import types
from sqlalchemy import func, select

from olisar.audit import record_audit
from olisar.config import settings
from olisar.db.models import (
    GuildChannelInfo,
    GuildConfig,
    GuildFact,
    KBChunk,
    KBSource,
    Persona,
    ProactivityConfig,
    ProactivityLevel,
    SearchMessage,
    UserProfile,
)
from olisar.gemini.models import RANKED_NAMES
from olisar.knowledge import sources
from olisar.knowledge.refresh import MAX_INTERVAL_HOURS, REFRESHABLE_TYPES
from olisar.messages import DEFAULT_COMMAND_MESSAGES, PLACEHOLDERS
from olisar.persona import SERVER_TYPES

if TYPE_CHECKING:
    from olisar.tools import ToolContext


def _str(desc: str, enum: list[str] | None = None) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc, enum=enum)


SECTIONS = ["persona", "behavior", "replies", "knowledge", "glossary", "members"]

ACTIONS = [
    "kb_add_page", "kb_add_site", "kb_remove", "kb_refresh", "kb_schedule",
    "index_rebuild", "index_clear",
    "glossary_delete", "glossary_mine", "glossary_deep_mine",
    "rebuild_impression",
]

# Declared on every reply, so every word here is paid for on every reply.
READ_DECLARATION = types.FunctionDeclaration(
    name="open_settings",
    description=(
        "View or change your own settings. You CAN edit every one of them from chat when "
        "asked, including your system prompt, style notes, name and bio, behavior, command "
        "replies, knowledge base, glossary and member impressions. Call this first: it "
        "lists each key and its allowed values, and unlocks change_setting and "
        "settings_action."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "section": _str("which page", SECTIONS),
            "filter": _str("optional: a key to show in full, or words to match in glossary/members"),
        },
        required=["section"],
    ),
)

# Declared only once open_settings has run in the same reply.
WRITE_DECLARATIONS = [
    types.FunctionDeclaration(
        name="change_setting",
        description="Change one key from open_settings. Takes effect from your next reply.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "key": _str("exactly as open_settings lists it"),
                "value": _str(
                    "the new value; lists comma-separated; '' resets a reply to its default"
                ),
                "find": _str(
                    "text keys: an exact passage of the current text to replace with "
                    "value, keeping the rest"
                ),
                "append": _str("text keys: 'true' to add value to the end instead"),
            },
            required=["key", "value"],
        ),
    ),
    types.FunctionDeclaration(
        name="settings_action",
        description="Run a Knowledge or Members action. Ids come from open_settings.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "action": _str("what to do", ACTIONS),
                "target": _str(
                    "URL for kb_add_*, source id for other kb_*, fact ids for "
                    "glossary_delete, member name or id for rebuild_impression"
                ),
                "hours": _str("kb_add_*, kb_schedule: re-read every N hours, 0 = never"),
                "depth": _str("kb_add_site: link depth 0-3, default 1"),
                "pages": _str("kb_add_site: page limit 1-100, default 25"),
            },
            required=["action"],
        ),
    ),
]

TOOL_NAMES = frozenset({READ_DECLARATION.name, *(d.name for d in WRITE_DECLARATIONS)})


# ── The keys ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Field:
    table: type
    attr: str
    kind: str  # text | bool | int | float | list | choice | quiet
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    max_len: int = 0


# The bounds match api/schemas.py (PersonaIn, ConfigIn, ProactivityIn), which is what the
# console enforces; tests/test_self_settings.py fails if the two drift apart.
_PERSONA: dict[str, _Field] = {
    "name": _Field(Persona, "name", "text", max_len=64),
    "system_prompt": _Field(Persona, "system_prompt", "text"),
    "tone_notes": _Field(Persona, "tone_notes", "text"),
    # The console's own limit: Discord allows 400, and the attribution line takes the rest.
    "desired_bio": _Field(Persona, "desired_bio", "text", max_len=300),
    "server_type": _Field(
        Persona, "server_type", "choice", choices=("none", *sorted(k for k in SERVER_TYPES if k))
    ),
    "slang_density": _Field(Persona, "slang_density", "int", 0, 3),
}

_BEHAVIOR: dict[str, _Field] = {
    "name_triggers": _Field(GuildConfig, "name_triggers", "list"),
    "name_requires_address": _Field(GuildConfig, "name_requires_address", "bool"),
    "reply_in_dms": _Field(GuildConfig, "reply_in_dms", "bool"),
    "see_other_bots": _Field(GuildConfig, "see_other_bots", "bool"),
    "blocked_mentions": _Field(
        GuildConfig, "blocked_mentions", "list", choices=("everyone", "here", "roles")
    ),
    "default_model": _Field(GuildConfig, "default_model", "choice", choices=tuple(RANKED_NAMES)),
    "grounding_enabled": _Field(GuildConfig, "grounding_enabled", "bool"),
    "grounding_daily_cap": _Field(GuildConfig, "grounding_daily_cap", "int", 0),
    "presence_tools_enabled": _Field(GuildConfig, "presence_tools_enabled", "bool"),
    "silent_acks_enabled": _Field(GuildConfig, "silent_acks_enabled", "bool"),
    "context_message_limit": _Field(GuildConfig, "context_message_limit", "int", 3, 100),
    "summary_token_threshold": _Field(GuildConfig, "summary_token_threshold", "int", 500),
    "glossary_mine_token_threshold": _Field(
        GuildConfig, "glossary_mine_token_threshold", "int", 300
    ),
    "user_persona_msg_threshold": _Field(GuildConfig, "user_persona_msg_threshold", "int", 5),
    "proactivity.enabled": _Field(ProactivityConfig, "enabled", "bool"),
    "proactivity.level": _Field(
        ProactivityConfig, "level", "choice", choices=tuple(lv.value for lv in ProactivityLevel)
    ),
    "proactivity.confidence_threshold": _Field(
        ProactivityConfig, "confidence_threshold", "float", 0, 1
    ),
    "proactivity.global_cooldown_sec": _Field(ProactivityConfig, "global_cooldown_sec", "int", 0),
    "proactivity.channel_cooldown_sec": _Field(
        ProactivityConfig, "channel_cooldown_sec", "int", 0
    ),
    "proactivity.max_per_hour": _Field(ProactivityConfig, "max_per_hour", "int", 0),
    "proactivity.quiet_hours": _Field(ProactivityConfig, "quiet_hours", "quiet"),
    "proactivity.reaction_enabled": _Field(ProactivityConfig, "reaction_enabled", "bool"),
    "proactivity.reaction_threshold": _Field(
        ProactivityConfig, "reaction_threshold", "float", 0, 1
    ),
    "proactivity.reaction_cooldown_sec": _Field(
        ProactivityConfig, "reaction_cooldown_sec", "int", 0
    ),
    "proactivity.reaction_max_per_hour": _Field(
        ProactivityConfig, "reaction_max_per_hour", "int", 0
    ),
}

# Command replies are keyed "reply.<name>" and stored together in one JSON column.
_REPLY = "reply."

_AUDIT = {
    Persona: ("update_persona", "persona"),
    GuildConfig: ("update_config", "guild_config"),
    ProactivityConfig: ("update_proactivity", "proactivity_config"),
}

# Past this a listed text value is cut short. Enough to recognise it by; the whole thing is
# one read away.
_CLIP = 80
_TRUE = {"true", "on", "yes", "1", "enable", "enabled"}
_FALSE = {"false", "off", "no", "0", "disable", "disabled"}


def _field(key: str) -> _Field | None:
    return _PERSONA.get(key) or _BEHAVIOR.get(key)


def _cut(text: str, limit: int = _CLIP) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _clip(text: str, limit: int = _CLIP) -> str:
    """Quoted, and with the full length when it had to be cut."""
    cut = _cut(text, limit)
    return f'"{cut}"' if not cut.endswith("…") else f'"{cut}" ({len(text)} chars)'


def _plain(value: object) -> object:
    """``value`` as something the audit log's JSON column can hold."""
    return getattr(value, "value", value)


def _show(f: _Field, value: object) -> str:
    if f.kind == "text":
        return _clip(value or "")
    if f.kind == "bool":
        return "true" if value else "false"
    if f.kind == "list":
        return ", ".join(value or []) or "none"
    if f.kind == "quiet":
        return f"{value['start']}-{value['end']} UTC" if value and "start" in value else "off"
    if f.kind == "choice":
        return str(_plain(value) or "none")
    return str(value)


def _hint(f: _Field) -> str:
    if f.kind == "list" and f.choices:
        return " [any of " + "|".join(f.choices) + "]"
    if f.choices:
        return " [" + "|".join(f.choices) + "]"
    if f.kind in ("int", "float") and f.lo is not None:
        return f" [{f.lo:g}-{f.hi:g}]" if f.hi is not None else f" [min {f.lo:g}]"
    if f.kind == "quiet":
        return " [start-end hour UTC, or off]"
    return ""


# ── Parsing a value ─────────────────────────────────────────────────────────


def _number(f: _Field, value: str) -> int | float:
    try:
        n = float(value.strip())
    except ValueError:
        raise ValueError("that isn't a number") from None
    if not math.isfinite(n):
        raise ValueError("that isn't a number")
    if f.kind == "int":
        if not n.is_integer():
            raise ValueError("it has to be a whole number")
        n = int(n)
    if (f.lo is not None and n < f.lo) or (f.hi is not None and n > f.hi):
        raise ValueError(f"out of range{_hint(f)}")
    return n


def _items(value: str) -> list[str]:
    raw = value.strip()
    if raw.lower() in ("", "none", "[]"):
        return []
    parts = None
    if raw.startswith("["):
        try:
            parts = [str(x) for x in json.loads(raw)]
        except (ValueError, TypeError):
            parts = None
    if parts is None:
        parts = raw.split(",")
    return list(dict.fromkeys(p.strip() for p in parts if p.strip()))


def _edit_text(current: str, value: str, find: str, append: bool) -> str:
    """The text after one edit: ``find`` swapped for ``value``, ``value`` added to the end,
    or ``value`` outright.

    ``find`` matches across any run of whitespace. The model quotes the passage back from
    the prompt it was given, and a line break it reproduces as a space is still the same
    passage."""
    if find.strip():
        pattern = r"\s+".join(re.escape(word) for word in find.split())
        hits = list(re.finditer(pattern, current))
        if not hits:
            raise ValueError("that passage isn't in the current text")
        if len(hits) > 1:
            raise ValueError(f"that passage appears {len(hits)} times; quote more of it")
        start, end = hits[0].span()
        return (current[:start] + value + current[end:]).strip()
    if append:
        return (current.rstrip() + "\n" + value.strip()).strip()
    return value.strip()


def _parse(f: _Field, value: str, current: object, find: str, append: bool) -> object:
    if f.kind == "text":
        new = _edit_text(current or "", value, find, append)
        if f.max_len and len(new) > f.max_len:
            raise ValueError(f"it's {len(new)} characters and the limit is {f.max_len}")
        if f.attr == "name" and not new:
            raise ValueError("the name can't be empty")
        return new
    if f.kind == "bool":
        word = value.strip().lower()
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        raise ValueError("it has to be true or false")
    if f.kind in ("int", "float"):
        return _number(f, value)
    if f.kind == "list":
        items = _items(value)
        if f.choices:
            items = [i.lower().lstrip("@") for i in items]
            bad = [i for i in items if i not in f.choices]
            if bad:
                raise ValueError(f"{', '.join(bad)} isn't one of {'|'.join(f.choices)}")
        return items
    if f.kind == "choice":
        word = value.strip().lower()
        if f.table is Persona and word in ("", "none"):
            return ""  # server_type: unset, let Olisar read the room
        if word not in f.choices:
            raise ValueError(f"it has to be one of{_hint(f)}")
        return ProactivityLevel(word) if f.table is ProactivityConfig else word
    if f.kind == "quiet":
        word = value.strip().lower()
        if word in ("", "off", "none"):
            return {}
        m = re.fullmatch(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})(?:\s*utc)?", word)
        if not m or int(m[1]) > 23 or int(m[2]) > 23:
            raise ValueError("give it as start-end hours in UTC, e.g. 23-7, or off")
        return {"start": int(m[1]), "end": int(m[2])}
    raise ValueError(f"unhandled kind {f.kind}")


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUE


# ── Reading ─────────────────────────────────────────────────────────────────


async def _rows(ctx: ToolContext, fields: dict[str, _Field]) -> dict[type, object]:
    return {
        table: await ctx.session.get(table, ctx.cfg_guild)
        for table in dict.fromkeys(f.table for f in fields.values())
    }


async def _read_fields(ctx: ToolContext, fields: dict[str, _Field]) -> str:
    rows = await _rows(ctx, fields)
    return "\n".join(
        f"{key}={_show(f, getattr(rows[f.table], f.attr, None))}{_hint(f)}"
        for key, f in fields.items()
    )


async def _replies(ctx: ToolContext) -> dict:
    config = await ctx.session.get(GuildConfig, ctx.cfg_guild)
    return dict(config.command_messages or {}) if config else {}


async def _read_replies(ctx: ToolContext) -> str:
    custom = await _replies(ctx)
    lines = []
    for name, default in DEFAULT_COMMAND_MESSAGES.items():
        line = f"{_REPLY}{name}={_clip(custom.get(name) or default)}"
        if not custom.get(name):
            line += " (default)"
        if PLACEHOLDERS.get(name):
            line += " vars: " + ", ".join("{" + p + "}" for p in PLACEHOLDERS[name])
        lines.append(line)
    return "\n".join(lines)


async def _read_one(ctx: ToolContext, key: str) -> str | None:
    """One key in full, or None when ``key`` isn't one."""
    if key.startswith(_REPLY) and key[len(_REPLY):] in DEFAULT_COMMAND_MESSAGES:
        name = key[len(_REPLY):]
        custom = (await _replies(ctx)).get(name)
        head = f"{key} ({'custom' if custom else 'default'})"
        if PLACEHOLDERS.get(name):
            head += ", vars: " + ", ".join("{" + p + "}" for p in PLACEHOLDERS[name])
        return f"{head}:\n{custom or DEFAULT_COMMAND_MESSAGES[name]}"
    f = _field(key)
    if f is None:
        return None
    row = await ctx.session.get(f.table, ctx.cfg_guild)
    value = getattr(row, f.attr, None)
    if f.kind == "text":
        return f"{key} ({len(value or '')} chars):\n{value or ''}"
    return f"{key}={_show(f, value)}{_hint(f)}"


async def _read_knowledge(ctx: ToolContext) -> str:
    s, guild = ctx.session, ctx.cfg_guild
    indexed = await s.scalar(
        select(func.count()).select_from(SearchMessage).where(SearchMessage.guild_id == guild)
    ) or 0
    backlog = await s.scalar(
        select(func.count()).select_from(GuildChannelInfo).where(
            GuildChannelInfo.guild_id == guild,
            GuildChannelInfo.index_enabled.is_(True),
            GuildChannelInfo.backfill_done.is_(False),
        )
    ) or 0
    lines = [
        f"search index: {indexed:,} messages"
        + (f", {backlog} channels still backfilling" if backlog else "")
    ]
    srcs = (
        await s.scalars(select(KBSource).where(KBSource.guild_id == guild).order_by(KBSource.id))
    ).all()
    chunks = dict(
        (
            await s.execute(
                select(KBChunk.source_id, func.count())
                .where(KBChunk.source_id.in_([r.id for r in srcs]))
                .group_by(KBChunk.source_id)
            )
        ).all()
    ) if srcs else {}
    lines.append(f"sources ({len(srcs)}):" if srcs else "sources: none")
    for r in srcs:
        line = f"#{r.id} {r.type.value} {r.uri} {r.status.value}, {chunks.get(r.id, 0)} passages"
        if r.title and r.title != r.uri:
            line += f", {_clip(r.title, 60)}"
        if r.refresh_interval_hours:
            line += f", re-read every {r.refresh_interval_hours}h"
        if r.error:
            line += f", error: {_cut(r.error, 100)}"
        lines.append(line)
    facts = await s.scalar(
        select(func.count()).select_from(GuildFact).where(GuildFact.guild_id == guild)
    ) or 0
    lines.append(f"glossary: {facts} facts")
    return "\n".join(lines)


_GLOSSARY_LIMIT = 40


async def _read_glossary(ctx: ToolContext, words: str) -> str:
    rows = (
        await ctx.session.scalars(
            select(GuildFact)
            .where(GuildFact.guild_id == ctx.cfg_guild)
            .order_by(GuildFact.mentions.desc(), GuildFact.updated_at.desc())
        )
    ).all()
    needles = words.lower().split()
    hits = [
        r for r in rows if all(n in f"{r.subject} {r.fact}".lower() for n in needles)
    ]
    if not hits:
        return "no matching facts" if needles else "the glossary is empty"
    lines = [
        f"#{r.id} {r.subject + ': ' if r.subject else ''}{_cut(r.fact, 100)}"
        for r in hits[:_GLOSSARY_LIMIT]
    ]
    if len(hits) > _GLOSSARY_LIMIT:
        lines.append(f"…{len(hits) - _GLOSSARY_LIMIT} more; filter to narrow")
    return "\n".join(lines)


async def _profiles(ctx: ToolContext) -> list[UserProfile]:
    return list(
        (
            await ctx.session.scalars(
                select(UserProfile).where(
                    UserProfile.guild_id == ctx.cfg_guild,
                    UserProfile.memory_opt_out.is_(False),
                )
            )
        ).all()
    )


def _matching(profiles: list[UserProfile], query: str) -> list[UserProfile]:
    """Members ``query`` names: an id or mention outright, else an exact display name, else
    every display name containing it."""
    digits = re.sub(r"[<@!>\s]", "", query)
    if digits.isdigit():
        return [p for p in profiles if p.user_id == int(digits)]
    needle = query.strip().lstrip("@").lower()
    exact = [p for p in profiles if (p.display_name or "").lower() == needle]
    return exact or [p for p in profiles if needle in (p.display_name or "").lower()]


async def _read_members(ctx: ToolContext, query: str) -> str:
    profiles = await _profiles(ctx)
    if not query.strip():
        built = sum(1 for p in profiles if p.persona_summary)
        return f"{len(profiles)} member profiles, {built} with an impression"
    hits = _matching(profiles, query)
    if not hits:
        return f"no member matching {query!r}"
    return "\n".join(
        f"{p.display_name} ({p.user_id}): {'has' if p.persona_summary else 'no'} impression"
        for p in hits[:15]
    )


# What a call with no section (or a wrong one) gets: every key and action, without values.
# The model sometimes calls with no arguments at all, and a bare "which section?" cost a
# whole extra model call; with the names in hand it can usually make the change directly,
# and an out-of-range value is refused with the range.
_DIRECTORY = "\n".join([
    "persona: " + ", ".join(_PERSONA),
    "behavior: " + ", ".join(_BEHAVIOR),
    "replies (key reply.<name>): " + ", ".join(DEFAULT_COMMAND_MESSAGES),
    "actions: " + ", ".join(ACTIONS),
    "Pass a section for current values and allowed ranges: " + ", ".join(SECTIONS) + ".",
])


async def open_settings(args: dict, ctx: ToolContext) -> str:
    section = (args.get("section") or "").strip().lower()
    filt = str(args.get("filter") or "").strip()
    if filt and section not in ("glossary", "members"):
        full = await _read_one(ctx, filt)
        if full is not None:
            return full
    if section == "persona":
        return await _read_fields(ctx, _PERSONA)
    if section == "behavior":
        return await _read_fields(ctx, _BEHAVIOR)
    if section == "replies":
        return await _read_replies(ctx)
    if section == "knowledge":
        return await _read_knowledge(ctx)
    if section == "glossary":
        return await _read_glossary(ctx, filt)
    if section == "members":
        return await _read_members(ctx, filt)
    return _DIRECTORY


# ── Changing a key ──────────────────────────────────────────────────────────


async def _audit(
    ctx: ToolContext, action: str, target_type: str, target_id: object, **extra
) -> None:
    """Recorded under the member who asked, and marked as coming from chat — there's no
    console Undo for these, so the before-value is what an operator restores from."""
    after = extra.pop("after", None) or {}
    await record_audit(
        ctx.session, actor=ctx.user_id, action=action, target_type=target_type,
        target_id=target_id, after={**after, "via": "chat"}, **extra,
    )


async def _change_reply(ctx: ToolContext, key: str, value: str, find: str, append: bool) -> str:
    name = key[len(_REPLY):]
    if name not in DEFAULT_COMMAND_MESSAGES:
        return f"No reply called {key!r}. open_settings replies lists them."
    config = await ctx.session.get(GuildConfig, ctx.cfg_guild)
    if config is None:
        config = GuildConfig(guild_id=ctx.cfg_guild)
        ctx.session.add(config)
    custom = dict(config.command_messages or {})
    default = DEFAULT_COMMAND_MESSAGES[name]
    stored = custom.get(name) or None
    old = stored or default
    try:
        new = _edit_text(old, value, find, append)
    except ValueError as e:
        return f"{key} not changed: {e}."
    # Nothing stored means "the default", so an edit that lands back on it stores nothing.
    kept = new if new and new != default else None
    if kept == stored:
        return f"{key} already says that."
    if kept:
        custom[name] = kept
    else:
        custom.pop(name, None)
    config.command_messages = custom  # reassigned so SQLAlchemy sees the change
    config.version = (config.version or 1) + 1
    await _audit(
        ctx, "update_command_messages", "guild_config", ctx.cfg_guild,
        before={name: stored}, after={name: kept},
    )
    await ctx.session.commit()
    return f"{key} changed." if kept else f"{key} reset to the default."


async def change_setting(args: dict, ctx: ToolContext) -> str:
    key = (args.get("key") or "").strip()
    value = "" if args.get("value") is None else str(args.get("value"))
    find = str(args.get("find") or "")
    append = _truthy(args.get("append"))
    if key.startswith(_REPLY):
        return await _change_reply(ctx, key, value, find, append)
    f = _field(key)
    if f is None:
        return f"No setting called {key!r}. Use a key exactly as open_settings lists it."
    if (find.strip() or append) and f.kind != "text":
        return f"find and append only work on text; give {key} its whole new value."
    row = await ctx.session.get(f.table, ctx.cfg_guild)
    if row is None:
        row = f.table(guild_id=ctx.cfg_guild)
        ctx.session.add(row)
    old = getattr(row, f.attr, None)
    try:
        new = _parse(f, value, old, find, append)
    except ValueError as e:
        return f"{key} not changed: {e}."
    if new == old:
        return f"{key} is already {_show(f, old)}."
    setattr(row, f.attr, new)
    if f.table is GuildConfig:
        row.version = (row.version or 1) + 1
    elif f.table is Persona:
        row.updated_by = ctx.user_id
    action, target_type = _AUDIT[f.table]
    await _audit(
        ctx, action, target_type, ctx.cfg_guild,
        before={f.attr: _plain(old)}, after={f.attr: _plain(new)},
    )
    await ctx.session.commit()
    if f.kind == "text":
        result = f"{key} changed ({len(old or '')} → {len(new)} chars)."
    else:
        result = f"{key}: {_show(f, old)} → {_show(f, new)}."
    # The bio is the bot's About Me, which is bot-wide, so only the home server's persona
    # drives it (same rule as the console's save).
    if f.attr == "desired_bio" and ctx.cfg_guild == settings.target_guild_id:
        from olisar.discord_bio import apply_bot_bio
        from olisar.runtime_config import discord_token

        if not await apply_bot_bio(await discord_token(), new):
            result += " Saved, but Discord didn't accept the new About Me."
    return result


# ── Actions ─────────────────────────────────────────────────────────────────


def _count(raw: object, default: int, lo: int, hi: int, what: str) -> int:
    if raw in (None, ""):
        return default
    try:
        n = int(float(str(raw).strip()))
    except (ValueError, OverflowError):
        raise ValueError(f"{what} has to be a number") from None
    if not lo <= n <= hi:
        raise ValueError(f"{what} has to be {lo}-{hi}")
    return n


async def _source(ctx: ToolContext, target: str) -> KBSource | str:
    try:
        sid = int(target.strip().lstrip("#"))
    except ValueError:
        return "Give the source's id as target (open_settings knowledge lists them)."
    src = await ctx.session.get(KBSource, sid)
    if src is None or src.guild_id != ctx.cfg_guild:
        return f"No source #{sid} here."
    return src


async def _kb_add(ctx: ToolContext, target: str, args: dict, *, site: bool) -> str:
    if not target.lower().startswith(("http://", "https://")):
        return "Give the full http(s) URL as target."
    try:
        hours = _count(args.get("hours"), 0, 0, MAX_INTERVAL_HOURS, "hours")
        depth = _count(args.get("depth"), 1, 0, 3, "depth")
        pages = _count(args.get("pages"), 25, 1, 100, "pages")
    except ValueError as e:
        return f"Not added: {e}."
    kind = "website" if site else "url"
    src = sources.new_source(
        guild_id=ctx.cfg_guild, type=kind, uri=target, crawl_depth=depth,
        max_pages=pages, refresh_hours=hours, added_by=ctx.user_id,
    )
    ctx.session.add(src)
    await ctx.session.flush()
    await _audit(
        ctx, "add_kb_source", "kb_source", src.id,
        after={"uri": target, "type": kind, "refresh_hours": hours},
    )
    await ctx.session.commit()
    return f"Queued source #{src.id}; it'll be read shortly."


async def _kb_remove(ctx: ToolContext, target: str, args: dict) -> str:
    src = await _source(ctx, target)
    if isinstance(src, str):
        return src
    sid = src.id
    removed = await sources.delete_source(ctx.session, src)
    await _audit(ctx, "delete_kb_source", "kb_source", sid)
    await ctx.session.commit()
    return f"Removed source #{sid} and the {removed} passages read from it."


async def _kb_refresh(ctx: ToolContext, target: str, args: dict) -> str:
    src = await _source(ctx, target)
    if isinstance(src, str):
        return src
    if src.status in sources.BUSY:
        return f"Source #{src.id} is already being read."
    sources.requeue(src)
    await _audit(ctx, "refresh_kb_source", "kb_source", src.id)
    await ctx.session.commit()
    return f"Re-reading source #{src.id} now."


async def _kb_schedule(ctx: ToolContext, target: str, args: dict) -> str:
    src = await _source(ctx, target)
    if isinstance(src, str):
        return src
    if args.get("hours") in (None, ""):
        return "Give hours: how often to re-read it, 0 for never."
    try:
        hours = _count(args.get("hours"), 0, 0, MAX_INTERVAL_HOURS, "hours")
    except ValueError as e:
        return f"Not changed: {e}."
    if hours and src.type not in REFRESHABLE_TYPES:
        return f"Source #{src.id} is an uploaded document; it can't change, so it has no schedule."
    before = src.refresh_interval_hours
    sources.set_schedule(src, hours)
    await _audit(
        ctx, "set_kb_refresh", "kb_source", src.id,
        before={"refresh_hours": before}, after={"refresh_hours": hours},
    )
    await ctx.session.commit()
    if not hours:
        return f"Source #{src.id} no longer re-reads on a schedule."
    return f"Source #{src.id} now re-reads every {hours}h."


async def _index_rebuild(ctx: ToolContext, target: str, args: dict) -> str:
    from olisar.memory.writer import rearm_search_index

    queued = await rearm_search_index(ctx.session, ctx.cfg_guild)
    await _audit(ctx, "reindex_search", "guild", ctx.cfg_guild)
    await ctx.session.commit()
    return f"Re-indexing {queued} channels' history in the background."


async def _index_clear(ctx: ToolContext, target: str, args: dict) -> str:
    from olisar.memory.writer import clear_search_index

    removed = await clear_search_index(ctx.session, ctx.cfg_guild)
    await _audit(ctx, "clear_search_index", "guild", ctx.cfg_guild, after={"removed": removed})
    await ctx.session.commit()
    return (
        f"Cleared {removed:,} messages from the search index. New posts are still "
        "indexed; index_rebuild brings the history back."
    )


async def _glossary_delete(ctx: ToolContext, target: str, args: dict) -> str:
    ids = [int(n) for n in re.findall(r"\d+", target)]
    if not ids:
        return "Give the fact ids as target (open_settings glossary lists them)."
    gone, missing = [], []
    for fid in dict.fromkeys(ids):
        row = await ctx.session.get(GuildFact, fid)
        if row is None or row.guild_id != ctx.cfg_guild:
            missing.append(fid)
            continue
        await ctx.session.delete(row)
        await _audit(ctx, "delete_guild_fact", "guild_fact", fid)
        gone.append(fid)
    await ctx.session.commit()
    parts = []
    if gone:
        parts.append("Deleted " + ", ".join(f"#{i}" for i in gone) + ".")
    if missing:
        parts.append("No fact " + ", ".join(f"#{i}" for i in missing) + " here.")
    return " ".join(parts)


async def _glossary_mine(ctx: ToolContext, target: str, args: dict) -> str:
    from olisar.memory.maintenance import mine_glossary_now

    # The mine writes through sessions of its own; let go of this reply's lock first.
    await ctx.session.commit()
    result = await mine_glossary_now(ctx.cfg_guild)
    dm = await mine_glossary_now(0)  # DMs mine into the guild-0 glossary, as on the console
    for k in ("added", "mined", "remaining"):
        result[k] = result.get(k, 0) + dm.get(k, 0)
    await _audit(ctx, "mine_glossary", "guild", ctx.cfg_guild, after=result)
    await ctx.session.commit()
    out = f"Mined {result['mined']} messages from memory: {result['added']} new facts."
    if result["remaining"]:
        out += f" {result['remaining']} messages left; run it again to continue."
    return out


async def _glossary_deep_mine(ctx: ToolContext, target: str, args: dict) -> str:
    from olisar.memory.maintenance import deep_mine_glossary_now

    await ctx.session.commit()
    result = await deep_mine_glossary_now(ctx.cfg_guild)
    dm = await deep_mine_glossary_now(0)
    for k in ("added", "sampled"):
        result[k] = result.get(k, 0) + dm.get(k, 0)
    await _audit(ctx, "deep_mine_glossary", "guild", ctx.cfg_guild, after=result)
    await ctx.session.commit()
    return f"Sampled {result['sampled']} indexed messages: {result['added']} new facts."


async def _rebuild_impression(ctx: ToolContext, target: str, args: dict) -> str:
    from olisar.memory.personas import build_persona_now

    if not target:
        return "Give the member's name or id as target."
    hits = _matching(await _profiles(ctx), target)
    if not hits:
        return f"No member matching {target!r} here."
    if len(hits) > 1:
        names = ", ".join(f"{p.display_name} ({p.user_id})" for p in hits[:8])
        return f"Several members match: {names}. Call again with the id."
    member = hits[0]
    result = await build_persona_now(ctx.session, guild_id=ctx.cfg_guild, user_id=member.user_id)
    if not result.get("ok"):
        return result.get("error") or "Couldn't rebuild it."
    await _audit(ctx, "build_impression", "user_profile", member.user_id)
    await ctx.session.commit()
    # The impression itself stays out of the result: it's a private profile, and this reply
    # is going to a channel.
    return f"Rebuilt {member.display_name}'s impression from {result.get('messages', 0)} messages."


_ACTIONS = {
    "kb_add_page": lambda ctx, t, a: _kb_add(ctx, t, a, site=False),
    "kb_add_site": lambda ctx, t, a: _kb_add(ctx, t, a, site=True),
    "kb_remove": _kb_remove,
    "kb_refresh": _kb_refresh,
    "kb_schedule": _kb_schedule,
    "index_rebuild": _index_rebuild,
    "index_clear": _index_clear,
    "glossary_delete": _glossary_delete,
    "glossary_mine": _glossary_mine,
    "glossary_deep_mine": _glossary_deep_mine,
    "rebuild_impression": _rebuild_impression,
}


async def settings_action(args: dict, ctx: ToolContext) -> str:
    action = (args.get("action") or "").strip().lower()
    handler = _ACTIONS.get(action)
    if handler is None:
        return f"No action {action!r}. Actions: {', '.join(ACTIONS)}."
    return await handler(ctx, str(args.get("target") or "").strip(), args)


async def run(name: str, args: dict, ctx: ToolContext) -> str:
    """Dispatch one of this module's tools."""
    if name == READ_DECLARATION.name:
        ctx.settings_open = True
        return await open_settings(args, ctx)
    if name == "change_setting":
        return await change_setting(args, ctx)
    return await settings_action(args, ctx)
