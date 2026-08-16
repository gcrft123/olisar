"""Server-wide message search — the engine behind the ``search_messages`` tool.

Answers "find the needle in the server's history" questions ("what's the server's
X account?") by hunting across EVERY channel (per the admin's all-channel opt-in),
not just the conversational subset that recall covers. Three best-effort passes,
fused:

* **keyword (FTS5)** over ``search_message`` — the all-channel index. This is the
  workhorse: exact tokens like ``x.com`` / ``twitter`` / a handle are what these
  questions hinge on, and FTS needs no embeddings (so it covers ``off`` channels).
* **semantic** over the existing conversational ``message_embedding`` — a relevance
  boost where we already have vectors, merged in by Discord message id.
* **context scan** of ``channel_context_item`` — catches resource/feed channels
  (e.g. an ``#announcements`` post), which live outside ``search_message``.

Each candidate is returned with a Discord jump-link so the model can cite it.
Bot/self messages and other guilds are filtered out; opt-out users were never
indexed (and ``/forget-me`` purges them — see olisar/memory/purge.py).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.context import name_map
from olisar.db.models import (
    ChannelContextItem,
    GuildChannelInfo,
    GuildConfig,
    Message,
    snowflake_time,
)
from olisar.gemini.embeddings import embed_query
from olisar.memory.vectors import knn

log = logging.getLogger("olisar.search")

FTS_K = 40          # keyword candidates pulled before fusion
VEC_K = 40          # semantic candidates pulled before fusion
CONTEXT_K = 20      # context-channel LIKE-scan cap
FINAL_K = 10        # rendered back to the model
# No absolute score floor. One was tried and removed: `kw` is normalised across the
# returned set, so the best candidate always scores 1.0 on keyword however poor it is
# absolutely, and every query topped out at the same 0.595 whether the fact existed or
# not. Worse, a 0.30 floor discarded the *correct* answer in a control case while leaving
# absent-fact noise in place. The fused score carries no absolute signal to threshold.
# A question put to Olisar is not evidence about the server, and it is the single best
# keyword match for a query about the same subject — so without this, asking "who posted
# the setup guide?" returns ten earlier askings of that question and nothing else. Olisar
# is then told to "skim these and answer", and it does, from the question.
#
# Self-reinforcing, and specific to a bot that indexes what is said *to* it: every asking
# makes the next search worse. Verified against a live corpus, where the top four hits for
# an absent fact were four phrasings of the question.
#
# Deliberately narrow — both conditions must hold. "olisar said the schedule moved to
# friday" names the bot and is real evidence; it survives because it isn't a question.
_INTERROGATIVE = re.compile(
    r"\b(who|what|when|where|which|why|how|do you|does anyone|did anyone|can you|"
    r"could you|any(one|body))\b",
    re.IGNORECASE,
)


def _drop_bot_questions_enabled() -> bool:
    """Whether to drop questions addressed to the bot. **Off** unless explicitly enabled.

    Off because the A/B said so, against the hypothesis that motivated writing it. On a
    question about something the server never discussed, the question-loop is the only
    absence signal the retrieval layer produces: ten hits that are all people *asking* X
    legibly means nobody answered X, and Olisar reads it that way — "don't think we have
    any", "nothing here", correct in every run. Filtering the questions out does not leave
    silence. It promotes the next tier of hits, which is members speculating, and
    speculation reads as evidence in a way that a question never does. Every run of the
    dropped arm invented a plausible history from it: a Twitter account "dead for at least
    a year" that never existed, three different confident dates for a guide nobody wrote.

    So it made absent-fact fabrication worse, which is the failure it was written to fix.
    Present facts retrieve correctly either way, so there is nothing on the other side of
    the trade. Kept as a flag rather than deleted because the finding rests on 16 runs
    across three scenarios and deserves re-testing on a larger corpus, where the balance
    between question-noise and speculation-noise may not be the same.

    The ugliness that prompted the whole investigation — Olisar narrating "i keep hitting
    your own questions about it" — is real, but it is the *mechanism* leaking into the
    reply, not the retrieval being wrong. That belongs in the tool briefing.
    """
    return os.environ.get("OLISAR_SEARCH_DROP_BOT_QUESTIONS", "0").strip() not in ("0", "false", "no")


def _label_questions_enabled() -> bool:
    """Whether to mark questions-to-the-bot in the rendered results. Off pending its A/B.

    Separate from _drop_bot_questions_enabled and the opposite of it: that one removes
    them from the ranking, this one leaves them ranked and says what they are.
    """
    return os.environ.get("OLISAR_SEARCH_LABEL_QUESTIONS", "0").strip() not in ("0", "false", "no")


def _is_question_to_bot(content: str, names: list[str]) -> bool:
    """Whether a stored message is somebody asking the bot something."""
    text_ = (content or "").strip()
    if not text_:
        return False
    if not any(
        re.search(rf"\b{re.escape(n.strip().lower())}\b", text_.lower())
        for n in names if n and n.strip()
    ):
        return False
    return "?" in text_ or bool(_INTERROGATIVE.search(text_))


SNIPPET_CHARS = 240

# Function words + contraction orphans dropped from the FTS query (kept short so
# meaningful single letters like "x" survive).
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "of", "to", "for", "in",
    "on", "at", "and", "or", "s", "t", "m", "re", "ve", "ll", "d", "what", "whats",
    "who", "whos", "how", "do", "does", "did", "i", "you", "it", "its", "this",
    "that", "whose", "with", "about", "any", "our", "your",
}


@dataclass
class _Cand:
    channel_id: int
    channel_name: str
    author_name: str
    content: str
    created_at: datetime
    message_id: int | None
    bm25: float | None = None
    vec_distance: float | None = None
    keyword: bool = False     # matched via FTS or context LIKE
    is_dm: bool = False        # from the guild-0 DM bucket — rendered as "DM", no jump link
    score: float = field(default=0.0)

    @property
    def key(self) -> tuple:
        if self.message_id:
            return ("m", self.message_id)
        return ("c", self.channel_id, self.content[:64])


def sanitize_fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 MATCH string: keep alphanumeric tokens,
    drop stopwords, phrase-quote each, OR-join. Quoting is structural injection
    safety — only quoted alphanumeric tokens reach MATCH, so '.', '@', '*', ':',
    'NEAR', parens and stray quotes can't break the parser (the cause of the
    ``fts5: syntax error near "."`` on a bare ``x.com``). Returns '' if empty."""
    seen: set[str] = set()
    tokens: list[str] = []
    for tok in re.findall(r"[0-9A-Za-z]+", query.lower()):
        if tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return " OR ".join(f'"{t}"' for t in tokens)


def _scope_orm(model, guilds: list[int], dm_channels: list[int]):
    """ORM predicate: rows in one of ``guilds`` OR (for own-DM recall) in a specific
    DM ``channel``. Channel ids are globally unique snowflakes, so the channel arm can't
    pull in the wrong guild's messages."""
    cond = model.guild_id.in_(guilds)
    return or_(cond, model.channel_id.in_(dm_channels)) if dm_channels else cond


def _scope_sql(guilds: list[int], dm_channels: list[int], table: str = "search_message"):
    """The same scope for a raw SQL pass — a ``(guild_id IN (…) OR channel_id IN (…))``
    fragment plus its bound params (named so they can't collide with :q/:k)."""
    params: dict = {}
    gph = []
    for i, g in enumerate(guilds):
        params[f"sg{i}"] = g
        gph.append(f":sg{i}")
    # No guild scope at all (a DM with no resolvable home server) means the server arm
    # matches nothing — never everything. "0" is SQLite's false literal.
    frag = f"{table}.guild_id IN ({','.join(gph)})" if gph else "0"
    if dm_channels:
        cph = []
        for i, c in enumerate(dm_channels):
            params[f"sc{i}"] = c
            cph.append(f":sc{i}")
        frag = f"({frag} OR {table}.channel_id IN ({','.join(cph)}))"
    return frag, params


async def _fts_pass(
    session: AsyncSession, guilds: list[int], dm_channels: list[int], fts_query: str
) -> list[_Cand]:
    if not fts_query:
        return []
    scope, scope_params = _scope_sql(guilds, dm_channels)
    rows = (
        await session.execute(
            text(
                "SELECT search_message.channel_id, search_message.channel_name, "
                "search_message.author_name, search_message.content, "
                "search_message.created_at, search_message.message_id, "
                "search_message.guild_id, bm25(search_message_fts) AS score "
                "FROM search_message_fts "
                "JOIN search_message ON search_message.id = search_message_fts.rowid "
                "WHERE search_message_fts MATCH :q AND " + scope + " "
                "ORDER BY score LIMIT :k"
            ),
            {"q": fts_query, "k": FTS_K, **scope_params},
        )
    ).all()
    out: list[_Cand] = []
    for r in rows:
        out.append(
            _Cand(
                channel_id=int(r[0]),
                channel_name=r[1] or "",
                author_name=r[2] or "",
                content=r[3] or "",
                created_at=_msg_time(r[5], r[4]),
                message_id=int(r[5]) if r[5] is not None else None,
                bm25=float(r[7]),
                is_dm=(int(r[6]) == 0),
                keyword=True,
            )
        )
    return out


async def _vec_pass(
    session: AsyncSession, guilds: list[int], dm_channels: list[int], query: str
) -> list[_Cand]:
    qvec = await embed_query(query) if query.strip() else []
    if not qvec:
        return []
    hits = await knn(session, "message_embedding", qvec, k=VEC_K)
    if not hits:
        return []
    dist = {rid: d for rid, d in hits}
    rows = (
        await session.scalars(
            select(Message).where(
                Message.id.in_(list(dist.keys())),
                _scope_orm(Message, guilds, dm_channels),
                Message.author_is_bot == False,  # noqa: E712
            )
        )
    ).all()
    if not rows:
        return []
    names = await name_map(session, {m.author_id for m in rows})
    return [
        _Cand(
            channel_id=m.channel_id,
            channel_name="",
            author_name=names.get(m.author_id, str(m.author_id)),
            content=m.content or "",
            created_at=_msg_time(m.message_id, m.created_at),
            message_id=m.message_id,
            vec_distance=dist.get(m.id),
            is_dm=(m.guild_id == 0),
        )
        for m in rows
        if (m.content or "").strip()
    ]


async def _context_pass(
    session: AsyncSession, guilds: list[int], dm_channels: list[int], tokens: list[str]
) -> list[_Cand]:
    if not tokens:
        return []
    rows = (
        await session.scalars(
            select(ChannelContextItem)
            .where(_scope_orm(ChannelContextItem, guilds, dm_channels))
            .order_by(ChannelContextItem.id.desc())
            .limit(400)
        )
    ).all()
    out: list[_Cand] = []
    for it in rows:
        low = (it.content or "").lower()
        if any(tok in low for tok in tokens):
            out.append(
                _Cand(
                    channel_id=it.channel_id,
                    channel_name=it.channel_name or "",
                    author_name=it.author_name or "",
                    content=it.content or "",
                    created_at=_msg_time(it.message_id, it.created_at),
                    message_id=it.message_id,
                    is_dm=(it.guild_id == 0),
                    keyword=True,
                )
            )
        if len(out) >= CONTEXT_K:
            break
    return out


def _as_utc(value) -> datetime:
    """Coerce a datetime — or the raw string SQLite hands back from the FTS query —
    into a tz-aware UTC datetime."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _msg_time(message_id, stored) -> datetime:
    """The message's real post time — from its snowflake id when we have one
    (accurate even for backfilled history), else the stored timestamp."""
    if message_id:
        return snowflake_time(int(message_id))
    return _as_utc(stored)


def _merge(passes: list[list[_Cand]]) -> list[_Cand]:
    """Merge candidates from all passes by identity, combining their signals."""
    merged: dict[tuple, _Cand] = {}
    for cands in passes:
        for c in cands:
            existing = merged.get(c.key)
            if existing is None:
                merged[c.key] = c
                continue
            if c.bm25 is not None and (existing.bm25 is None or c.bm25 < existing.bm25):
                existing.bm25 = c.bm25
            if c.vec_distance is not None and (
                existing.vec_distance is None or c.vec_distance < existing.vec_distance
            ):
                existing.vec_distance = c.vec_distance
            existing.keyword = existing.keyword or c.keyword
            if not existing.channel_name and c.channel_name:
                existing.channel_name = c.channel_name
            if not existing.author_name and c.author_name:
                existing.author_name = c.author_name
    return list(merged.values())


def _fuse(cands: list[_Cand]) -> list[_Cand]:
    """Score = 0.45*keyword + 0.40*semantic + 0.15*recency; missing signal = 0."""
    bms = [c.bm25 for c in cands if c.bm25 is not None]
    lo, hi = (min(bms), max(bms)) if bms else (0.0, 0.0)
    now = datetime.now(timezone.utc)
    for c in cands:
        if c.bm25 is not None:
            kw = (hi - c.bm25) / (hi - lo) if hi > lo else 1.0
        elif c.keyword:
            kw = 0.5  # matched (context LIKE) but unranked
        else:
            kw = 0.0
        sem = max(0.0, 1.0 - c.vec_distance / 2.0) if c.vec_distance is not None else 0.0
        age_days = max(0.0, (now - c.created_at).total_seconds() / 86400.0)
        recency = 0.5 ** (age_days / 30.0)
        c.score = 0.45 * kw + 0.40 * sem + 0.15 * recency
    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


async def _name_triggers(session: AsyncSession, guild_id: int) -> list[str]:
    """The names this guild answers to, for spotting questions aimed at the bot."""
    if not guild_id:
        return []
    try:
        config = await session.get(GuildConfig, guild_id)
    except Exception:  # noqa: BLE001 — search must never fail over a config read
        return []
    return list(config.name_triggers or []) if config else []


async def _channel_labels(
    session: AsyncSession, guild_id: int, channel_ids: set[int]
) -> dict[int, str]:
    if not channel_ids:
        return {}
    rows = (
        await session.scalars(
            select(GuildChannelInfo).where(GuildChannelInfo.channel_id.in_(channel_ids))
        )
    ).all()
    return {r.channel_id: r.name for r in rows}


def _snippet(content: str) -> str:
    text_ = re.sub(r"\s+", " ", content).strip()
    return text_[:SNIPPET_CHARS] + ("…" if len(text_) > SNIPPET_CHARS else "")


async def search_messages(
    session: AsyncSession,
    *,
    guild_id: int,
    query: str,
    k: int = FINAL_K,
    dm_channel_id: int | None = None,
) -> str:
    """Search the server's message history. Returns a rendered candidate block (with
    Discord jump-links) for the model to read and synthesize, or ''.

    ``dm_channel_id`` adds one specific DM channel (the guild-0 bucket is otherwise never
    searched), so a DM conversation can recall its own history. There is deliberately no
    way to widen this to the whole DM bucket: a member's 1:1 messages are readable only
    inside that same conversation, by anyone, admin or not. DM hits render as
    "DM · <author>" without a jump-link (they're private 1:1s)."""
    # Guild 0 *is* the DM bucket, so it must never be used as a server scope — otherwise a
    # DM whose home guild can't be resolved (dm_home_guild_id falling back to an unset
    # target_guild_id) would search every member's private messages.
    guilds = [guild_id] if guild_id else []
    dm_channels = [dm_channel_id] if dm_channel_id else []
    fts_query = sanitize_fts_query(query)
    tokens = [t.strip('"') for t in fts_query.split(" OR ") if t]

    passes: list[list[_Cand]] = []
    for runner in (
        _fts_pass(session, guilds, dm_channels, fts_query),
        _vec_pass(session, guilds, dm_channels, query),
        _context_pass(session, guilds, dm_channels, tokens),
    ):
        try:
            passes.append(await runner)
        except Exception:
            log.exception("a search pass failed; continuing with the others")

    ranked = _fuse(_merge(passes))

    # Drop questions put to the bot before ranking decides anything. See
    # _is_question_to_bot: these are the strongest keyword match for a query on the same
    # subject and carry no information about the answer.
    names = await _name_triggers(session, guild_id) if _drop_bot_questions_enabled() else []
    if names:
        before = len(ranked)
        ranked = [c for c in ranked if not _is_question_to_bot(c.content, names)]
        if before != len(ranked):
            log.info(
                "search_messages(%r): dropped %d question(s) addressed to the bot",
                query, before - len(ranked),
            )
    # Drop the merely-similar before taking the top k, so "nothing relevant" is a result
    # this function can actually return. Returning "" makes the tool answer "No matching
    # messages found in the server's history", which is the truth and is something the
    # model can act on — unlike ten near-misses it has no way to recognise as such.
    cands = ranked[:k]
    if not cands:
        return ""

    labels = await _channel_labels(
        session, guild_id, {c.channel_id for c in cands if not c.channel_name and not c.is_dm}
    )

    # Label, don't drop. Dropping questions addressed to the bot was tried and measured
    # worse (see _drop_bot_questions_enabled): it promotes members' speculation into the
    # top slots, and speculation reads as evidence in a way a question never does.
    #
    # But leaving them unmarked asks Olisar to infer absence from a wall of text that
    # looks like ten ordinary hits, and the prompt cannot help — "when search comes back
    # with nothing, say you don't know" is an instruction it cannot execute, because
    # search never comes back with nothing. `kw` is rank-normalised, so ten near-misses
    # and ten real answers are indistinguishable at this layer. An instruction that cannot
    # be obeyed produces confabulation rather than refusal; a tools_note forbidding
    # invented affordances was A/B'd against baseline and landed inside the noise on every
    # dimension, while both arms went on inventing wikis, archives and log retention.
    #
    # Marking them makes the absence legible at the only layer that knows: the one that
    # can see the hits are questions. It also gives Olisar words for what it is seeing —
    # ungrounded, it narrates the mechanism instead ("i keep hitting your own questions").
    question_names = await _name_triggers(session, guild_id) if _label_questions_enabled() else []
    asked_count = 0

    lines: list[str] = []
    for c in cands:
        who = c.author_name or "someone"
        date = c.created_at.strftime("%Y-%m-%d %H:%M UTC")
        asked = bool(question_names) and _is_question_to_bot(c.content, question_names)
        tag = " · [someone asking, not an answer]" if asked else ""
        asked_count += 1 if asked else 0
        if c.is_dm:
            # Private 1:1 DM — no jump-link (only the participant could open it anyway).
            lines.append(f'- DM · {who} · {date} · "{_snippet(c.content)}"{tag}')
            continue
        ch = c.channel_name or labels.get(c.channel_id) or str(c.channel_id)
        link = (
            f" · https://discord.com/channels/{guild_id}/{c.channel_id}/{c.message_id}"
            if c.message_id
            else ""
        )
        lines.append(f'- #{ch} · {who} · {date} · "{_snippet(c.content)}"{tag}{link}')

    log.info(
        "search_messages(%r): %d hit(s) — %s",
        query, len(cands),
        "; ".join(
            f"#{c.channel_name or labels.get(c.channel_id) or c.channel_id}/{c.message_id}"
            for c in cands[:8]
        ),
    )
    header = (
        "Message search results (skim these and answer the question; include a "
        "jump-link only if they're asking where or when something was posted):"
    )
    if asked_count and asked_count >= len(cands) / 2:
        # The stated conclusion, not just the evidence. Left to infer it, the model
        # reports the search instead of the finding.
        header += (
            f"\nNOTE: {asked_count} of {len(cands)} results are people asking this same "
            f"question rather than answering it. If the rest don't answer it either, then "
            f"nobody in this server has — say you don't know, and don't send them to a "
            f"channel, pin, wiki or log you haven't seen."
        )
    return header + "\n" + "\n".join(lines)
