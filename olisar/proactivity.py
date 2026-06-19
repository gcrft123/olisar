"""Proactivity decision engine — the cheap → expensive cascade.

The whole point is to spend almost nothing on the ~95% of messages Olisar should
ignore. Order of escalation (the cog applies Stage 0 gates first):
  Stage 1  heuristic_score()  — free, text-only
  Stage 2  classify()         — one tiny Flash-Lite call, only for survivors
  Stage 3  full reply         — only when Stage 2 is confident (cog handles it)
"""

from __future__ import annotations

import json
import logging
import re

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

_CLASSIFY_SYSTEM = (
    "You decide whether 'Olisar', a friendly community member bot, should jump "
    "into this Discord conversation UNPROMPTED right now. Say yes ONLY if it "
    "would clearly add genuine value — answer an open question, correct a clear "
    "factual error, or share uniquely useful info. Say no for small talk, banter, "
    "matters of opinion, an active human back-and-forth, or anything where butting "
    "in would be annoying. Respond with ONLY a JSON object: "
    '{"should_respond": true|false, "confidence": 0.0-1.0, "reason": "brief"}'
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


async def classify(transcript: str) -> tuple[bool, float, str]:
    """Stage 2: a tiny Flash-Lite call -> (should_respond, confidence, reason)."""
    result = await get_gemini().generate(
        contents=[transcript],
        system_instruction=_CLASSIFY_SYSTEM,
        model=settings.gemini_lite_model,
        temperature=0.1,
        max_output_tokens=120,
    )
    data = _parse_json(result.text)
    return (
        bool(data.get("should_respond", False)),
        float(data.get("confidence", 0.0) or 0.0),
        str(data.get("reason", ""))[:200],
    )
