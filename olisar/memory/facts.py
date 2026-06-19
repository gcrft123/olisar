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
    "You mine a slice of a Discord server's chat for DURABLE, SERVER-SPECIFIC "
    "lore worth remembering forever. Capture: acronym / abbreviation expansions, "
    "the names of groups, orgs, teams, or people and how they relate, project or "
    "place codenames, and the meaning of in-jokes or local slang. IGNORE transient "
    "chatter, opinions, feelings, questions, and general world knowledge anyone "
    "would know. Only keep facts tied to THIS community.\n\n"
    "Return ONLY a JSON array of objects, each {\"subject\": \"<the term or "
    "entity>\", \"fact\": \"<one short, standalone factual statement>\"}. Return "
    "[] if nothing qualifies. Examples:\n"
    '[{"subject": "ICA", "fact": "ICA is short for Ironclad Assault"}, '
    '{"subject": "Griefernet", "fact": "Griefernet is an enemy org run by Griefenfuhrer"}]'
)

MAX_FACTS_PER_PASS = 12   # cap how many new facts one summary can introduce
GLOSSARY_LIMIT = 60       # how many facts to carry into a reply's context


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


async def upsert_facts(
    session: AsyncSession, *, guild_id: int, channel_id: int | None, items: list[dict]
) -> int:
    """Insert new facts; reinforce (bump `mentions`) ones we already know. Returns
    the count of genuinely new facts added."""
    if not items:
        return 0
    existing = (
        await session.scalars(select(GuildFact).where(GuildFact.guild_id == guild_id))
    ).all()
    index = {
        (f.subject.strip().lower(), f.fact.strip().lower()): f for f in existing
    }
    added = 0
    for item in items[:MAX_FACTS_PER_PASS]:
        fact = (item.get("fact") or "").strip()
        subject = (item.get("subject") or "").strip()
        if not fact:
            continue
        if not subject:
            subject = fact.split(" ", 1)[0][:128]
        key = (subject.lower(), fact.lower())
        hit = index.get(key)
        if hit is not None:
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
        index[key] = row
        added += 1
    if added:
        log.info("learned %d new guild fact(s) in channel %s", added, channel_id)
    return added


async def extract_and_store_facts(
    session: AsyncSession, *, guild_id: int, channel_id: int | None, transcript: str
) -> int:
    """Run the extraction pass over a transcript and persist the results. Best-
    effort: returns 0 (and never raises) when the model is unavailable."""
    if not transcript.strip():
        return 0
    try:
        result = await get_gemini().generate(
            contents=[transcript],
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
