"""Server lore extraction — the guild glossary.

Alongside per-user personas and rolling channel summaries, Olisar keeps a compact
list of *durable, server-specific facts*: abbreviation expansions, the names of
groups / orgs / people and how they relate, project codenames, in-joke meanings —
the local dialect of THIS community. These are extracted with a cheap Flash-Lite
pass whenever a channel is summarized (so busy channels mine lore more often), and
the whole glossary is folded into every reply's context so the bot "just knows"
what `ICA` or `Griefernet` mean.

Deliberately simple: a flat, deduplicated list (no embeddings). A guild's lore is
inherently small and almost always relevant, so we always carry it rather than
retrieve a slice. If a guild's glossary ever grows huge, switch `glossary_block`
to a KNN retrieval like the other memories.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.config import settings
from olisar.db.models import GuildFact, utcnow
from olisar.gemini.client import get_gemini
from olisar.gemini.rate_limiter import RateLimitExceeded

log = logging.getLogger("olisar.facts")

FACTS_SYSTEM = (
    "You mine a slice of a Discord server's chat for DURABLE, SERVER-SPECIFIC facts "
    "worth remembering. Capture: acronym / abbreviation expansions; the names of "
    "groups, orgs, teams, or people and how they relate or what role/responsibility "
    "they hold; project or place codenames; recurring events, rituals, or schedules; "
    "and the meaning of in-jokes or local slang. Be generous about anything clearly "
    "specific to THIS community, but IGNORE transient chatter, one-off opinions, "
    "feelings, questions, and general world knowledge anyone would know.\n\n"
    "You are given the glossary ALREADY KNOWN (each line is a subject and its current "
    "fact). Use it to avoid duplicates:\n"
    "- Do NOT return a fact that merely repeats or rephrases something already known — "
    "if it adds no new information, omit it entirely.\n"
    "- Keep exactly ONE entry per subject. If you learn genuinely NEW detail about a "
    "subject that's already known, return that subject with a SINGLE consolidated fact "
    "that merges what was already known WITH the new detail (return the full updated "
    "fact, not just the new fragment, and not a mere rewording of the old one).\n"
    "- Only introduce a new subject when it isn't already covered above.\n\n"
    "Return ONLY a JSON array of objects, each {\"subject\": \"<the term or "
    "entity>\", \"fact\": \"<one short, standalone factual statement>\"}. Return "
    "[] if nothing is genuinely new. Examples:\n"
    '[{"subject": "ICA", "fact": "ICA is short for Ironclad Assault"}, '
    '{"subject": "Movie Night", "fact": "Movie Night is the Friday watch-party in #cinema"}]'
)

MAX_FACTS_PER_PASS = 20   # cap how many new facts one mining pass can introduce
GLOSSARY_LIMIT = 60       # how many facts to carry into a reply's context
KNOWN_FACTS_LIMIT = 120   # how many known facts to show the miner so it can de-dupe


def _parse_facts(text: str) -> list[dict]:
    """Tolerantly pull a JSON array of {subject, fact} from a model response."""
    if not text:
        return []
    s = text.strip()
    # Strip markdown code fences if present.
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start, end = s.find("["), s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(s[start : end + 1])
    except Exception:
        return []
    out: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (item.get("fact") or item.get("subject")):
                out.append(item)
    return out


def _norm(s: str) -> str:
    """Lowercased, whitespace-collapsed, trailing-punctuation-stripped — a cheap key for
    spotting a fact that only repeats or rephrases one we already have."""
    return " ".join((s or "").split()).strip().lower().rstrip(".!?,;:")


async def upsert_facts(
    session: AsyncSession, *, guild_id: int, channel_id: int | None, items: list[dict]
) -> int:
    """Fold mined facts into the glossary, keeping exactly ONE entry per subject:

    * new subject           -> insert it
    * known subject, and the fact repeats / rephrases / is a subset of what we already
      have -> reinforce only (bump ``mentions``); the stored text is left untouched
    * known subject with genuinely new detail (the miner returns a consolidated fact)
      -> adopt the updated text and reinforce

    This is what stops the glossary filling up with duplicate or reworded rows. Returns
    the count of genuinely new subjects added."""
    if not items:
        return 0
    existing = (
        await session.scalars(
            select(GuildFact)
            .where(GuildFact.guild_id == guild_id)
            .order_by(GuildFact.mentions.desc(), GuildFact.updated_at.desc())
        )
    ).all()
    # Canonical row per subject = the most-reinforced one (first, given the ordering).
    by_subject: dict[str, GuildFact] = {}
    for f in existing:
        by_subject.setdefault(f.subject.strip().lower(), f)
    added = 0
    for item in items[:MAX_FACTS_PER_PASS]:
        fact = (item.get("fact") or "").strip()
        subject = (item.get("subject") or "").strip()
        if not fact:
            continue
        if not subject:
            subject = fact.split(" ", 1)[0][:128]
        hit = by_subject.get(subject.strip().lower())
        if hit is not None:
            n_new, n_old = _norm(fact), _norm(hit.fact)
            # Only overwrite when the new fact genuinely differs AND isn't just a subset
            # of what we already store — i.e. the miner consolidated in new information.
            if n_new != n_old and n_new not in n_old:
                hit.fact = fact
            hit.mentions += 1
            hit.updated_at = utcnow()
            continue
        row = GuildFact(
            guild_id=guild_id,
            subject=subject[:128],
            fact=fact,
            source_channel_id=channel_id,
            mentions=1,  # set now so same-batch duplicates can reinforce pre-flush
        )
        session.add(row)
        by_subject[subject.strip().lower()] = row
        added += 1
    if added:
        log.info("learned %d new guild fact(s) in channel %s", added, channel_id)
    return added


async def _known_facts_block(session: AsyncSession, guild_id: int) -> str:
    """The guild's current glossary rendered for the miner, so it can skip anything it
    already knows and consolidate rather than duplicate."""
    rows = (
        await session.scalars(
            select(GuildFact)
            .where(GuildFact.guild_id == guild_id)
            .order_by(GuildFact.mentions.desc(), GuildFact.updated_at.desc())
            .limit(KNOWN_FACTS_LIMIT)
        )
    ).all()
    if not rows:
        return ""
    lines = "\n".join(f"- {r.subject}: {r.fact}" for r in rows)
    return "ALREADY KNOWN (do not repeat or rephrase these):\n" + lines


async def extract_and_store_facts(
    session: AsyncSession, *, guild_id: int, channel_id: int | None, transcript: str
) -> int:
    """Run the extraction pass over a transcript and persist the results. Best-
    effort: returns 0 (and never raises) when the model is unavailable. The current
    glossary is shown to the miner so it returns only genuinely new/updated facts."""
    if not transcript.strip():
        return 0
    known = await _known_facts_block(session, guild_id)
    prompt = (known + "\n\n" if known else "") + "CHAT TO MINE:\n" + transcript
    try:
        result = await get_gemini().generate(
            contents=[prompt],
            system_instruction=FACTS_SYSTEM,
            model=settings.gemini_lite_model,
            temperature=0.2,
            max_output_tokens=600,
        )
    except RateLimitExceeded:
        return 0
    except Exception:
        log.exception("guild-fact extraction call failed")
        return 0
    items = _parse_facts(result.text or "")
    return await upsert_facts(
        session, guild_id=guild_id, channel_id=channel_id, items=items
    )


async def glossary_block(
    session: AsyncSession, guild_id: int, limit: int = GLOSSARY_LIMIT
) -> str:
    """Render the guild's glossary as a compact context block (empty if none)."""
    rows = (
        await session.scalars(
            select(GuildFact)
            .where(GuildFact.guild_id == guild_id)
            .order_by(GuildFact.mentions.desc(), GuildFact.updated_at.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return ""
    lines = "\n".join(f"- {r.fact}" for r in rows)
    return "Server glossary (durable facts about this community):\n" + lines
