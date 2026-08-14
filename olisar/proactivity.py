"""Proactivity decision engine — the cheap → expensive cascade.

The whole point is to spend almost nothing on the ~95% of messages Olisar should
ignore. Order of escalation (the cog applies Stage 0 gates first):
  Stage 1  heuristic_score()  — free, text-only
  Stage 2  classify()         — one tiny Flash-Lite call, only for survivors
  Stage 3  full reply         — only when Stage 2 is confident (cog handles it)

Both gates are scaled by ``follow_up_score()``: every threshold here is calibrated for
interrupting a conversation Olisar isn't in, and a message that answers something Olisar
just said is the opposite case. Continuing an exchange you're part of shouldn't have to
clear the bar for barging into one you aren't.
"""

from __future__ import annotations

import json
import logging
import re

from olisar.addressing import SECOND_PERSON
from olisar.config import settings
from olisar.db.models import ProactivityLevel
from olisar.gemini.client import get_gemini

log = logging.getLogger("olisar.proactivity")

# Higher level = more eager = LOWER heuristic bar to clear.
_LEVEL_THRESHOLDS = {
    ProactivityLevel.low: 0.8,
    ProactivityLevel.med: 0.6,
    ProactivityLevel.high: 0.4,
}

_INTERROGATIVES = (
    "how", "what", "why", "when", "where", "who", "which", "can ", "could ",
    "does", "do you", "is there", "are there", "should", "anyone", "any one",
    "help", "recommend", "suggest",
)

# A late-stage escape hatch: even after the classifier says yes, the model can
# bail if it realizes it has nothing worth adding.
SKIP_SENTINEL = "(skip)"

PROACTIVE_NOTE = (
    "You're choosing to jump into this conversation on your own — nobody "
    "addressed you. Only add something genuinely useful (answer an open "
    "question, fix a clear error, share uniquely helpful info). Keep it to one "
    "or two sentences and don't dominate. If, on reflection, you don't have "
    f"anything truly worth adding, reply with exactly {SKIP_SENTINEL} and nothing else."
)

# The reply-stage counterpart. PROACTIVE_NOTE tells the model nobody addressed it, which
# is true of a cold chime and false of an answer to something it just said — and a reply
# written under "you are butting in uninvited" reads exactly as apologetic as that sounds.
FOLLOW_UP_REPLY_NOTE = (
    "Someone has just responded to something you said. You're carrying on a conversation "
    "you're already part of, not interrupting one — answer the way you would if they'd "
    "addressed you by name, and keep it short. If it genuinely needs nothing back, reply "
    f"with exactly {SKIP_SENTINEL} and nothing else."
)

_CLASSIFY_SYSTEM = (
    "You decide whether 'Olisar', a friendly community member bot, should jump "
    "into this Discord conversation UNPROMPTED right now. Say yes ONLY if it "
    "would clearly add genuine value — answer an open question, correct a clear "
    "factual error, or share uniquely useful info. Say no for small talk, banter, "
    "matters of opinion, an active human back-and-forth, or anything where butting "
    "in would be annoying. Respond with ONLY a JSON object: "
    '{"should_respond": true|false, "confidence": 0.0-1.0, "reason": "brief"}'
)

# Added to the classifier's instruction when the message being judged landed straight
# after one of Olisar's own. "Don't interrupt" is the right instinct for a conversation
# between other people and the wrong one for a conversation you're already in: someone
# answering, pushing back on, or asking about what you just said is talking to you, and
# walking away mid-exchange is a worse failure than one reply too many.
_FOLLOW_UP_NOTE = (
    "\nIMPORTANT: the last message came immediately after one of Olisar's own and reads "
    "as a response to it — an answer, a challenge, a follow-up question, or a reaction to "
    "what Olisar just said. This is a conversation Olisar is ALREADY in, not one it would "
    "be interrupting, and the 'don't butt into an active back-and-forth' rule does not "
    "apply to an exchange it is a participant in. Lean towards yes unless the message is "
    "plainly closing the conversation off ('thanks', 'ok cool'), is addressed to somebody "
    "else, or genuinely needs nothing back."
)

# How a follow-up moves the two gates. Relief is subtracted from the operator's confidence
# threshold in proportion to the signal, never past the floor — a server that set a high
# bar still has one, it just isn't the same bar for someone talking back to Olisar as for
# a stranger's passing remark.
FOLLOW_UP_RELIEF = 0.3
FOLLOW_UP_FLOOR = 0.3

# Short openers that carry a conversation on rather than starting one. Only ever read in
# the position right after Olisar spoke, where "wait" and "same" are answers to it.
_ACK_OPENERS = (
    "yeah", "yea", "yep", "ye ", "yes", "nah", "no ", "nope", "wait", "huh", "really",
    "oh", "ok", "okay", "k ", "hm", "hmm", "true", "same", "fair", "lol", "lmao", "why",
    "how", "what", "wdym", "but ", "and ", "so ", "thanks", "ty ", "cheers", "hold on",
    "i thought", "i mean", "actually", "damn", "wow", "wtf", "since when", "based",
)


def level_threshold(level: ProactivityLevel) -> float:
    return _LEVEL_THRESHOLDS.get(level, 1.1)  # unknown/off -> impossible to clear


def heuristic_score(text: str, age_seconds: float) -> float:
    """Free, text-only signal in [0, 1] that a message might want a reply."""
    t = (text or "").lower().strip()
    if len(t) < 8:
        return 0.0  # too trivial to be worth analyzing
    score = 0.0
    if "?" in t:
        score += 0.5
    if any(t.startswith(w) or f" {w}" in t for w in _INTERROGATIVES):
        score += 0.25
    if age_seconds > 45:  # sat unanswered a while -> more likely a real lull
        score += 0.2
    if len(t) > 80:
        score += 0.05
    return max(0.0, min(score, 1.0))


def follow_up_score(text: str, *, after_olisar: bool) -> float:
    """How much a message reads as a response to what Olisar just said, in [0, 1].

    ``after_olisar`` is the structural fact — Olisar wrote the message directly above
    this one — and nothing here fires without it: "wait really?" between two other people
    is not Olisar's to answer. Given it, the wording says how *directly* the message comes
    back at Olisar, which is what the two gates below are scaled by.

    An explicit @mention or Discord reply never reaches this path; it is already handled
    as an addressed message. This is the case with no marker on it at all — the one that
    used to be judged as if Olisar were a stranger to the conversation.
    """
    if not after_olisar:
        return 0.0
    t = " ".join((text or "").lower().split())
    if not t:
        return 0.0
    score = 0.4  # Olisar spoke last, so this is plausibly aimed back at it
    if SECOND_PERSON.search(t):
        score += 0.3  # "you" in the message right after yours means you
    if t.startswith(_ACK_OPENERS):
        score += 0.2
    if "?" in t:
        score += 0.2
    if len(t.split()) <= 12:
        score += 0.1  # a quick beat back is more of a reply than a fresh paragraph
    return max(0.0, min(score, 1.0))


def relaxed_threshold(base: float, follow_up: float) -> float:
    """The confidence bar to apply to this message, given how much it looks like a
    follow-up.

    Bounded both ways. ``FOLLOW_UP_FLOOR`` keeps the gate lowered rather than removed —
    but it bounds the *relief*, not the operator: someone who deliberately set a bar
    below the floor asked for eager, and returning the floor there would raise their
    threshold in the name of relaxing it."""
    eased = base - FOLLOW_UP_RELIEF * max(0.0, min(follow_up, 1.0))
    return min(base, max(FOLLOW_UP_FLOOR, eased))


def _parse_json(text: str) -> dict:
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


async def classify(transcript: str, *, follow_up: bool = False) -> tuple[bool, float, str]:
    """Stage 2: a tiny Flash-Lite call -> (should_respond, confidence, reason).

    ``follow_up`` tells the classifier that the last message answers one of Olisar's, so
    it judges "should I continue this" instead of "should I interrupt this"."""
    result = await get_gemini().generate(
        contents=[transcript],
        system_instruction=_CLASSIFY_SYSTEM + (_FOLLOW_UP_NOTE if follow_up else ""),
        model=settings.gemini_lite_model,
        temperature=0.1,
        max_output_tokens=120,
        source="proactivity",
    )
    data = _parse_json(result.text)
    return (
        bool(data.get("should_respond", False)),
        float(data.get("confidence", 0.0) or 0.0),
        str(data.get("reason", ""))[:200],
    )


# What actually draws a reaction, which is close to the opposite of what draws a reply.
# `heuristic_score` scores questions — and the reaction prompt below says explicitly not
# to react to questions, so gating reactions on it selected for the one thing it rejects.
_LAUGHTER = ("lol", "lmao", "lmfao", "rofl", "kek", "haha", "hehe", "xd", "💀", "😭", "😂", "🤣")
_CELEBRATION = (
    "gg", "congrats", "congratulations", "finally", "we did it", "let's go", "lets go",
    "shipped", "launched", "birthday", "passed", "got the job", "nailed it", "poggers", "🎉",
)
_SYMPATHY = ("rip", "damn", "sorry", "ugh", "that sucks", "unlucky", "oof", "brutal", "😔", "😢")
# Markers bot/content.py leaves in stored text for a posted image or file.
_MEDIA_MARKERS = ("[image:", "[image description:", "[attachment:", "[sticker:")


def reaction_score(text: str) -> float:
    """How much a message invites a *reaction* rather than a reply, in [0, 1].

    Zero is a hard no, not merely a low score: the caller skips anything that scores 0
    however low the operator's threshold is. Questions land there — someone asking
    something wants an answer, and a lone 👍 on a question is the bot looking like it
    misunderstood.
    """
    t = (text or "").strip().lower()
    if len(t) < 2:
        return 0.0  # a lone character is noise; "gg" and "w" are not
    if "?" in t or any(t.startswith(w) for w in _INTERROGATIVES):
        return 0.0  # wants an answer, not an emoji
    score = 0.0
    if any(w in t for w in _LAUGHTER):
        score += 0.6
    if any(w in t for w in _CELEBRATION):
        score += 0.5
    if any(w in t for w in _SYMPATHY):
        score += 0.4
    if any(m in t for m in _MEDIA_MARKERS):
        score += 0.35  # people react to what gets posted, not just what gets said
    if "!" in t:
        score += 0.2
    words = t.split()
    if words and sum(w.isupper() for w in (text or "").split()) >= 2:
        score += 0.2  # shouting
    if len(words) <= 6:
        score += 0.15  # a short beat is reactable; a paragraph wants reading
    elif len(t) > 300:
        score -= 0.2
    return max(0.0, min(score, 1.0))


_REACT_SYSTEM = (
    "You are Olisar, a member of this Discord server skimming the latest message. "
    "Decide whether to add a quick emoji reaction to it — the way a person reacts "
    "without replying. React ONLY if a single emoji fits naturally and adds a light, "
    "friendly touch: agreement, amusement, celebration, sympathy, or simple "
    "acknowledgement. Most messages need no reaction; don't react to questions aimed "
    "at you (answer those normally) or to anything where a reaction would feel random. "
    "Reply with ONLY one emoji, or the single word none if no reaction fits."
)


def _first_emoji(s: str) -> str | None:
    """Pull a single leading emoji (incl. skin-tone, ZWJ sequences, flags, and
    variation selectors) out of a short model reply; None if it doesn't start with one."""
    token = (s or "").strip().split()[:1]
    if not token:
        return None
    out: list[str] = []
    for ch in token[0]:
        o = ord(ch)
        is_emoji = (
            o >= 0x1F000
            or 0x2190 <= o <= 0x2BFF
            or 0x1F1E6 <= o <= 0x1F1FF  # regional indicators (flags)
            or o in (0x200D, 0xFE0F)    # ZWJ + variation selector
            or 0x1F3FB <= o <= 0x1F3FF  # skin-tone modifiers
        )
        if is_emoji:
            out.append(ch)
        else:
            break
    return "".join(out) or None


async def pick_reaction_emoji(transcript: str) -> str | None:
    """A tiny Flash-Lite call that returns one emoji to react with, or None if no
    reaction fits. Used by the passive-reaction path (no reply is generated)."""
    result = await get_gemini().generate(
        contents=[transcript],
        system_instruction=_REACT_SYSTEM,
        model=settings.gemini_lite_model,
        temperature=0.4,
        max_output_tokens=12,
        source="proactivity",
    )
    text = (result.text or "").strip()
    if not text or text.lower().startswith("none"):
        return None
    return _first_emoji(text)
