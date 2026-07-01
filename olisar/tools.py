"""Function-calling tools Olisar can invoke mid-conversation.

Each tool returns a short string that's fed back to the model. Tools that touch
Discord (set_status, react) go through a `DiscordActions` provided by the caller,
so this module stays Discord-agnostic; if no actions are available (e.g. /ask),
those tools degrade politely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import GeminiUsage, GuildConfig, Reminder, UserMemory, UserMemoryKind
from olisar.gemini.client import GroundingUnavailable, get_gemini
from olisar.imaging import generate_image, is_configured as image_is_configured
from olisar.knowledge.retrieval import search_knowledge
from olisar.memory.retriever import recall
from olisar.memory.search import search_messages

log = logging.getLogger("olisar.tools")


class DiscordActions(Protocol):
    async def set_status(self, text: str) -> str: ...
    async def react(self, emoji: str) -> str: ...
    async def send_dm(self, user_id: int, text: str) -> str: ...
    async def send_channel(
        self, channel: object, text: str, *, home_guild_id: int, requester_id: int,
        blocked_mentions: list | None = None,
    ) -> str: ...
    async def send_image(self, data: bytes, *, filename: str = ..., caption: str = ...) -> str: ...
    async def user_status(self, query: str, guild_id: int) -> str: ...
    async def who_in_voice(self, guild_id: int) -> str: ...
    # Post a message (optionally with an embed + interactive components) to a channel —
    # backs host.discord.send for trusted extension tools. `channel` is None for the current
    # channel, or a name/id/#mention resolved within `home_guild_id`. Returns a status string.
    async def post_components(
        self, *, channel: object, content: object, embed: object,
        components: object, ext_key: str, home_guild_id: int,
    ) -> str: ...


@dataclass
class ToolContext:
    session: AsyncSession
    cfg_guild: int
    channel_id: int
    user_id: int
    display_name: str
    actions: DiscordActions | None = None
    # tool name -> async handler(args, ctx), supplied per-reply for enabled
    # extensions (olisar/extensions). execute_tool dispatches to these first.
    extension_tools: dict = field(default_factory=dict)


def _str(desc: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=desc)


def _obj(props: dict, required: list[str]) -> types.Schema:
    return types.Schema(type=types.Type.OBJECT, properties=props, required=required)


def _parse_dt(value) -> datetime | None:
    """Parse an ISO8601 string into a tz-aware UTC datetime (None if unparseable)."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_DECLARATIONS = [
    types.FunctionDeclaration(
        name="recall_memory",
        description=(
            "Search your long-term memory (past messages, channel summaries, and "
            "remembered facts) for anything relevant. Use when someone refers to "
            "something earlier that isn't in the visible recent chat."
        ),
        parameters=_obj({"query": _str("what to look up")}, ["query"]),
    ),
    types.FunctionDeclaration(
        name="remember",
        description=(
            "Save a durable fact or preference about the user you're talking to so "
            "you recall it later. Use sparingly, for things clearly worth keeping. For "
            "a time-bound plan they mention (a trip, a deadline, an event), set "
            "kind='event' and remind_at to when a brief, friendly follow-up would help "
            "— you'll automatically DM them then."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "fact": _str("the fact, phrased about the user"),
                "kind": _str("fact | preference | event (default fact)"),
                "remind_at": _str(
                    "optional ISO8601 UTC time to DM a follow-up; events only"
                ),
            },
            required=["fact"],
        ),
    ),
    types.FunctionDeclaration(
        name="remember_server_fact",
        description=(
            "Add a durable, server-wide glossary fact about THIS community — an "
            "acronym, a codename, who someone is, an in-joke, a recurring event. Use "
            "when you learn lasting server lore worth carrying into every reply. This "
            "is shared server knowledge; for a fact about one person, use remember."
        ),
        parameters=_obj(
            {
                "subject": _str("the term or entity (optional)"),
                "fact": _str("one short, standalone statement"),
            },
            ["fact"],
        ),
    ),
    types.FunctionDeclaration(
        name="query_knowledge",
        description=(
            "Search this community's knowledge base — admin-provided docs and "
            "crawled websites. Use for questions about the server's own info, "
            "guides, rules, lore, or projects."
        ),
        parameters=_obj({"query": _str("what to look up in the knowledge base")}, ["query"]),
    ),
    types.FunctionDeclaration(
        name="search_messages",
        description=(
            "Search the WHOLE server's message history (every channel + posted "
            "announcements) for a specific fact someone mentioned before — a link, "
            "handle, account, date, or decision. Use for 'what's the server's X / "
            "Twitter / Discord invite', 'where did someone post Y', 'has anyone "
            "mentioned Z'. Returns candidate messages with Discord jump-links: read "
            "them and synthesize the answer (share a jump-link only when they're "
            "asking where or when something was posted). This is "
            "broader than recall_memory (which is about you and the current person) "
            "— reach for it when the answer is buried somewhere in past chat. One "
            "good search is usually enough: read the results and answer; don't "
            "repeat near-identical searches."
        ),
        parameters=_obj({"query": _str("the fact to find in server history")}, ["query"]),
    ),
    types.FunctionDeclaration(
        name="web_search",
        description=(
            "Search the public WEB for general or current-events info from the "
            "outside world (news, live facts, things unrelated to this server). Do "
            "NOT use this for anything about THIS community — for the server's own "
            "accounts, links, history, or who-said-what, use search_messages "
            "instead. May be unavailable when rate-limited."
        ),
        parameters=_obj({"query": _str("the search query")}, ["query"]),
    ),
    types.FunctionDeclaration(
        name="generate_image",
        description=(
            "Create and post an ORIGINAL image from a text prompt. Use when someone "
            "asks you to draw, paint, generate, make, design, or imagine a picture, "
            "art, meme, or visual. Write a vivid, detailed prompt yourself — subject, "
            "style, mood, colors, composition — don't just echo their words. The "
            "image is posted to the channel automatically; you only add a short, "
            "in-character caption. Not for editing existing images or answering "
            "questions about them."
        ),
        parameters=_obj(
            {"prompt": _str("a detailed description of the image to create")}, ["prompt"]
        ),
    ),
    types.FunctionDeclaration(
        name="set_status",
        description=(
            "Set your own Discord status/activity text (shows under your name). "
            "Short and in-character."
        ),
        parameters=_obj({"text": _str("status text, ~120 chars max")}, ["text"]),
    ),
    types.FunctionDeclaration(
        name="react",
        description="React to the user's current message with a single emoji.",
        parameters=_obj({"emoji": _str("one emoji, e.g. 👍 or 🔥")}, ["emoji"]),
    ),
    types.FunctionDeclaration(
        name="send_dm",
        description=(
            "Send a private direct message to a user by their numeric id (from the "
            "people directory in your context). Use to take something out of a busy "
            "channel, follow up privately, or reach out when it genuinely helps. "
            "Omit user_id to DM the person you're currently talking with."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "user_id": _str("the recipient's numeric Discord id; omit to DM the current user"),
                "message": _str("the message to send privately"),
            },
            required=["message"],
        ),
    ),
    types.FunctionDeclaration(
        name="send_to_channel",
        description=(
            "Post a message to a specific server channel on the user's behalf — use when "
            "they ask you to send, relay, announce, or drop something in a named channel "
            "(e.g. 'tell #general the event is live', even from a DM). Identify the channel "
            "by name (fuzzy — 'general' matches '💬│general-chat'), a <#id> mention, or its "
            "numeric id. It only works if the person asking can post there themselves. You "
            "may include @everyone/@here or role pings if they ask, but those only actually "
            "notify people when the requester is a server admin and the server permits it — "
            "otherwise they're shown without pinging. Don't use this to reply to the channel "
            "you're already in — just answer normally there."
        ),
        parameters=_obj(
            {
                "channel": _str(
                    "the target channel: a name (partial/fuzzy is fine), a <#id> mention, "
                    "or a numeric channel id"
                ),
                "message": _str("the exact message text to post in that channel"),
            },
            ["channel", "message"],
        ),
    ),
    types.FunctionDeclaration(
        name="catchup",
        description=(
            "Summarize what the user missed in THIS channel since they were last "
            "active here. Use when someone asks to be caught up, what they missed, or "
            "for a tl;dr of recent activity in this channel. Returns a short digest — "
            "relay it in your own voice."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"hours": _str("optional: how many hours back to cover")},
            required=[],
        ),
    ),
    types.FunctionDeclaration(
        name="add_reminder",
        description=(
            "Schedule a reminder. Give either delay_minutes (minutes from now) OR "
            "at_iso (an absolute ISO8601 UTC time you compute from the current time in "
            "your context). target 'dm' (default) DMs the user; 'channel' posts here "
            "and @-mentions them."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "content": _str("what to remind them about"),
                "delay_minutes": _str("minutes from now, e.g. 120 for 2 hours"),
                "at_iso": _str("absolute ISO8601 UTC time (alternative to delay_minutes)"),
                "target": _str("'dm' (default) or 'channel'"),
            },
            required=["content"],
        ),
    ),
    types.FunctionDeclaration(
        name="list_reminders",
        description="List the current user's pending reminders (with their id numbers).",
        parameters=_obj({}, []),
    ),
    types.FunctionDeclaration(
        name="cancel_reminder",
        description="Cancel one of the current user's pending reminders by its id number.",
        parameters=_obj({"id": _str("the reminder's id number")}, ["id"]),
    ),
    types.FunctionDeclaration(
        name="set_dm_indexing",
        description=(
            "Turn saving & search-indexing of THIS user's direct messages on or off, when "
            "they ask (e.g. 'stop saving my DMs' / 'don't index my messages' / 'you can "
            "remember my DMs again'). Pass enabled=false to stop storing and indexing their "
            "DMs, enabled=true to resume. Only affects DMs, never server channels."
        ),
        parameters=_obj(
            {"enabled": _str("'true' to allow DM storage + indexing, 'false' to disable")},
            ["enabled"],
        ),
    ),
]

TOOLS = [types.Tool(function_declarations=_DECLARATIONS)]

# Situational-awareness tools — added to a reply's tool set only when the server
# has presence_tools_enabled (see pipeline.generate_reply). Kept out of the core
# set because reading presence is privileged + sensitive and opt-in per guild.
_PRESENCE_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_user_status",
        description=(
            "Check a member's CURRENT Discord presence — whether they're "
            "online/idle/do-not-disturb/offline and what game or app they're playing, "
            "streaming, or listening to right now. Use only when asked what someone is "
            "up to at the moment or whether they're around."
        ),
        parameters=_obj({"user": _str("the member's display name or numeric id")}, ["user"]),
    ),
    types.FunctionDeclaration(
        name="who_is_in_voice",
        description=(
            "List who is in the server's voice channels right now. Use when asked "
            "who's in voice / in a call / hanging out in VC."
        ),
        parameters=_obj({}, []),
    ),
]


def presence_declarations() -> list:
    """Function declarations for the situational-awareness tools."""
    return _PRESENCE_DECLARATIONS


def tools_with_extensions(extra_declarations: list) -> list:
    """The tool set for one reply: the core tools plus any enabled extensions'
    function declarations. Returns the shared TOOLS when there are no extras."""
    if not extra_declarations:
        return TOOLS
    return [types.Tool(function_declarations=[*_DECLARATIONS, *extra_declarations])]


# Core tools exposed in the dashboard sandbox (the enclosed test chat). Only the
# knowledge-base lookup and web search: everything else is deliberately excluded —
# the memory/glossary tools (remember, remember_server_fact, recall_memory,
# search_messages, catchup, *_reminder) because the sandbox is memory-free, and the
# Discord-action tools (react, send_dm, set_status, get_user_status, who_is_in_voice,
# generate_image) because there's no live channel/member to act on. New tools stay
# out by default, which is the safe behaviour for an enclosed environment.
_SANDBOX_CORE = {"query_knowledge", "web_search"}


def sandbox_tools(extra_declarations: list) -> list:
    """Tool set for the enclosed dashboard test chat: the sandbox-safe core tools plus
    any enabled extensions' tools (those are API-based and need no Discord context).
    Keeps tool-calling + KB working while guaranteeing a test chat never writes memory
    or reaches into the live server."""
    core = [d for d in _DECLARATIONS if d.name in _SANDBOX_CORE]
    return [types.Tool(function_declarations=[*core, *extra_declarations])]


async def _grounding_allowed(session: AsyncSession, cfg_guild: int) -> bool:
    config = await session.get(GuildConfig, cfg_guild)
    if config is None or not config.grounding_enabled:
        return False
    today = datetime.now(timezone.utc).date()
    rows = (await session.scalars(select(GeminiUsage).where(GeminiUsage.day == today))).all()
    used = sum(r.grounding_count for r in rows)
    return used < config.grounding_daily_cap


def _summarize(text: str, limit: int = 200) -> str:
    """Collapse a tool result to one short line for logging."""
    s = " ".join((text or "").split())
    return (s[:limit] + "…") if len(s) > limit else s


async def _dispatch(name: str, args: dict, ctx: ToolContext) -> str:
    # Extension-provided tools (enabled per reply) take precedence over core tools.
    ext_handler = ctx.extension_tools.get(name)
    if ext_handler is not None:
        try:
            return await ext_handler(args, ctx)
        except Exception:
            log.exception("extension tool %s failed", name)
            return f"the {name} feature hit an error — tell the user you couldn't run it."
    try:
        if name == "recall_memory":
            block = await recall(
                ctx.session,
                cfg_guild=ctx.cfg_guild,
                user_id=ctx.user_id,
                query_text=args.get("query", ""),
                recent_ids=set(),
            )
            return block or "Nothing relevant found in memory."

        if name == "remember":
            fact = (args.get("fact") or "").strip()
            if not fact:
                return "Nothing to remember."
            kind = {
                "event": UserMemoryKind.event,
                "preference": UserMemoryKind.preference,
            }.get((args.get("kind") or "").strip().lower(), UserMemoryKind.fact)
            remind_at = _parse_dt(args.get("remind_at"))
            is_event = kind is UserMemoryKind.event
            ctx.session.add(
                UserMemory(
                    user_id=ctx.user_id,
                    guild_id=ctx.cfg_guild,
                    kind=kind,
                    content=fact,
                    embedded=False,
                    event_date=remind_at if is_event else None,
                )
            )
            now = datetime.now(timezone.utc)
            if is_event and remind_at and remind_at > now:
                ctx.session.add(
                    Reminder(
                        guild_id=ctx.cfg_guild,
                        channel_id=ctx.channel_id,
                        user_id=ctx.user_id,
                        target="dm",
                        source="event_fact",
                        content=f"following up on what you mentioned — {fact}",
                        scheduled_at=remind_at,
                    )
                )
                return f"Saved, and I'll check back with you around then: {fact}"
            return f"Saved to memory: {fact}"

        if name == "remember_server_fact":
            fact = (args.get("fact") or "").strip()
            if not fact:
                return "Nothing to add to the glossary."
            from olisar.memory.facts import upsert_facts

            added = await upsert_facts(
                ctx.session,
                guild_id=ctx.cfg_guild,
                channel_id=ctx.channel_id,
                items=[{"subject": (args.get("subject") or "").strip(), "fact": fact}],
            )
            return (
                f"Added to the server glossary: {fact}"
                if added
                else f"Already in the glossary: {fact}"
            )

        if name == "query_knowledge":
            block = await search_knowledge(
                ctx.session, ctx.cfg_guild, args.get("query", ""), k=5
            )
            return block or "Nothing found in the knowledge base."

        if name == "search_messages":
            block = await search_messages(
                ctx.session, guild_id=ctx.cfg_guild, query=args.get("query", "")
            )
            return block or "No matching messages found in the server's history."

        if name == "web_search":
            if not await _grounding_allowed(ctx.session, ctx.cfg_guild):
                return "Web search is unavailable right now (daily limit) — answer from what you know."
            try:
                text, sources = await get_gemini().search(args.get("query", ""))
            except GroundingUnavailable:
                return "Web search is temporarily unavailable (rate limited) — answer from what you know."
            except Exception:
                log.exception("web_search failed")
                return "Web search failed — answer from what you know."
            log.info(
                "web_search(%r): %d source(s) — %s",
                args.get("query", ""), len(sources), "; ".join(sources[:5]) or "none",
            )
            if sources:
                return f"{text}\n\nSources: " + "; ".join(sources[:3])
            return text or "No results."

        if name == "generate_image":
            prompt = (args.get("prompt") or "").strip()
            if not prompt:
                return "No image prompt given."
            if ctx.actions is None:
                return "Can't generate images from here."
            if not await image_is_configured():
                return (
                    "Image generation isn't set up on this server — tell the user "
                    "you can't make images right now."
                )
            try:
                data, mime = await generate_image(prompt)
            except Exception:
                log.exception("generate_image failed")
                return "Image generation failed — tell the user you couldn't make it right now."
            if not data:
                return (
                    "Image generation is unavailable right now (the daily free "
                    "allocation may be used up) — tell the user you can't make an "
                    "image at the moment."
                )
            ext = "jpg" if "jpeg" in (mime or "") or "jpg" in (mime or "") else "png"
            result = await ctx.actions.send_image(data, filename=f"olisar.{ext}")
            if result != "image posted":
                return result  # surface the failure reason to the model
            return (
                f"Posted the image you generated for: {prompt!r}. Now add a short, "
                "natural caption in your own voice — don't describe it in detail."
            )

        if name == "set_status":
            if ctx.actions is None:
                return "Can't set status from here."
            return await ctx.actions.set_status((args.get("text") or "")[:128])

        if name == "react":
            if ctx.actions is None:
                return "Can't react from here."
            return await ctx.actions.react(args.get("emoji") or "")

        if name == "send_dm":
            if ctx.actions is None:
                return "Can't send DMs from here."
            target = args.get("user_id") or ctx.user_id
            return await ctx.actions.send_dm(target, args.get("message") or "")

        if name == "send_to_channel":
            if ctx.actions is None:
                return "Can't post to channels from here."
            cfg = await ctx.session.get(GuildConfig, ctx.cfg_guild)
            blocked = list(cfg.blocked_mentions or []) if cfg else []
            return await ctx.actions.send_channel(
                args.get("channel") or "",
                args.get("message") or "",
                home_guild_id=ctx.cfg_guild,
                requester_id=ctx.user_id,
                blocked_mentions=blocked,
            )

        if name == "get_user_status":
            if ctx.actions is None:
                return "Can't check status from here."
            return await ctx.actions.user_status((args.get("user") or "").strip(), ctx.cfg_guild)

        if name == "who_is_in_voice":
            if ctx.actions is None:
                return "Can't check voice channels from here."
            return await ctx.actions.who_in_voice(ctx.cfg_guild)

        if name == "catchup":
            from olisar.catchup import generate_catchup

            raw = args.get("hours")
            try:
                hours = int(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                hours = None
            return await generate_catchup(
                ctx.session,
                guild_id=ctx.cfg_guild,
                channel_id=ctx.channel_id,
                user_id=ctx.user_id,
                hours=hours,
            )

        if name == "add_reminder":
            content = (args.get("content") or "").strip()
            if not content:
                return "What should I remind you about?"
            now = datetime.now(timezone.utc)
            when: datetime | None = None
            raw_delay = args.get("delay_minutes")
            try:
                if raw_delay not in (None, ""):
                    when = now + timedelta(minutes=float(raw_delay))
            except (TypeError, ValueError):
                when = None
            if when is None:
                when = _parse_dt(args.get("at_iso"))
            if when is None:
                return "I need a time — say how long from now or an exact time."
            if when <= now:
                return "That time is already past — give me a future time."
            target = "channel" if (args.get("target") or "").strip().lower() == "channel" else "dm"
            ctx.session.add(
                Reminder(
                    guild_id=ctx.cfg_guild,
                    channel_id=ctx.channel_id,
                    user_id=ctx.user_id,
                    target=target,
                    source="user",
                    content=content,
                    scheduled_at=when,
                )
            )
            return f"Reminder set for {when.strftime('%Y-%m-%d %H:%M UTC')}: {content}"

        if name == "list_reminders":
            rows = (
                await ctx.session.scalars(
                    select(Reminder)
                    .where(
                        Reminder.user_id == ctx.user_id,
                        Reminder.guild_id == ctx.cfg_guild,
                        Reminder.fired == False,  # noqa: E712
                    )
                    .order_by(Reminder.scheduled_at.asc())
                    .limit(20)
                )
            ).all()
            if not rows:
                return "You have no pending reminders."
            return "Pending reminders:\n" + "\n".join(
                f"#{r.id} — {r.scheduled_at.strftime('%Y-%m-%d %H:%M UTC')}: {r.content}"
                for r in rows
            )

        if name == "cancel_reminder":
            try:
                rid = int(args.get("id"))
            except (TypeError, ValueError):
                return "Which reminder? Give me its id number (see list_reminders)."
            r = await ctx.session.get(Reminder, rid)
            if r is None or r.user_id != ctx.user_id or r.fired:
                return "I couldn't find that pending reminder of yours."
            r.fired = True
            return f"Cancelled reminder #{rid}."

        if name == "set_dm_indexing":
            from olisar.memory.writer import upsert_profile

            raw = str(args.get("enabled", "")).strip().lower()
            enabled = raw in ("true", "1", "yes", "on")
            # The DM preference lives on the user's guild-0 profile (DMs aren't per-guild).
            profile = await upsert_profile(ctx.session, 0, ctx.user_id, ctx.display_name)
            profile.dm_opt_out = not enabled
            if enabled:
                return "Okay — I'll keep saving and indexing our DMs so I can remember our chats."
            return "Done — I've stopped saving and indexing your DMs, and won't remember new ones."

        return f"Unknown tool: {name}"
    except Exception:
        log.exception("tool %s failed", name)
        return f"Tool {name} errored."


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> str:
    """Run a tool, logging the call and a one-line summary of what it returned
    (search-type tools also log the specific items they used, in their modules)."""
    log.info("tool call: %s(%s)", name, ", ".join(f"{k}={v!r}" for k, v in args.items()))
    result = await _dispatch(name, args, ctx)
    log.info("tool result: %s -> %s", name, _summarize(result))
    return result
