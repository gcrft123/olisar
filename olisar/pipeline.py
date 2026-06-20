"""Core reply pipeline: context -> persona -> Gemini -> text.

Discord-agnostic on purpose, so the message listener and the /ask command share
it. The caller handles Discord I/O (typing, sending, recording the reply).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.context import CONTEXT_NOTE, build_contents, people_directory
from olisar.db.models import GuildConfig, Persona
from olisar.gemini.client import get_gemini, safe_text, was_truncated
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.memory.retriever import recall
from olisar.messages import DEFAULT_COMMAND_MESSAGES, render_message
from olisar.persona import (
    DEFAULT_PERSONA_NAME,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TONE_NOTES,
    build_system_prompt,
)
from olisar.extensions import GatheredExtensions, gather_enabled
from olisar.tools import (
    TOOLS,
    DiscordActions,
    ToolContext,
    execute_tool,
    presence_declarations,
    tools_with_extensions,
)

log = logging.getLogger("olisar.pipeline")

# Defaults for the fixed fallbacks; admins override them as command replies
# ("blank_fallback" / "rate_limit"), resolved per-reply in generate_reply.
FALLBACK_EMPTY = DEFAULT_COMMAND_MESSAGES["blank_fallback"]
FALLBACK_RATELIMIT = DEFAULT_COMMAND_MESSAGES["rate_limit"]

MAX_TOOL_ITERS = 6

# When a reply hits the output-token ceiling (finish_reason MAX_TOKENS) it gets cut
# off mid-sentence. We ask the model to keep going and stitch the pieces, up to this
# many extra rounds. A rate-limit/error during a continuation just sends what we have.
MAX_CONTINUATIONS = 2
CONTINUE_NUDGE = (
    "Your previous message was cut off because it hit the length limit. Continue it "
    "EXACTLY where it stopped — pick up from the last character, do not repeat or "
    "re-summarize anything you already wrote, and don't add a preamble."
)

TOOLS_NOTE = (
    "You have tools — recall_memory, search_messages, query_knowledge, remember, "
    "web_search, generate_image, set_status, react, send_dm. Reach for them when "
    "they genuinely help: query_knowledge for anything about this community's own docs/guides/"
    "lore, recall_memory when someone references something older than the visible "
    "chat, search_messages to dig a specific fact out of the WHOLE server's history "
    "(e.g. 'what's the server's X/Twitter account', 'where was that link posted') — "
    "it returns candidate messages with jump-links (share one only when they're "
    "asking where or when something was posted), remember for "
    "durable facts about a person, web_search for current info you don't know, "
    "generate_image when someone asks you to draw/make/imagine a picture (it posts "
    "to the channel — you just add a caption), react / set_status for a light, alive "
    "touch, and send_dm to message someone "
    "privately (by their id from the people directory) when it fits. recall_memory "
    "is about you and the current person; search_messages searches everything anyone "
    "ever posted. IMPORTANT: any question about THIS server — its X/social accounts, "
    "links, invites, history, announcements, or who-said-what / when-was — uses "
    "search_messages, NOT web_search. web_search is only for the outside world "
    "(news, general facts). Only cite or link a source when you used web_search "
    "(the outside web) — for the knowledge base, message search, and any other tool, "
    "answer in your own words with no source tags or '(source: …)' labels. Don't "
    "announce tool use — just use it and reply. When a tool "
    "needs a name or argument, always supply it — never call a tool with empty "
    "arguments — and gather what you need in as few calls as you can, then answer."
)


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


PARTIAL_PREFIX = "couldn't pull a clean summary together just now, but here's what i found:\n\n"


def _strip_internal_header(body: str) -> str:
    """Drop a leading internal-instruction line (e.g. search_messages' "skim these:"
    header) so it isn't shown to the user."""
    if "\n" in body and body.split("\n", 1)[0].rstrip().endswith(":"):
        return body.split("\n", 1)[1]
    return body


def _fallback_from_gathered(blank_fallback: str, gathered: list[str]) -> str:
    """Last resort when synthesis fails: surface *everything* we gathered (deduped,
    order preserved), not just the most recent result, so nothing important is
    dropped behind the apology."""
    seen: set[str] = set()
    items: list[str] = []
    for r in gathered:
        b = r.strip()
        if b and b not in seen:
            seen.add(b)
            items.append(b)
    if not items:
        return blank_fallback
    body = _strip_internal_header(items[0]) if len(items) == 1 else "\n\n".join(items)
    return PARTIAL_PREFIX + body


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
        )
        text = _response_text(resp)
        if text:
            return await _complete_truncated(client, contents, nudged, model, tools, resp, text)
    except Exception:
        log.exception("forced final answer (tools barred) failed")
    # The model kept trying to call tools — remove tools entirely so it can't, and
    # let it write the summary from the gathered results already in `contents`.
    try:
        result = await client.generate(
            contents=contents,
            system_instruction=nudged,
            model=model,
            max_output_tokens=1024,  # match the other chat paths so this fallback isn't the one that cuts off
        )
        if result.text:
            return result.text
    except Exception:
        log.exception("forced final answer (no tools) failed")
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
    for _ in range(MAX_TOOL_ITERS):
        resp = await client.generate_with_tools(
            contents=contents,
            system_instruction=system_instruction,
            tools=tools,
            model=model,
        )
        calls = _function_calls(resp)
        if not calls:
            text = _response_text(resp)
            if text:
                return await _complete_truncated(
                    client, contents, system_instruction, model, tools, resp, text
                )
            break  # no calls and no text — go force a final answer

        contents.append(resp.candidates[0].content)  # the model's tool-call turn
        responses = []
        for call in calls:
            result = await execute_tool(call.name, dict(call.args or {}), ctx)
            if _useful(result):
                gathered.append(result)
            responses.append(
                types.Part.from_function_response(
                    name=call.name, response={"result": result}
                )
            )
        contents.append(types.Content(role="tool", parts=responses))

    # Budget spent (or an empty turn) — force a plain-text final answer.
    answer = await _force_final_answer(client, contents, system_instruction, model, tools)
    if answer:
        return answer

    # Last resort: surface everything we gathered, not just the last item.
    return _fallback_from_gathered(blank_fallback, gathered)


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
    images: list[tuple[bytes, str]] | None = None,
) -> str:
    """Produce Olisar's reply text for one incoming message/prompt.

    ``images`` (``(data, mime)`` pairs from the triggering message) are shown to
    the model directly, so Olisar can react to screenshots/pictures in real time."""
    # DMs (guild_id 0) borrow the home server's persona + config.
    cfg_guild = guild_id or settings.target_guild_id

    persona = await session.get(Persona, cfg_guild)
    if persona is None:
        system_instruction = build_system_prompt(
            persona_name=DEFAULT_PERSONA_NAME,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            tone_notes=DEFAULT_TONE_NOTES,
            runtime_note=runtime_note,
        )
    else:
        system_instruction = build_system_prompt(
            persona_name=persona.name,
            system_prompt=persona.system_prompt,
            tone_notes=persona.tone_notes,
            runtime_note=runtime_note,
        )
    system_instruction += "\n\n" + CONTEXT_NOTE + "\n\n" + TOOLS_NOTE
    system_instruction += (
        f"\n\nCurrent time (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M} — use it "
        "to resolve any 'remind me' / scheduling request before calling add_reminder."
    )

    config = await session.get(GuildConfig, cfg_guild)
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

    # Semantic recall — best-effort; a failure here must not block the reply.
    try:
        recalled = await recall(
            session,
            cfg_guild=cfg_guild,
            user_id=user_id,
            query_text=user_text,
            recent_ids=recent_ids,
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

    ctx = ToolContext(
        session=session,
        cfg_guild=cfg_guild,
        channel_id=channel_id,
        user_id=user_id,
        display_name=display_name,
        actions=actions,
        extension_tools=ext.handlers,
    )
    try:
        return await _run_tool_loop(
            contents,
            system_instruction,
            model,
            ctx,
            blank_fallback=blank_fallback,
            tools=tools_with_extensions(extra_decls),
        )
    except RateLimitExceeded:
        return rate_limit_msg
    except Exception:
        log.exception("gemini generation failed")
        return blank_fallback
