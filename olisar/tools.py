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

from olisar import self_settings, toolpin
from olisar.db.models import (
    MASS_MENTIONS,
    GeminiUsage,
    GuildConfig,
    Reminder,
    UserMemory,
    UserMemoryKind,
)
from olisar.gemini.client import GroundingUnavailable, get_gemini
from olisar.gemini.quota import quota_day
from olisar.gemini.rate_limiter import pending_grounding
from olisar.imaging import generate_image, is_configured as image_is_configured
from olisar.knowledge.retrieval import search_knowledge
from olisar.memory.retriever import recall
from olisar.memory.writer import record_bot_activity
from olisar.memory.search import search_messages
from olisar.message_links import ChannelFilter, channel_filter
from olisar.proactivity import first_emoji

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
    async def is_admin(self, user_id: int, guild_id: int) -> bool: ...
    async def is_member(self, user_id: int, guild_id: int) -> bool: ...
    async def channel_directory(
        self, guild_id: int, *, requester_id: int = ..., limit: int = ...
    ) -> str: ...
    # Which of `channel_ids` the requester can open (read the history of) in this guild.
    # Search and recall filter every hit through it; see olisar.message_links.
    async def readable_channels(
        self, guild_id: int, channel_ids: set[int], *, requester_id: int
    ) -> set[int]: ...
    # Post a message (optionally with an embed + interactive components) to a channel —
    # backs host.discord.send for trusted extension tools. `channel` is None for the current
    # channel, or a name/id/#mention resolved within `home_guild_id`. Returns a status string.
    async def post_components(
        self, *, channel: object, content: object, embed: object,
        components: object, ext_key: str, home_guild_id: int,
    ) -> str: ...
    # Ask the channel to confirm a gated tool call with the 4-digit PIN, and wait for the
    # answer. `details` says what the call would do. Returns one of the outcome constants
    # in olisar.toolpin.
    async def request_pin(
        self, *, tool: str, guild_id: int, user_id: int, timeout: float, details: str = ""
    ) -> str: ...
    # React to the message that triggered this reply and end the turn without sending
    # anything. Returns a success string, or a refusal when there's no message to react
    # to (the /ask path) or the reaction itself failed.
    async def acknowledge(self, emoji: str) -> str: ...


# The search tools the pipeline caps per reply: a model left alone re-queries these with
# reworded arguments until the iteration budget is gone.
LOOKUP_TOOLS = frozenset(
    {"search_messages", "recall_memory", "query_knowledge", "web_search"}
)

# The tools that change something and report only whether they did. These are the only
# ones that may come before a silent `acknowledge`, since all that's left to say after one
# is "done". Anything else (a search, a catch-up, a presence check, the settings read, any
# extension's tool) returns something the person asked to hear, so silence after it is
# refused. A list of what's allowed rather than of what isn't, so a new tool starts out
# owing an answer.
ACTION_TOOLS = frozenset({
    "send_dm", "send_to_channel", "remember", "remember_server_fact", "add_reminder",
    "cancel_reminder", "set_status", "react", "generate_image", "set_dm_indexing",
    "change_setting", "settings_action",
})

# The tools that read or write the server's own data: its knowledge base, its glossary, and
# what its members are doing right now. A DM borrows the home server, and sharing any
# server with the bot is enough to DM it, so in a DM these are only for members of the home
# server (see _asker_in_guild).
GUILD_TOOLS = frozenset({
    "query_knowledge", "remember_server_fact", "get_user_status", "who_is_in_voice",
})


@dataclass
class ToolContext:
    session: AsyncSession
    cfg_guild: int
    channel_id: int
    user_id: int
    display_name: str
    # True when this exchange is a DM (raw guild_id 0). Lets tools offer own-DM recall
    # without mistaking it for a guild channel. cfg_guild is still the DM's home guild.
    is_dm: bool = False
    # The Discord message being answered; 0 when there isn't one (/ask).
    message_id: int = 0
    actions: DiscordActions | None = None
    # tool name -> async handler(args, ctx), supplied per-reply for enabled
    # extensions (olisar/extensions). execute_tool dispatches to these first.
    extension_tools: dict = field(default_factory=dict)
    # Gated actions (olisar.toolpin.gate) whose PIN prompt already came back "no" this
    # reply, and why. A denial holds for the rest of the reply: without it the model's retry
    # would post a second prompt, and someone who just declined would be asked again.
    pin_denied: dict = field(default_factory=dict)
    # Gated actions a PIN entry already confirmed this reply. The confirmation covers the
    # action, so "rename yourself and rewrite your bio" asks once rather than per call.
    pin_approved: set = field(default_factory=set)
    # Every tool name this reply has called, in order. `acknowledge` reads it to refuse
    # silence after anything but an action; nothing else depends on the ordering yet.
    tools_run: list = field(default_factory=list)
    # The calls the model made in the turn now being run (olisar.pipeline._run_tool_loop).
    # They all run before anyone checks for silence, so `acknowledge` won't share one.
    batch: list = field(default_factory=list)
    # Calls this reply that didn't go through: an action that reported a failure or
    # errored, or any call the PIN gate refused. Silence after one would hide it.
    failed: list = field(default_factory=list)
    # How many audited changes the settings tools have made. A settings write that didn't
    # add one didn't change anything (execute_tool reads it to tell success from refusal).
    settings_writes: int = 0
    # The emoji Olisar reacted with instead of replying — set only once the reaction has
    # actually landed. Non-empty means this turn is over and nothing gets sent, so it must
    # never be set optimistically: a failed reaction that still silenced the reply would be
    # a bot that swallowed the request without a trace anyone can see.
    silent: str = ""
    # Set once open_settings has run. From then on the reply also declares the settings
    # write tools (see with_settings_tools), which are left out until then to save tokens.
    settings_open: bool = False
    # False when this reply mustn't reach the settings tools at all (see
    # without_settings_tools). They aren't declared then, and a call that names one anyway
    # is refused.
    settings_allowed: bool = True
    # False for a reply nobody addressed to Olisar (a proactive chime-in). Search and
    # recall then go by what @everyone can open rather than by the person it answers; see
    # olisar.message_links.channel_filter.
    addressed: bool = True
    # Whether the asker is a member of cfg_guild; None until someone asks. Only a DM can be
    # from a non-member, and then GUILD_TOOLS are refused (see _asker_in_guild).
    in_guild: bool | None = None
    # How many images this reply has asked for; capped at IMAGES_PER_REPLY.
    images: int = 0

    def readable(self) -> ChannelFilter:
        """The channels this reply's asker can open, for filtering search and recall. For a
        reply nobody asked for, what @everyone can open."""
        return channel_filter(
            self.actions, guild_id=self.cfg_guild,
            requester_id=self.user_id if self.addressed else 0, here=self.channel_id,
        )


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
            "them and synthesize the answer, pasting the jump-link of the message "
            "your answer rests on. This is "
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
            "by name (fuzzy — 'general' matches '💬│general-chat', 'moderator' matches "
            "'🔨┃moderator'), a <#id> mention, or its numeric id. You do NOT need the exact "
            "name or emoji — pass the distinctive word and let it resolve; don't ask the user "
            "to spell out the channel, just call this and relay what it says (it tells you if "
            "a name is ambiguous or missing). It only works if the person asking can post "
            "there themselves. You "
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
    self_settings.READ_DECLARATION,
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


# Ending a turn without saying anything. Added to a reply's tool set only when the server
# has silent_acks_enabled, and never in the console's test chat — there is no message to
# react to there, so the whole tool reduces to a way of producing an empty test reply.
_ACK_DECLARATIONS = [
    types.FunctionDeclaration(
        name="acknowledge",
        description=(
            "React to the message you're answering and finish your turn there, sending no "
            "message at all. This is the normal way to close out a request you've already "
            "carried out with another tool — send_dm, send_to_channel, remember, "
            "remember_server_fact, add_reminder, set_status — where the only thing left to "
            "write is a confirmation: 'done', 'got it', 'sent', 'noted', 'written down', "
            "\"i'll remember that\". Call this instead of writing one. Also use it when "
            "their message needs no answer at all: 'thanks', 'sounds good', an FYI. It ENDS "
            "the reply, so call it last, on its own once the other tools' results are back, "
            "and write nothing alongside it. Never use it to duck a question, and never to "
            "avoid saying that something failed."
        ),
        parameters=_obj(
            {"emoji": _str("one emoji to react with, e.g. 👍 or 🔥 — defaults to 👍")}, []
        ),
    ),
]


def ack_declarations() -> list:
    """Function declarations for the silent-acknowledgment tool."""
    return _ACK_DECLARATIONS


# Every name Olisar's own tools answer to. A call by one of these names always runs the
# core tool (_dispatch), and an extension can't declare one (olisar.extensions.tool_names):
# its handler would get the calls meant for the core tool, arguments and all, and the model
# would take what it returned as the core tool's answer.
CORE_TOOL_NAMES = frozenset(
    d.name for d in (*_DECLARATIONS, *_PRESENCE_DECLARATIONS, *_ACK_DECLARATIONS)
) | self_settings.TOOL_NAMES


def _shadows_core(declaration: types.FunctionDeclaration) -> bool:
    """Whether a declaration handed in next to the core tools is someone else's tool under
    a core tool's name. The optional core tools (presence, acknowledge) arrive the same
    way, and pass."""
    return declaration.name in CORE_TOOL_NAMES and not any(
        declaration is own for own in (*_PRESENCE_DECLARATIONS, *_ACK_DECLARATIONS)
    )


def tools_with_extensions(extra_declarations: list) -> list:
    """The tool set for one reply: the core tools plus any enabled extensions'
    function declarations. Returns the shared TOOLS when there are no extras. An
    extension's tool under a core tool's name is left out, so the model never sees two
    tools by one name."""
    extras = [d for d in extra_declarations if not _shadows_core(d)]
    if not extras:
        return TOOLS
    return [types.Tool(function_declarations=[*_DECLARATIONS, *extras])]


def with_settings_tools(tools: list) -> list:
    """``tools`` plus the settings write tools, once open_settings has unlocked them.

    They're withheld until then because they're rarely wanted and every declaration is
    resent on every model call. See ``olisar.self_settings``."""
    declared = [d for t in tools for d in (t.function_declarations or [])]
    if any(d.name in self_settings.TOOL_NAMES - {"open_settings"} for d in declared):
        return tools
    return [
        types.Tool(function_declarations=[*declared, *self_settings.WRITE_DECLARATIONS])
    ]


def without_settings_tools(tools: list) -> list:
    """``tools`` minus every settings tool, read included, for a reply that mustn't see or
    change the server's settings. Pair it with ``ToolContext.settings_allowed = False``."""
    declared = [d for t in tools for d in (t.function_declarations or [])]
    return [
        types.Tool(
            function_declarations=[d for d in declared if d.name not in self_settings.TOOL_NAMES]
        )
    ]


def without_guild_tools(tools: list) -> list:
    """``tools`` minus GUILD_TOOLS, for a DM from someone who isn't a member of the home
    server. Pair it with ``ToolContext.in_guild = False``."""
    declared = [d for t in tools for d in (t.function_declarations or [])]
    return [
        types.Tool(function_declarations=[d for d in declared if d.name not in GUILD_TOOLS])
    ]


# Core tools exposed in the dashboard sandbox (the enclosed test chat). Only the
# knowledge-base lookup and web search: everything else is deliberately excluded —
# the memory/glossary tools (remember, remember_server_fact, recall_memory,
# search_messages, catchup, *_reminder) because the sandbox is memory-free, and the
# Discord-action tools (react, send_dm, set_status, get_user_status, who_is_in_voice,
# generate_image) because there's no live channel/member to act on. New tools stay
# out by default, which is the safe behaviour for an enclosed environment.
# The only core tools the dashboard's test chat declares. Exported (as SANDBOX_TOOL_NAMES)
# so the pipeline can render a tool briefing that matches — describing tools that aren't
# there is what made the test chat invent lookups it never performed.
_SANDBOX_CORE = {"query_knowledge", "web_search"}
SANDBOX_TOOL_NAMES = frozenset(_SANDBOX_CORE)


def sandbox_tools(extra_declarations: list) -> list:
    """Tool set for the enclosed dashboard test chat: the sandbox-safe core tools plus
    any enabled extensions' tools (those are API-based and need no Discord context).
    Keeps tool-calling + KB working while guaranteeing a test chat never writes memory
    or reaches into the live server."""
    core = [d for d in _DECLARATIONS if d.name in _SANDBOX_CORE]
    extras = [d for d in extra_declarations if not _shadows_core(d)]
    return [types.Tool(function_declarations=[*core, *extras])]


async def _grounding_allowed(session: AsyncSession, cfg_guild: int) -> bool:
    config = await session.get(GuildConfig, cfg_guild)
    if config is None or not config.grounding_enabled:
        return False
    today = quota_day()
    rows = (await session.scalars(select(GeminiUsage).where(GeminiUsage.day == today))).all()
    # Searches this reply (or any other still running) already made aren't written yet.
    used = sum(r.grounding_count for r in rows) + pending_grounding(today)
    return used < config.grounding_daily_cap


def _summarize(text: str, limit: int = 200) -> str:
    """Collapse a tool result to one short line for logging."""
    s = " ".join((text or "").split())
    return (s[:limit] + "…") if len(s) > limit else s


# How many images one reply may generate. Each spends the install's image allowance (the
# free tier, then billed), and one message used to be able to ask for a dozen.
IMAGES_PER_REPLY = 2
_IMAGE_CAP_NOTE = (
    "Not made: that's the {cap} images one reply can make. Don't call generate_image again "
    "this turn; tell them plainly how many you made, and that they can ask for more in "
    "another message."
)

# How ``DiscordActions.set_status`` opens a success, so the status can be recorded only
# once Discord took it.
STATUS_OK = "status set to:"
# How the other Discord actions open a success (bot/actions.py). Anything else they return
# is prose about why it didn't happen.
DM_OK = "sent a DM to"
POSTED_OK = "Posted your message in"
REACT_OK = "reacted with"

# How each action's result opens when it went through (the settings writes are judged by
# whether they wrote an audit row instead; see execute_tool). Any other result, like a
# refusal or an error, is a failure, and `acknowledge` won't let silence follow one.
_ACTION_DONE: dict[str, tuple[str, ...]] = {
    "send_dm": (DM_OK,),
    "send_to_channel": (POSTED_OK,),
    "react": (REACT_OK,),
    "set_status": (STATUS_OK,),
    "generate_image": ("Posted the image",),
    "remember": ("Saved",),
    "remember_server_fact": ("Added to the server glossary", "Already in the glossary"),
    "add_reminder": ("Reminder set",),
    "cancel_reminder": ("Cancelled reminder",),
    "set_dm_indexing": ("Okay", "Done"),
}


async def _note_activity(ctx: ToolContext, **row) -> None:
    """Record what a tool did for the server app's activity feed (``bot_activity``). The
    tool has already done it by now, so a failure here is logged and never turns its
    result into an error the model would repeat to the user."""
    try:
        await record_bot_activity(ctx.session, **row)
    except Exception:  # noqa: BLE001
        log.exception("couldn't record %s for the activity feed", row.get("kind"))

# What Olisar reacts with when it ends a turn without saying anything and doesn't pick an
# emoji itself. A thumbs-up is the one reaction that reads as "got it" in every room.
DEFAULT_ACK_EMOJI = "👍"

# The contract between the Discord layer and this one. ``bot/actions.py`` opens a successful
# acknowledgment with this prefix and returns ordinary prose for every failure, so whether
# the reaction landed is something the action *reports* rather than something this module
# infers from the wording — which matters more here than elsewhere, because guessing wrong
# in the optimistic direction means a request vanishing with nothing to show for it. The
# model never sees it: ``_acknowledge`` writes its own result.
ACK_OK = "reacted:"

# Refusals handed back when silence isn't available. Each one has to tell the model what to
# do *instead*, because the alternative — a bare "no" — leaves it holding a turn it thinks
# is finished, and an unfinished turn comes out as the blank fallback.
_ACK_NO_SURFACE = (
    "There's no message to react to here, so you can't end this without saying something. "
    "Reply normally: tell them briefly what you did."
)
_ACK_AFTER_LOOKUP = (
    "You looked something up this turn ({tools}), so a reaction isn't an answer — you owe "
    "them what you found, or a plain admission that you found nothing. Reply normally."
)
_ACK_IN_BATCH = (
    "Not reacted: acknowledge has to be a call on its own, and you made it alongside "
    "{tools}, whose results you hadn't seen yet. Read them and answer in words."
)
_ACK_AFTER_FAILURE = (
    "Not reacted: {tools} didn't go through this turn, and a reaction would hide that. "
    "Answer in words and say plainly what didn't work."
)


async def _acknowledge(emoji: str, ctx: ToolContext) -> str:
    """React to the message being answered and mark the turn finished, or explain why not.

    Five ways this refuses, and all of them exist because the failure they prevent looks
    identical from the channel — Olisar read the message and did nothing:

    * nothing to react to (the ``/ask`` path builds ``BotActions``, which has no message);
    * it shares a model turn with other calls, which all run before the reply is checked
      for silence, so a DM that failed or a search result would go unsaid;
    * an action didn't go through earlier in the reply (``ctx.failed``);
    * something other than an action ran this turn (``ACTION_TOOLS``), so a reaction would
      be the answer going missing;
    * the reaction itself didn't land, which is the one case where silence would also hide
      the reason it didn't.

    ``ctx.silent`` is set only on the far side of a reaction Discord accepted.
    """
    if ctx.actions is None:
        return _ACK_NO_SURFACE
    if len(ctx.batch) > 1:
        others = [n for n in ctx.batch if n != "acknowledge"] or ["another acknowledge"]
        return _ACK_IN_BATCH.format(tools=", ".join(dict.fromkeys(others)))
    if ctx.failed:
        return _ACK_AFTER_FAILURE.format(tools=", ".join(dict.fromkeys(ctx.failed)))
    used = [n for n in ctx.tools_run if n not in ACTION_TOOLS and n != "acknowledge"]
    if used:
        return _ACK_AFTER_LOOKUP.format(tools=", ".join(dict.fromkeys(used)))
    picked = first_emoji(emoji) or DEFAULT_ACK_EMOJI
    result = await ctx.actions.acknowledge(picked)
    if not result.startswith(ACK_OK):
        return result  # the reaction failed — the model has to reply after all
    ctx.silent = picked
    log.info("acknowledged with %s — this reply sends nothing", picked)
    return (
        f"Reacted with {picked}. This turn is finished — write nothing further, and don't "
        "call another tool."
    )


async def _dispatch(name: str, args: dict, ctx: ToolContext) -> str:
    # Extension-provided tools (enabled per reply) run under their own names only: a core
    # tool's name always runs the core tool, whatever an extension declared.
    ext_handler = None if name in CORE_TOOL_NAMES else ctx.extension_tools.get(name)
    if ext_handler is not None:
        try:
            return await ext_handler(args, ctx)
        except Exception:
            log.exception("extension tool %s failed", name)
            await _recover_session(ctx)
            return f"the {name} feature hit an error — tell the user you couldn't run it."
    try:
        if name == "recall_memory":
            block = await recall(
                ctx.session,
                cfg_guild=ctx.cfg_guild,
                user_id=ctx.user_id,
                query_text=args.get("query", ""),
                recent_ids=set(),
                channel_id=ctx.channel_id,
                readable=ctx.readable(),
                member=await _asker_in_guild(ctx),
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
                    # Never a DM's message: a DM's fact is filed under the home guild, and
                    # this staying empty is what keeps it out of the activity feed.
                    source_message_id=(ctx.message_id or None) if not ctx.is_dm else None,
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
            # DMs live in the guild-0 bucket, separate from the server index. A DM is only
            # ever recalled inside that same 1:1 conversation — never another member's, and
            # never in a channel. Being a server admin does NOT widen this: Manage Server is
            # a permission over the server's own channels, and a member who DMs the bot has
            # no way to know an admin could read it back out, nor any way to refuse.
            dm_channel = ctx.channel_id if ctx.is_dm else None
            block = await search_messages(
                ctx.session,
                guild_id=ctx.cfg_guild,
                query=args.get("query", ""),
                readable=ctx.readable(),
                dm_channel_id=dm_channel,
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
            if ctx.images >= IMAGES_PER_REPLY:
                return _IMAGE_CAP_NOTE.format(cap=IMAGES_PER_REPLY)
            if not await image_is_configured():
                return (
                    "Image generation isn't set up on this server — tell the user "
                    "you can't make images right now."
                )
            ctx.images += 1  # counted before the call: a failed one can still be billed
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
            result = await ctx.actions.send_image(data, filename=f"image.{ext}")
            if result != "image posted":
                return result  # surface the failure reason to the model
            await _note_activity(
                ctx,
                kind="image",
                text=prompt,
                guild_id=0 if ctx.is_dm else ctx.cfg_guild,
                channel_id=ctx.channel_id,
                request_message_id=ctx.message_id,
            )
            return (
                f"Posted the image you generated for: {prompt!r}. Now add a short, "
                "natural caption in your own voice — don't describe it in detail."
            )

        if name == "set_status":
            if ctx.actions is None:
                return "Can't set status from here."
            text = (args.get("text") or "")[:128]
            result = await ctx.actions.set_status(text)
            if result.startswith(STATUS_OK):
                await _note_activity(
                    ctx,
                    kind="status",
                    text=text,
                    guild_id=0 if ctx.is_dm else ctx.cfg_guild,
                    channel_id=ctx.channel_id,
                    request_message_id=ctx.message_id,
                )
            return result

        if name == "react":
            if ctx.actions is None:
                return "Can't react from here."
            return await ctx.actions.react(args.get("emoji") or "")

        if name == "acknowledge":
            return await _acknowledge(args.get("emoji") or DEFAULT_ACK_EMOJI, ctx)

        if name == "send_dm":
            if ctx.actions is None:
                return "Can't send DMs from here."
            target = args.get("user_id") or ctx.user_id
            return await ctx.actions.send_dm(target, args.get("message") or "")

        if name == "send_to_channel":
            if ctx.actions is None:
                return "Can't post to channels from here."
            cfg = await ctx.session.get(GuildConfig, ctx.cfg_guild)
            blocked = list(cfg.blocked_mentions or []) if cfg else list(MASS_MENTIONS)
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

        if name in self_settings.TOOL_NAMES:
            if not ctx.settings_allowed:
                return "Settings aren't available here. Answer without them."
            return await self_settings.run(name, args, ctx)

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
        await _recover_session(ctx)
        return f"Tool {name} errored."


async def _recover_session(ctx: ToolContext) -> None:
    """Roll back a session a failed tool left unusable (a flush or commit that raised).

    Left as it was, the next thing to touch it raised PendingRollbackError: the PIN gate on
    the model's next call, then the reply's own commit, and no reply was sent at all. Only
    a session in that state is rolled back. One that's still usable is left alone, so what
    earlier calls in this reply added (a remembered fact, a reminder) still gets saved."""
    session = ctx.session
    if session is None or session.is_active:
        return
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001
        log.exception("couldn't roll back after a failed tool")


async def _commit_tool_writes(name: str, ctx: ToolContext) -> None:
    """Commit what a tool wrote as soon as it's done.

    SQLite has one writer at a time, and a reply's session holds that lock from its first
    write until it commits. Left open, it stays held through every model call after the
    tool, several seconds each, and every message the bot tries to store in the meantime
    waits and then fails with "database is locked". Committing here keeps it to the tool's
    own write. What was written stays written if the reply fails later on, as it would have
    for the PIN prompt (_confirm_with_pin), which let go of it the same way."""
    session = ctx.session
    if session is None or not session.in_transaction():
        return
    try:
        await session.commit()
    except Exception:
        log.exception("couldn't commit what %s wrote", name)
        await _recover_session(ctx)


async def _confirm_with_pin(name: str, args: dict, ctx: ToolContext) -> str:
    """Put a PIN prompt in the channel and wait for it. Returns an ``olisar.toolpin``
    outcome; anything but ``APPROVED`` means the call doesn't run.

    Fails closed. A tool gated with no PIN set, or gated somewhere there's no Discord
    surface to ask in (the console's test chat), is refused rather than waved through:
    the point of the gate is that the call doesn't happen unless a person said so.
    """
    state = await toolpin.get_state(ctx.session)
    ask = getattr(ctx.actions, "request_pin", None)
    if not state.is_set or ask is None:
        return toolpin.UNAVAILABLE
    # Let go of the reply's write transaction before parking on a human. SQLite locks the
    # whole database for a writer, so holding one open for the length of a PIN prompt would
    # stall every other reply the bot is working on.
    try:
        await ctx.session.commit()
    except Exception:
        log.exception("couldn't commit before the PIN prompt; waiting anyway")
    return await ask(
        tool=name,
        guild_id=ctx.cfg_guild,
        user_id=ctx.user_id,
        timeout=float(state.timeout_sec),
        details=toolpin.describe(name, args),
    )


async def _asker_in_guild(ctx: ToolContext) -> bool:
    """Whether the person this reply answers is a member of the server it acts on.

    Always so in a server channel, where they just posted. In a DM the server is the home
    one, and anyone who shares some server with the bot can DM it, so Discord is asked, once
    per reply. A failed lookup counts as not a member."""
    if not ctx.is_dm:
        return True
    if ctx.in_guild is None:
        check = getattr(ctx.actions, "is_member", None)
        try:
            ctx.in_guild = bool(check and await check(ctx.user_id, ctx.cfg_guild))
        except Exception:  # noqa: BLE001
            log.exception("couldn't check whether %s is in guild %s", ctx.user_id, ctx.cfg_guild)
            ctx.in_guild = False
    return ctx.in_guild


_NOT_A_MEMBER = (
    "Not available: that tool is for members of the server, and the person you're talking "
    "to isn't one. Answer without it, and don't share anything about the server."
)


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> str:
    """Run a tool, logging the call and a one-line summary of what it returned
    (search-type tools also log the specific items they used, in their modules)."""
    log.info("tool call: %s(%s)", name, ", ".join(f"{k}={v!r}" for k, v in args.items()))
    # Ahead of the PIN gate: nobody should be asked to confirm a call that can't run.
    if name in GUILD_TOOLS and not await _asker_in_guild(ctx):
        log.info("tool %s refused: %s isn't a member of guild %s", name, ctx.user_id, ctx.cfg_guild)
        if name in ACTION_TOOLS:
            ctx.failed.append(name)
        return _NOT_A_MEMBER
    action = await toolpin.gate(ctx.session, ctx.cfg_guild, name)
    if action and action not in ctx.pin_approved:
        outcome = ctx.pin_denied.get(action) or await _confirm_with_pin(name, args, ctx)
        if outcome != toolpin.APPROVED:
            ctx.pin_denied[action] = outcome
            ctx.failed.append(name)
            log.info("tool %s refused by the PIN gate (%s)", name, outcome)
            return toolpin.denial_note(name, outcome)
        ctx.pin_approved.add(action)
    # Recorded after the PIN gate, so a call that never ran doesn't count as one that did.
    ctx.tools_run.append(name)
    writes = ctx.settings_writes
    result = await _dispatch(name, args, ctx)
    await _commit_tool_writes(name, ctx)
    log.info("tool result: %s -> %s", name, _summarize(result))
    if name in ACTION_TOOLS and not _went_through(name, result, ctx.settings_writes > writes):
        ctx.failed.append(name)
    return result


def _went_through(name: str, result: str, wrote_settings: bool) -> bool:
    """Whether an action tool's call did what it was asked. The settings writes add an
    audit row for every change they make, so that's their test; the rest report success
    in a fixed form (``_ACTION_DONE``)."""
    if name in self_settings.TOOL_NAMES:
        return wrote_settings
    return str(result or "").startswith(_ACTION_DONE.get(name, ()))
