"""Core reply pipeline: context -> persona -> Gemini -> text.

Discord-agnostic on purpose, so the message listener and the /ask command share
it. The caller handles Discord I/O (typing, sending, recording the reply).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from olisar import prompt_overrides
from olisar.config import settings
from olisar.context import (
    CHANNEL_TASK_HISTORY_NOTE,
    CHANNEL_TASK_NOTE,
    CONTEXT_NOTE,
    build_contents,
    build_task_contents,
    channel_note,
    people_directory,
)
from olisar.db.models import GuildConfig, Persona
from olisar.gemini.client import get_gemini, safe_text, was_truncated
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.retriever import recall, server_memory
from olisar.memory.writer import ACK_MARKER
from olisar.message_links import channel_filter, link_ids, strip_unoffered_links
from olisar.messages import DEFAULT_COMMAND_MESSAGES, render_message
from olisar.persona import (
    DEFAULT_PERSONA_NAME,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TONE_NOTES,
    SPLIT_MARKER,
    build_system_prompt,
    split_messages,
    strip_breaks,
)
from olisar.proactivity import first_emoji
from olisar.extensions import GatheredExtensions, gather_enabled
from olisar.tools import (
    LOOKUP_TOOLS,
    SANDBOX_TOOL_NAMES,
    TOOLS,
    DiscordActions,
    ToolContext,
    ack_declarations,
    execute_tool,
    presence_declarations,
    sandbox_tools,
    tools_with_extensions,
    with_settings_tools,
)

log = logging.getLogger("olisar.pipeline")

# Defaults for the fixed fallbacks; admins override them as command replies
# ("blank_fallback" / "rate_limit"), resolved per-reply in generate_reply.
FALLBACK_EMPTY = DEFAULT_COMMAND_MESSAGES["blank_fallback"]
FALLBACK_RATELIMIT = DEFAULT_COMMAND_MESSAGES["rate_limit"]


@dataclass(frozen=True)
class Reply:
    """What :func:`generate_reply` produces: the text, and whether it is an apology.

    ``blanked`` marks the reply as the blank fallback — the model produced nothing usable
    and the user is being asked to rephrase. The cogs use it to offer a report button, so
    the distinction has to be carried rather than re-derived: from the outside a blank is
    just a string, and a caller comparing text against the operator's configured fallback
    would be guessing at something the pipeline already knows.

    ``silent`` marks a turn Olisar chose to end with a reaction instead of a message (the
    ``acknowledge`` tool — see ``olisar.tools``). ``text`` is empty and nothing is sent;
    ``emoji`` is what it reacted with, which the caller needs to record the turn. It is a
    separate flag rather than "``text`` came back empty" because those mean opposite
    things: an empty reply is the failure path, and this is a reply that worked.

    ``str(reply)`` is the text, so a caller that only wants to send it can.
    """

    text: str
    blanked: bool = False
    silent: bool = False
    emoji: str = ""

    def __str__(self) -> str:
        return self.text

MAX_TOOL_ITERS = 6

# Per-reply cap on the *lookup* tools. Left alone, the model re-queries these with
# slightly reworded arguments until the iteration budget is gone and it never gets to
# an answer — which is how a bare "test" in a DM spent six rounds re-searching the
# question from several turns earlier, then fell through to the failure path. Action
# tools (react, reminders, sends, remember…) stay uncapped: repeating those is the
# user asking for several things, not the model spinning.
LOOKUP_CALL_CAP = 3
LOOKUP_CAP_NOTE = (
    "You've already used {name} {cap} times for this reply — that's the limit. Do not "
    "call it again; answer the user now with what you already have, and say plainly "
    "what you couldn't find."
)

# When a reply hits the output-token ceiling (finish_reason MAX_TOKENS) it gets cut
# off mid-sentence. We ask the model to keep going and stitch the pieces, up to this
# many extra rounds. A rate-limit/error during a continuation just sends what we have.
MAX_CONTINUATIONS = 2
CONTINUE_NUDGE = (
    "Your previous message was cut off because it hit the length limit. Continue it "
    "EXACTLY where it stopped — pick up from the last character, do not repeat or "
    "re-summarize anything you already wrote, and don't add a preamble."
)

_TOOLS_HEADER = (
    "You have tools — use them when they genuinely help, gather what you need in as few "
    "calls as you can, then answer. Never call a tool with empty arguments, and don't "
    "announce that you're using one — just use it and reply.\n"
)

# One line per tool, keyed by the declaration name, so the briefing can be rendered for
# whatever tool set a given call actually has. Grouped entries are keyed by their first
# tool — they're declared and withheld together.
_TOOL_LINES: dict[str, str] = {
    "query_knowledge": "- query_knowledge — anything about this community's own docs, guides, or lore.\n",
    "recall_memory": (
        "- recall_memory — when someone references something older than the visible chat; "
        "it's about you and the person you're talking to.\n"
    ),
    "search_messages": (
        "- search_messages — dig a specific fact out of the WHOLE server's history (e.g. "
        "'what's the server's X/Twitter', 'where was that link posted'). Returns candidate "
        "messages with jump-links.\n"
    ),
    "remember": (
        "- remember — a durable fact about a person; remember_server_fact — a durable fact "
        "about the community itself (the shared glossary).\n"
    ),
    "send_dm": "- send_dm — message someone privately by their id from the people directory.\n",
    "send_to_channel": (
        "- send_to_channel — post to ANOTHER channel when asked to relay / announce / "
        "'tell #x …'; give the channel by name or id from the channel directory. Don't use "
        "it to reply to the channel you're already in — just answer there.\n"
    ),
    "catchup": "- catchup — summarise what someone missed in this channel.\n",
    "add_reminder": (
        "- add_reminder / list_reminders / cancel_reminder — schedule and manage reminders; "
        "use the current time above to resolve 'in 10 min' or 'tomorrow at 9'.\n"
    ),
    "web_search": (
        "- web_search — current info from the OUTSIDE world that you don't already know.\n"
    ),
    "generate_image": (
        "- generate_image — when someone asks you to draw / make / imagine a picture; it "
        "posts to the channel and you just add a short caption.\n"
    ),
    "react": "- react / set_status — a light, alive touch.\n",
    "acknowledge": (
        "- acknowledge — end the turn with a reaction and no message. Once you've done what "
        "was asked — a DM sent, something posted, a fact remembered, a reminder set — this "
        "REPLACES the sentence you'd have written about it: \"done\", \"got it\", \"sent\", "
        "\"noted\", \"written down\", \"i'll remember that\". Send the reaction instead of "
        "the sentence, not as well as it. Same for a message that only needs acknowledging "
        "(\"thanks\", \"sounds good\", an fyi you have nothing to add to). Not for a "
        "question, and not when something went wrong — say so.\n"
    ),
}

# Only meaningful when both tools are present — it's a rule about choosing between them.
_SERVER_QUESTIONS_NOTE = (
    "IMPORTANT: any question about THIS server — its X/social accounts, links, invites, "
    "history, announcements, or who-said-what / when-was — uses search_messages, NOT "
    "web_search (web_search is only for the outside world: news, general facts). "
)

# The replacement when the server's history ISN'T reachable. Without this the model is told
# to answer server questions with a tool it hasn't got, and the only ways to comply are to
# invent the answer or to narrate a search that never happened — which is exactly what it
# did. Telling it plainly that it can't look costs nothing and removes the trap.
_NO_HISTORY_NOTE = (
    "IMPORTANT: you can't search this server's message history here. If someone asks about "
    "something posted in the server — a link, an announcement, who said what, when "
    "something happened — don't guess, don't answer from what seems likely, and never "
    "describe searching or checking logs you have no access to. Say you don't know the way "
    "a person does — \"no idea\", \"wasn't around for that\", \"someone else'll know\" — not "
    "by narrating what you can and can't access. And don't accept a premise you have no "
    "basis for: if you're told you were somewhere or saw something, you don't remember it "
    "and shouldn't play along. "
)

_CITE_NOTE = (
    "Only cite or link a source when you used web_search; for the knowledge base, message "
    "search, and every other tool, answer in your own words with no source tags or "
    "'(source: …)' labels."
)

# The briefing when past messages come with jump-links (search_messages, and the older
# messages recall adds to memory). A bare message link is what a person pastes in Discord,
# and it renders as a chip that opens the message, so a citation reads as someone pointing
# rather than as a footnote. Links the model wasn't given are stripped on the way out
# (olisar.message_links.strip_unoffered_links).
_CITE_MESSAGES_NOTE = (
    "Answer in your own words, with no source tags or '(source: …)' labels. Two exceptions: "
    "name the source when a fact came from web_search, and when your answer rests on one "
    "specific past message (a search result, or an older message in your memory), paste its "
    "jump-link right after the sentence it backs up, bare, the way people link a message in "
    "chat. One link per answer is usually plenty. Only paste links you were given, and none "
    "for anything already in the visible chat."
)


def render_tools_note(available: set[str] | None = None) -> str:
    """The tool briefing for the tools a call actually has.

    Rendering it per tool set rather than as one fixed block fixes a real mismatch: the
    dashboard's test chat declares only ``query_knowledge`` and ``web_search`` (see
    ``olisar.tools.sandbox_tools``) but was handed the full briefing for twelve, told to
    "use your tools normally", and instructed to answer server questions with
    ``search_messages``. An instruction that cannot be obeyed doesn't produce a refusal, it
    produces a confabulation — the model invents the lookup it was told to perform. That is
    a defect in what operators are shown, not only in the test harness: the test chat was
    demonstrating behaviour the live bot doesn't have.
    """
    names = _ALL_TOOL_KEYS if available is None else available
    body = "".join(line for key, line in _TOOL_LINES.items() if key in names)
    if "search_messages" in names:
        tail = _SERVER_QUESTIONS_NOTE + _CITE_MESSAGES_NOTE
    else:
        tail = _NO_HISTORY_NOTE + _CITE_NOTE
    return _TOOLS_HEADER + body + tail


_ALL_TOOL_KEYS = frozenset(_TOOL_LINES)
# Everything a server has by default. ``acknowledge`` is per-guild
# (``GuildConfig.silent_acks_enabled``) and joins the set in ``generate_reply`` when it's
# on, so the module-level briefing keeps describing the tools every install actually has.
_CORE_TOOL_KEYS = _ALL_TOOL_KEYS - {"acknowledge"}

TOOLS_NOTE = render_tools_note(_CORE_TOOL_KEYS)


# Folded into the system prompt for a DM so Olisar knows it's a private one-on-one, not
# a server channel — kept subtle (it still has all its usual knowledge, memory, and tools).
DM_NOTE = (
    "This is a private direct message — just you and this person, no server channel and no "
    "one else watching. You keep all your usual knowledge, memory, and tools. Keep it "
    "personal and one-on-one, and don't act as if other members or channels are here."
)


def _without_invented_links(text: str, system_instruction: str, contents: list) -> str:
    """``text`` minus any Discord message link the model wasn't given this turn.

    "Given" is anything it could have copied from: the system prompt (recall's older
    messages), the chat history and the message being answered, and every tool result,
    all of which end up in ``contents`` by the time the tool loop returns. A link that
    isn't in any of them was written from nothing, or copied with a digit slipped, and
    either way opens the wrong message or none."""
    offered = link_ids(system_instruction)
    for content in contents:
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "text", None):
                offered |= link_ids(part.text)
            response = getattr(getattr(part, "function_response", None), "response", None)
            if response:
                offered |= link_ids(str(response.get("result", "")))
    cleaned, removed = strip_unoffered_links(text, offered)
    if removed:
        log.warning("removed %d message link(s) the model wasn't given: %s", len(removed), removed)
    return cleaned


def _function_calls(resp) -> list:
    """Pull function calls from a response. Prefers ``resp.function_calls`` but
    falls back to scanning the candidate parts directly — some SDK/AFC states
    leave ``function_calls`` empty while a ``function_call`` part is present, which
    would otherwise drop the tool call and yield an empty reply."""
    calls = list(resp.function_calls or [])
    if calls:
        return calls
    try:
        for part in resp.candidates[0].content.parts:
            if getattr(part, "function_call", None):
                calls.append(part.function_call)
    except Exception:
        pass
    return calls


FINAL_ANSWER_NUDGE = (
    "Now answer the user directly, in plain text, using what you gathered above. "
    "Do not call any more tools. If you found relevant messages, summarize the "
    "answer; if you genuinely found nothing, say so plainly."
)

# Tool results that are prompts-for-input or not-found aren't real progress, so we
# don't keep them for the graceful fallback (e.g. a no-arg call that returns "Give
# me a commodity…", or a "No commodity matching…" miss).
_UNHELPFUL_PREFIXES = (
    # A PIN-gated call that nobody confirmed (olisar/toolpin.py) is an instruction to the
    # model, not a result — it must never count as something the reply gathered.
    "denied:",
    "no matching", "nothing", "give me", "tell me which", "couldn't reach",
    "couldn't find", "uex error", "uex returned", "that uex endpoint needs",
    "no commodity", "no vehicle", "no location", "no star system", "no planet",
    "no moon", "no point of interest", "no jump points", "no item",
    "no profitable", "no live terminal", "no in-game", "no pledge", "no origin",
)


def _useful(result: str) -> bool:
    """Whether a tool result is real data worth keeping — vs a miss or an
    argument-prompt — used to gate the graceful fallback so wasted calls don't
    crowd out (or stand in for) the results that actually answer the question."""
    return bool(result) and not result.lstrip().lower().startswith(_UNHELPFUL_PREFIXES)


def _response_text(resp) -> str:
    """Plain text from a tool-enabled response. ``safe_text`` (``resp.text``) already
    drops function_call parts; we also scan parts directly as a belt-and-suspenders
    for SDK states where ``.text`` is unavailable."""
    text = safe_text(resp)
    if text:
        return text
    try:
        return " ".join(
            p.text for p in resp.candidates[0].content.parts if getattr(p, "text", None)
        ).strip()
    except Exception:
        return ""


# A tool call the model typed as its reply instead of making it. The fallback models do
# this: a bot sent `react(emoji="🔥")` to a channel as a whole reply, and Flash 3 preview
# answers an emoji-only message with `[reacted 🔥]`, the transcript's own marker for a
# reaction, more often than not. Gemini's older tool-code form wraps the same call as
# `print(default_api.react(emoji="🔥"))` inside a ```tool_code fence.
#
# The model is asked again with this rule added to its instructions, and the conversation
# left as it was. Telling it in the conversation read as the person it was talking to
# complaining, and it apologised to them: "my bad. thumb slipped", "no tool calls from me".
TYPED_CALL_RULE = (
    "Whatever you write is posted to the channel exactly as written, so never write out a "
    "tool call or a `[reacted …]` line: a tool only runs when you call it. To react, call "
    "the tool; otherwise reply in words. Don't mention tools or this rule."
)
_MARKER_HEAD, _MARKER_TAIL = ACK_MARKER.split("{emoji}")
# The marker opening a piece, with whatever the model wrote after it on the same line.
_MARKER_RE = re.compile(
    re.escape(_MARKER_HEAD) + r"(\S+?)" + re.escape(_MARKER_TAIL) + r"\s*(.*)", re.DOTALL
)
# ...and the marker without its brackets, as a whole piece: "reacted 🔥".
_BARE_MARKER_RE = re.compile(re.escape(_MARKER_HEAD.strip("[ ")) + r"\s+(\S+)", re.IGNORECASE)
_TOOL_FENCE_RE = re.compile(r"`{3}\s*tool_(?:code|call)\s*")
_REACTION_TOOLS = frozenset({"react", "acknowledge"})


def _declared(tools: list) -> set[str]:
    return {d.name for t in tools for d in (t.function_declarations or [])}


def _typed_call(text: str, names: set[str]) -> tuple[str, dict] | None:
    """``text`` as a tool call the model typed instead of making, or None.

    Read as a Python expression, since that's the shape Gemini writes calls in, and
    ``ast`` parses one without running it. Only a bare call (or ``default_api.``'s) to a
    tool this reply declares, with literal keyword arguments, counts, so a line of code in
    an ordinary answer doesn't."""
    text = text.strip().strip("`").strip()
    # Not only SyntaxError: a long run of unary minus signs is a MemoryError in the parser,
    # and literal_eval raises TypeError for an unhashable set. Anything that fails is prose.
    try:
        node = ast.parse(text, mode="eval").body
    except Exception:  # noqa: BLE001
        return None
    if (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "print" and len(node.args) == 1 and not node.keywords
    ):
        node = node.args[0]
    if not isinstance(node, ast.Call) or node.args:
        return None
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
    elif (
        isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
        and func.value.id == "default_api"
    ):
        name = func.attr
    else:
        return None
    if name not in names:
        return None
    try:
        args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
    except Exception:  # noqa: BLE001
        return None
    return None if None in args else (name, args)


def _split_typed_calls(text: str, names: set[str]) -> tuple[list[tuple[str, dict]], str]:
    """The tool calls typed out in ``text``, and the text left without them.

    A call is the whole reply, a line to itself, or a piece of a line between [[break]]
    markers. The reaction marker also counts when words follow it on its line; they stay.
    Without its brackets it only counts alone, since "reacted to that" is a sentence.
    Lines inside an ordinary code fence are left alone, since that's an answer showing
    code. Only a ```tool_code fence is read, the form Gemini leaks calls in, and the fence
    goes with them."""
    whole = _typed_call(text, names)  # also catches a call spread over several lines
    if whole:
        return [whole], ""
    calls: list[tuple[str, dict]] = []
    kept: list[str] = []
    fence = ""  # "tool" or "code" while inside one
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            opening = not fence
            if opening:
                fence = "tool" if _TOOL_FENCE_RE.fullmatch(line.strip()) else "code"
            was_tool = fence == "tool"
            if not opening:
                fence = ""
            if not was_tool:
                kept.append(line)
            continue
        if fence == "code":
            kept.append(line)
            continue
        pieces = []
        for piece in line.split(SPLIT_MARKER):
            call = _typed_call(piece, names)
            if call:
                calls.append(call)
                continue
            marker = _MARKER_RE.match(piece.strip())
            if marker:
                calls.append(("acknowledge", {"emoji": marker.group(1)}))
                piece = marker.group(2)
            bare = _BARE_MARKER_RE.fullmatch(piece.strip().strip("*_"))
            if bare and first_emoji(bare.group(1)) == bare.group(1):
                calls.append(("acknowledge", {"emoji": bare.group(1)}))
                continue
            pieces.append(piece)
        kept.append(SPLIT_MARKER.join(pieces))
    rest = "\n".join(kept)
    # What split_messages would send. Nothing, when all that's left is break markers.
    return calls, (rest.strip() if split_messages(rest) else "")


async def _settle_typed_calls(text: str, ctx: ToolContext, tools: list) -> str | None:
    """``text`` ready to send once any tool call typed out in it is dealt with, or None
    when the model has to be asked again. A turn it ended with a reaction instead comes
    back as ``""`` with ``ctx.silent`` set, as when the model calls ``acknowledge``.

    A reaction typed as the whole reply is made through ``acknowledge``, with that tool's
    refusals, and only where the server has it on and nothing but a reaction has run this
    turn: after a lookup or an action, a reaction could stand in for a result or a failure,
    and the model is better asked again, where calling acknowledge gets its checks. A
    typed ``[reacted 👍]`` after a real ``react`` call is the model saying that was all.

    A reaction typed next to words is made with ``react``, and the words are sent. A call
    to a tool that already ran this turn is the model describing it, and is dropped. Any
    other call is asked again, because the words around it may say it happened, and it
    didn't."""
    names = _declared(tools)
    calls, rest = _split_typed_calls(text, names)
    if not calls:
        return text
    log.warning("the model typed a tool call as its reply; not sending it: %r", text[:300])
    if any(name not in _REACTION_TOOLS and name not in ctx.tools_run for name, _ in calls):
        return None
    reactions = [str(args.get("emoji") or "") for name, args in calls if name in _REACTION_TOOLS]
    if not reactions:
        return rest
    if rest:
        emoji = first_emoji(reactions[-1])
        if emoji and "react" in names:
            await execute_tool("react", {"emoji": emoji}, ctx)
        return rest
    if set(ctx.tools_run) <= _REACTION_TOOLS and "acknowledge" in names:
        await execute_tool("acknowledge", {"emoji": reactions[-1]}, ctx)
        if ctx.silent:
            return ""
    return None


def _fallback_when_synthesis_fails(blank_fallback: str, gathered: list[str]) -> str:
    """Last resort when both synthesis and the forced final answer fail.

    This used to paste every gathered tool result into the reply so nothing was "dropped
    behind the apology". That made the failure path an exfiltration path: tool results are
    written *for the model*, not the user, and they carry other members' verbatim messages
    (search_messages), their names and timestamps, internal instruction headers, and tool
    error strings like "answer from what you know" — all of which reached a user verbatim
    in a real incident, including private DMs the requester was never meant to see.

    Nothing the tools return is safe to show raw, so nothing is. What we gathered is logged
    for the operator instead; the user gets the ordinary blank fallback and can retry.
    """
    if gathered:
        log.warning(
            "synthesis failed after %d tool result(s); replying with the blank fallback "
            "rather than surfacing raw tool output",
            len(gathered),
        )
    return blank_fallback


async def _complete_truncated(
    client, contents: list, system_instruction: str, model: str | None, tools: list,
    resp, text: str,
) -> str:
    """If ``resp`` was cut off at the token ceiling, ask the model to continue and
    stitch the pieces so the reply finishes instead of stopping mid-sentence. Bounded
    by ``MAX_CONTINUATIONS``; a rate-limit/error mid-continuation returns what we have
    so far (the partial is never thrown away)."""
    if not was_truncated(resp):
        return text
    log.info(
        "reply truncated at token cap (%d chars); attempting up to %d continuation(s)",
        len(text), MAX_CONTINUATIONS,
    )
    pieces = [text]
    cur = resp
    for n in range(MAX_CONTINUATIONS):
        if not was_truncated(cur):
            break
        try:
            contents.append(cur.candidates[0].content)  # the partial we just got
        except Exception:
            break
        contents.append(types.Content(role="user", parts=[types.Part(text=CONTINUE_NUDGE)]))
        try:
            cur = await client.generate_with_tools(
                contents=contents,
                system_instruction=system_instruction,
                tools=tools,
                model=model,
                force_text=True,
                source="conversation",
            )
        except Exception:
            log.exception("continuation %d after truncation failed; sending the partial", n + 1)
            break
        more = _response_text(cur)
        if not more:
            log.info("continuation %d returned no text; sending what we have", n + 1)
            break
        log.info("continuation %d added %d chars", n + 1, len(more))
        pieces.append(more)
    stitched = "".join(pieces)
    if was_truncated(cur):
        log.warning(
            "reply still truncated after %d continuation(s) (%d chars total)",
            MAX_CONTINUATIONS, len(stitched),
        )
    return stitched


async def _force_final_answer(
    client, contents: list, system_instruction: str, model: str | None, tools: list
) -> str:
    """Close out the loop with a plain-text answer. First bar tool-calling
    (mode=NONE); if a weaker fallback model emits a function_call anyway and returns
    no text, retry with NO tools in the request at all — it then physically cannot
    call a tool and must synthesize from the results already in context."""
    nudged = system_instruction + "\n\n" + FINAL_ANSWER_NUDGE
    try:
        resp = await client.generate_with_tools(
            contents=contents,
            system_instruction=nudged,
            tools=tools,
            model=model,
            force_text=True,
            source="conversation",
        )
        text = _response_text(resp)
        if text:
            return await _complete_truncated(client, contents, nudged, model, tools, resp, text)
    except RateLimitExceeded:
        raise  # generate_reply says "I'm rate limited" — see the note below
    except Exception:
        log.exception("forced final answer (tools barred) failed")
    # The model kept trying to call tools — remove tools entirely so it can't, and
    # let it write the summary from the gathered results already in `contents`. Retry
    # once on an empty candidate (a transient empty response here is what otherwise
    # drops us to the out-of-character raw-results fallback).
    #
    # A rate limit must NOT be swallowed here. Returning "" is indistinguishable from
    # "the model had nothing to say", so an exhausted quota used to surface as the
    # generic blank fallback ("my mind went blank") instead of the rate_limit reply the
    # admin configured — leaving no hint that waiting would fix it.
    for attempt in range(2):
        try:
            result = await client.generate(
                contents=contents,
                system_instruction=nudged,
                model=model,
                max_output_tokens=1024,  # match the other chat paths so this fallback isn't the one that cuts off
                source="conversation",
            )
            if result.text:
                return result.text
            log.warning("forced final answer (no tools) returned empty (attempt %d/2)", attempt + 1)
        except RateLimitExceeded:
            raise
        except Exception:
            log.exception("forced final answer (no tools) failed")
            break
    return ""


async def _run_tool_loop(
    contents: list,
    system_instruction: str,
    model: str | None,
    ctx: ToolContext,
    blank_fallback: str = FALLBACK_EMPTY,
    tools: list = TOOLS,
) -> str:
    """Generate with tools, executing any function calls and looping until the
    model returns a plain text answer (or the iteration budget is spent)."""
    client = get_gemini()
    gathered: list[str] = []  # every useful tool result, for the graceful fallback
    lookups: dict[str, int] = {}  # lookup tool -> calls so far this reply
    for _ in range(MAX_TOOL_ITERS):
        resp = await client.generate_with_tools(
            contents=contents,
            system_instruction=system_instruction,
            tools=tools,
            model=model,
            source="conversation",
        )
        calls = _function_calls(resp)
        if not calls:
            text = await _settle_typed_calls(_response_text(resp), ctx, tools)
            if text is None:
                if TYPED_CALL_RULE not in system_instruction:
                    system_instruction += "\n\n" + TYPED_CALL_RULE
                continue
            if ctx.silent:
                return ""
            if text:
                full = await _complete_truncated(
                    client, contents, system_instruction, model, tools, resp, text
                )
                # A continuation is the model writing too, and can hold a typed call.
                return full if full == text else _split_typed_calls(full, _declared(tools))[1]
            break  # no calls and no text — go force a final answer

        contents.append(resp.candidates[0].content)  # the model's tool-call turn
        responses = []
        for call in calls:
            if call.name in LOOKUP_TOOLS:
                lookups[call.name] = lookups.get(call.name, 0) + 1
                if lookups[call.name] > LOOKUP_CALL_CAP:
                    log.info(
                        "%s called %d times this reply — capped at %d, telling the model "
                        "to answer with what it has",
                        call.name, lookups[call.name], LOOKUP_CALL_CAP,
                    )
                    responses.append(
                        types.Part.from_function_response(
                            name=call.name,
                            response={
                                "result": LOOKUP_CAP_NOTE.format(
                                    name=call.name, cap=LOOKUP_CALL_CAP
                                )
                            },
                        )
                    )
                    continue  # not a real result — never goes into `gathered`
            result = await execute_tool(call.name, dict(call.args or {}), ctx)
            if _useful(result):
                gathered.append(result)
            responses.append(
                types.Part.from_function_response(
                    name=call.name, response={"result": result}
                )
            )
        if ctx.silent:
            # `acknowledge` landed: the reaction is the reply and this turn is over. Return
            # before the forcing machinery below, which exists to stop an empty reply and
            # would here manufacture the "done" the reaction was chosen instead of.
            unsent = _response_text(resp).strip()
            if unsent:
                log.info("not sending alongside the %s reaction: %s", ctx.silent, unsent)
            return ""
        # Function responses go back as a "user" turn: the SDK documents role as
        # "either 'user' or 'model'", and Gemini 3.x enforces it (older 2.x models
        # silently tolerated role="tool", which is what this used to send).
        contents.append(types.Content(role="user", parts=responses))
        if ctx.settings_open:
            # open_settings ran, so the reply is about settings: declare the tools that
            # change them from the next call on.
            tools = with_settings_tools(tools)

    # Budget spent (or an empty turn) — force a plain-text final answer. Barred from calling
    # tools, or given none, the model is likelier still to type one out.
    answer = await _force_final_answer(client, contents, system_instruction, model, tools)
    if answer:
        answer = await _settle_typed_calls(answer, ctx, tools)
    if ctx.silent:
        return ""
    if answer:
        return answer

    # Last resort: apologize. Raw tool results are never shown to the user — see below.
    return _fallback_when_synthesis_fails(blank_fallback, gathered)


def _persona_prompt(persona: Persona | None, runtime_note: str = "") -> str:
    """The server's persona as a system instruction, or the default one for a server that
    hasn't customized it."""
    if persona is None:
        return build_system_prompt(
            persona_name=DEFAULT_PERSONA_NAME,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tone_notes=DEFAULT_TONE_NOTES,
            runtime_note=runtime_note,
        )
    return build_system_prompt(
        persona_name=persona.name,
        system_prompt=persona.system_prompt,
        tone_notes=persona.tone_notes,
        runtime_note=runtime_note,
        server_type=persona.server_type,
        slang_density=persona.slang_density,
    )


async def generate_reply(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    current_message_id: int,
    bot_user_id: int,
    user_id: int,
    display_name: str,
    user_text: str,
    actions: DiscordActions | None = None,
    runtime_note: str = "",
    images: list[tuple[bytes, str, str]] | None = None,
    home_guild_id: int | None = None,
    reply_to: tuple[str, str] | None = None,
    channel_name: str = "",
    channel_topic: str = "",
) -> Reply:
    """Produce Olisar's reply for one incoming message/prompt.

    ``images`` (``(data, mime)`` pairs from the triggering message) are shown to
    the model directly, so Olisar can react to screenshots/pictures in real time."""
    # DMs (guild_id 0 == DM_GUILD_ID) borrow a home server's persona, knowledge, and tools
    # (cfg_guild — the caller passes a real guild the bot is in via home_guild_id), while the
    # message history stays keyed to the per-user DM channel. Tell the model it's a private
    # one-on-one so it doesn't act like a server channel.
    cfg_guild = guild_id or home_guild_id or settings.target_guild_id
    if not guild_id:
        runtime_note = (DM_NOTE + (("\n\n" + runtime_note) if runtime_note else "")).strip()

    persona = await session.get(Persona, cfg_guild)
    system_instruction = _persona_prompt(persona, runtime_note)
    config = await session.get(GuildConfig, cfg_guild)
    # Ending a turn with a reaction instead of a message is per-server and on by default.
    # Off, the tool is never declared and never described — an operator who turned it off
    # shouldn't have Olisar reading about a way to stay quiet that it hasn't got.
    silent_acks = bool(getattr(config, "silent_acks_enabled", True)) if config else True
    system_instruction += (
        "\n\n" + CONTEXT_NOTE + "\n\n"
        + prompt_overrides.tools_note(
            render_tools_note(_ALL_TOOL_KEYS if silent_acks else _CORE_TOOL_KEYS)
        )
    )
    # Which room this is, so the register can follow it (DMs get DM_NOTE instead).
    room = channel_note(channel_name, channel_topic) if guild_id else ""
    if room:
        system_instruction += "\n\n" + room
    system_instruction += (
        f"\n\nCurrent time (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M} — use it "
        "to resolve any 'remind me' / scheduling request before calling add_reminder."
    )

    model = config.default_model if config and config.default_model else None
    cmd_msgs = config.command_messages if config and config.command_messages else {}
    rate_limit_msg = render_message(cmd_msgs, "rate_limit")
    blank_fallback = render_message(cmd_msgs, "blank_fallback")

    contents, recent_ids = await build_contents(
        session,
        channel_id=channel_id,
        current_message_id=current_message_id,
        bot_user_id=bot_user_id,
        current_display_name=display_name,
        current_text=user_text,
        current_images=images,
        reply_to=reply_to,
        recent_window=(config.context_message_limit if config else None),
        own_name=(persona.name if persona else "") or DEFAULT_PERSONA_NAME,
    )

    # A people directory (name -> id) so Olisar can DM participants by id.
    try:
        directory = await people_directory(
            session,
            channel_id=channel_id,
            current_user_id=user_id,
            current_display_name=display_name,
        )
        if directory:
            system_instruction += "\n\n" + directory
    except Exception:
        log.exception("people directory build failed; continuing without it")

    # A channel directory (name -> id) so Olisar maps a loose channel reference to the real
    # channel itself and posts by id via send_to_channel — instead of guessing at a name.
    if actions is not None:
        try:
            channels = await actions.channel_directory(cfg_guild, requester_id=user_id)
            if channels:
                system_instruction += "\n\n" + channels
        except Exception:
            log.exception("channel directory build failed; continuing without it")

    # Semantic recall — best-effort; a failure here must not block the reply.
    try:
        recalled = await recall(
            session,
            cfg_guild=cfg_guild,
            user_id=user_id,
            query_text=user_text,
            recent_ids=recent_ids,
            channel_id=channel_id,
            readable=channel_filter(
                actions, guild_id=cfg_guild, requester_id=user_id, here=channel_id
            ),
        )
        if recalled:
            system_instruction += "\n\n" + recalled
    except Exception:
        log.exception("recall failed; replying without semantic memory")

    # Enabled extensions contribute extra tools + behaviour notes, read live so a
    # dashboard toggle takes effect on the next reply (best-effort; never blocks).
    ext = GatheredExtensions()
    try:
        ext = await gather_enabled(session, cfg_guild)
    except Exception:
        log.exception("extension gather failed; continuing without extensions")
    # Per-reply tool set: extension tools, plus the situational-awareness tools when
    # the server has opted in (presence is privileged + sensitive, off by default).
    extra_decls = list(ext.declarations)
    if config is not None and getattr(config, "presence_tools_enabled", False):
        extra_decls += presence_declarations()
    for note in ext.notes:
        system_instruction += "\n\n" + note
    if extra_decls:
        system_instruction += "\n\nAlso enabled: " + ", ".join(
            d.name for d in extra_decls
        ) + " — use these when they fit the request."
    # Declared after that line, deliberately. `acknowledge` is described in the tool
    # briefing with the conditions attached; listing it again under "use these when they
    # fit the request" reads as encouragement to stay quiet, which is the last thing this
    # needs a second nudge toward.
    reply_tools = tools_with_extensions(
        extra_decls + (ack_declarations() if silent_acks else [])
    )

    ctx = ToolContext(
        session=session,
        cfg_guild=cfg_guild,
        channel_id=channel_id,
        user_id=user_id,
        display_name=display_name,
        is_dm=guild_id == 0,
        message_id=current_message_id,
        actions=actions,
        extension_tools=ext.handlers,
    )
    try:
        text = await _run_tool_loop(
            contents,
            system_instruction,
            model,
            ctx,
            blank_fallback=blank_fallback,
            tools=reply_tools,
        )
    except RateLimitExceeded:
        # Deliberately not a blank. The quota is spent, waiting fixes it, and the reply
        # says so — there is nothing here for the operator to diagnose or the team to fix.
        return Reply(rate_limit_msg)
    except Exception:
        log.exception("gemini generation failed")
        return Reply(blank_fallback, blanked=True)
    if ctx.silent:
        # Checked ahead of the blank test below: both carry empty text, and reading this
        # one as a blank would hang a Report button off a reply that did exactly what the
        # user asked for.
        return Reply("", silent=True, emoji=ctx.silent)
    text = _without_invented_links(text, system_instruction, contents) or blank_fallback
    # _fallback_when_synthesis_fails returns the very string handed to it, so an equal
    # result is the loop reporting that it never reached an answer. A model that happened
    # to write the operator's fallback text verbatim would also match; the cost of that
    # coincidence is one offered report button on a reply that was fine.
    return Reply(text, blanked=text == blank_fallback)


async def channel_task_prompt(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    channel_name: str,
    channel_topic: str,
    task: str,
    runtime_note: str = "",
) -> tuple[str, list]:
    """The system instruction and ``contents`` for writing into a channel when nobody there
    asked: ``host.generate`` given a ``channelId``, which is how Welcome greets a new member
    in the room they'll arrive in.

    Built from the same pieces as :func:`generate_reply`, so the text comes out as if the bot
    had been called in that channel: the persona with its room settings, the channel's name
    and topic, its recent transcript read by the same rules, and the glossary and
    resource-channel memory every reply carries. Left out is everything that belongs to a
    person asking (their memory, the people directory) and to tools, which this turn can't
    call. ``runtime_note`` is the extension's ``systemNote``."""
    persona = await session.get(Persona, guild_id)
    config = await session.get(GuildConfig, guild_id)
    contents, had_history = await build_task_contents(
        session,
        channel_id=channel_id,
        task=task,
        recent_window=(config.context_message_limit if config else None),
        own_name=(persona.name if persona else "") or DEFAULT_PERSONA_NAME,
    )

    system_instruction = _persona_prompt(persona, runtime_note)
    if had_history:
        system_instruction += "\n\n" + CONTEXT_NOTE
    name = (channel_name or "").lstrip("#").strip()
    system_instruction += "\n\n" + CHANNEL_TASK_NOTE.format(
        channel=f"#{name}" if name else "this channel"
    ) + (CHANNEL_TASK_HISTORY_NOTE if had_history else "")
    room = channel_note(channel_name, channel_topic)
    if room:
        system_instruction += "\n\n" + room
    system_instruction += f"\n\nCurrent time (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}."
    # Best-effort, as recall is on a reply: the text still gets written without it.
    try:
        memory = await server_memory(session, guild_id)
        if memory:
            system_instruction += "\n\n" + memory
    except Exception:
        log.exception("server memory failed; writing into the channel without it")
    return system_instruction, contents


SANDBOX_NOTE = (
    "This is a private SANDBOX test chat with a server administrator — used to try out "
    "your persona, knowledge base, and tools. There is no real Discord channel here and "
    "NO memory: you will not remember anything from this conversation and nothing said "
    "here is saved. Stay fully in character and use your tools normally, but do not "
    "claim you'll remember things for next time."
)


async def generate_sandbox_reply(
    session: AsyncSession,
    *,
    guild_id: int,
    messages: list[dict],
    runtime_note: str = "",
) -> str:
    """Reply for the dashboard's enclosed test chat. Same persona, KB, and tool-calling
    as a live reply, but deliberately memory-free: context is built only from the
    supplied transcript (no channel history), there is NO glossary/semantic recall, no
    message is recorded, and the tool set excludes every memory and Discord-action tool
    (see ``sandbox_tools``). Nothing here touches or pollutes the server's memory."""
    cfg_guild = guild_id or settings.target_guild_id

    persona = await session.get(Persona, cfg_guild)
    system_instruction = _persona_prompt(persona, runtime_note)
    # The briefing for the tools this lane actually declares — not the full one. See
    # render_tools_note: the test chat has query_knowledge and web_search only.
    system_instruction += (
        "\n\n" + SANDBOX_NOTE + "\n\n"
        + prompt_overrides.tools_note(render_tools_note(set(SANDBOX_TOOL_NAMES)))
    )
    system_instruction += f"\n\nCurrent time (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}."

    config = await session.get(GuildConfig, cfg_guild)
    model = config.default_model if config and config.default_model else None
    cmd_msgs = config.command_messages if config and config.command_messages else {}
    rate_limit_msg = render_message(cmd_msgs, "rate_limit")
    blank_fallback = render_message(cmd_msgs, "blank_fallback")

    # Context is ONLY the supplied transcript — no channel read, no recall.
    contents = []
    for m in messages:
        text = (m.get("content") or "").strip()
        if not text:
            continue
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    if not contents:
        return blank_fallback

    # Extensions still contribute their tools + behaviour notes (API-based, no Discord),
    # so the test reflects real KB/extension behaviour. Presence tools are omitted
    # (they need a live guild). Best-effort; never blocks.
    ext = GatheredExtensions()
    try:
        ext = await gather_enabled(session, cfg_guild)
    except Exception:
        log.exception("sandbox extension gather failed; continuing without extensions")
    for note in ext.notes:
        system_instruction += "\n\n" + note

    ctx = ToolContext(
        session=session,
        cfg_guild=cfg_guild,
        channel_id=0,
        user_id=0,
        display_name="Sandbox admin",
        actions=None,  # no Discord side-effects in the sandbox
        extension_tools=ext.handlers,
    )
    try:
        # The sandbox renders one reply as one bubble, so a split marker would show up as
        # literal text there rather than as the extra message it asks for on Discord.
        text = strip_breaks(await _run_tool_loop(
            contents,
            system_instruction,
            model,
            ctx,
            blank_fallback=blank_fallback,
            tools=sandbox_tools(list(ext.declarations)),
        ))
        return _without_invented_links(text, system_instruction, contents) or blank_fallback
    except RateLimitExceeded:
        return rate_limit_msg
    except Exception:
        log.exception("sandbox generation failed")
        return blank_fallback
