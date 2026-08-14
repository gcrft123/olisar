"""Is this message *to* Olisar, or merely *about* it?

The name trigger matches its name anywhere in a message, which is what people expect
("hey olisar", "does olisar know?", "olisar you around") — and also what made it answer
"olisar was down yesterday" and "i asked olisar about that already", neither of which is
addressed to anyone. Answering every mention of your own name is one of the clearest
tells that nobody is home: real members read a room and let most of it go past.

The cascade is the same shape as proactivity's: a free heuristic first, and one tiny
Flash-Lite call only for the genuinely ambiguous middle. It fails *open* — an error or a
rate limit means Olisar replies, because going mute is a far worse failure than an
unnecessary answer.
"""

from __future__ import annotations

import logging
import re

from olisar.config import settings
from olisar.gemini.client import get_gemini

log = logging.getLogger("olisar.addressing")

ADDRESSED = "addressed"
AMBIGUOUS = "ambiguous"
PASSING = "passing"

# Openers that still leave the name in the vocative — "hey olisar", "ok olisar so",
# and a typed-out "@olisar" that isn't a real mention.
_LEAD_INS = re.compile(r"(?:(?:hey|hi|hello|yo|ok|okay|so|um|uh|oi|ayo|pls|please)\s+)*@?\s*")
# Second person anywhere is a strong sign the message is *to* somebody, and in a message
# that also names Olisar — or one that lands right after Olisar spoke (see
# olisar/proactivity.py) — that somebody is Olisar.
SECOND_PERSON = re.compile(r"\b(you|your|you're|youre|yours|u|ur|urself|yourself)\b")
# A name followed by one of these is being talked *about*: "olisar was down", "olisar
# keeps forgetting", "olisar's memory". Third-person verbs and the possessive.
#
# Deliberately narrow. Adverbs ("just", "never", "already") and bare modals ("can",
# "keep") read as third-person here and as an imperative one word later — "oli just do
# it" is an instruction, not gossip — so anything that cuts both ways is left out and
# falls to the classifier instead of being guessed at.
_THIRD_PERSON = re.compile(
    r"^(?:'s|s'|\s+(?:is|isn't|was|wasn't|has|hasn't|had|does|doesn't|did|didn't|"
    r"won't|would|said|says|told|kept|keeps|broke|breaks|went|goes|"
    r"seems|seemed|likes|liked|thinks|thought)\b)"
)
# "i asked olisar", "ping olisar", "tell olisar" — talking about it to someone else.
_REFERRING_VERBS = re.compile(
    r"\b(asked|ask|told|tell|pinged?|mention(?:ed)?|means?|meant|about|via|through|"
    r"like|unlike|vs|versus)\s+$"
)


def _positions(text: str, names: list[str]) -> list[tuple[int, int]]:
    """Where each configured name appears, as (start, end) spans."""
    spans: list[tuple[int, int]] = []
    for name in names:
        if not name.strip():
            continue
        for m in re.finditer(rf"\b{re.escape(name.strip().lower())}\b", text):
            spans.append((m.start(), m.end()))
    return sorted(spans)


def name_mention_kind(text: str, names: list[str]) -> str:
    """Classify a name mention as :data:`ADDRESSED`, :data:`PASSING` or :data:`AMBIGUOUS`.

    Free and text-only. Anything with a question, a "you", or the name in the vocative
    slot (first or last) is addressed; a name wearing a possessive or followed by a
    third-person verb is being talked about; everything else is handed upward.
    """
    body = (text or "").strip().lower()
    spans = _positions(body, names)
    if not spans:
        return AMBIGUOUS  # the trigger fired on something else (a mention, a reply)

    start, end = spans[0]
    before, after = body[:start], body[end:]

    # Order matters, and the vocative can't come first: "olisar was down yesterday" opens
    # with the name too. A question or a "you" is the strongest signal there is, so it
    # wins outright ("olisar is that right?" is addressed despite the third-person verb).
    if "?" in body or SECOND_PERSON.search(body):
        return ADDRESSED
    if _THIRD_PERSON.match(after) or _REFERRING_VERBS.search(before):
        return PASSING  # talked about rather than talked to
    # Vocative: the name opens the message ("olisar, help") or closes it ("thanks olisar").
    if _LEAD_INS.fullmatch(before) or not after.strip(" .!,~"):
        return ADDRESSED
    return AMBIGUOUS


_CONFIRM_SYSTEM = (
    "A Discord message mentions a bot named {name}. Decide whether the message is "
    "ADDRESSED to {name} — asking it something, telling it something, or otherwise "
    "expecting it to respond — or whether it merely mentions {name} while talking to "
    "other people about it. Someone saying '{name} is broken' or 'i already asked "
    "{name}' to the channel is NOT addressing it. Answer with exactly one word: "
    "addressed or mentioning."
)


async def confirm_addressed(text: str, name: str) -> bool:
    """One tiny Flash-Lite call for the ambiguous middle. True = reply.

    Fails open: any error, rate limit or unparseable answer returns True, because the
    cost of a needless reply is much lower than the cost of a bot that silently ignores
    someone who was talking to it.
    """
    try:
        result = await get_gemini().generate(
            contents=[text],
            system_instruction=_CONFIRM_SYSTEM.format(name=name or "the bot"),
            model=settings.gemini_lite_model,
            temperature=0.0,
            max_output_tokens=8,
            source="proactivity",
        )
    except Exception:
        log.info("addressing check unavailable; treating the mention as addressed")
        return True
    answer = (result.text or "").strip().lower()
    return not answer.startswith("mention")
