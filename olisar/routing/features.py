"""What a message looks like to the router, read from its text alone.

Cheap and readable on purpose: regular expressions over the message, no model. The
classifier (olisar.routing.classifier) weighs these alongside the message's embedding, and
the policy (olisar.routing.policy) uses a few of them as hard floors: a message that asks for
a lookup or an action, or reads like an injection attempt, never goes to the light tier
whatever it scores.

English carries most of the patterns. A message in another language mostly matches none of
them, which leaves it on the standard tier: the safe direction to be wrong in.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Words that make up a whole message that only acknowledges, greets or laughs. A message
# made only of these (and emoji, punctuation, the bot's name) is an acknowledgement.
_ACK_WORDS = frozenset("""
thanks thank thx ty tysm tyvm thankyou thanku tq cheers appreciate appreciated it you u
ok okay okie k kk cool nice neat great awesome perfect sweet lovely excellent amazing
lol lmao lmfao rofl haha hahaha hahahaha ha hah hehe heh xd xdd kek lul lel
gg ggs wp gj glhf gl hf
gm gn morning night goodnight good evening afternoon hi hello hey heya hiya yo sup howdy hai helo
bye cya later laters ttyl brb afk back
np no problem yw welcome youre you're your ur
yes yeah yea ye yep yup ya yah no nope nah nop sure true fair facts same mood based real fr
w l rip oof bruh damn dang wow omg omfg woah whoa lets go gooo goo
love it this ily luv
good bad bot boy girl job one
nvm never mind mind
got gotcha makes sense sounds will do on all set
merci gracias danke obrigado obrigada grazie arigato jaja jajaja kkkk mdr
the a an so very much too lot lots
""".split())

# Words that agree to something. A message of acknowledgement words that includes one of
# these may be a yes to something Olisar just offered ("want me to remind you?" "do it"), so
# it isn't treated as only acknowledging when it follows one of Olisar's replies.
_ASSENT_WORDS = frozenset("""
yes yeah yea ye yep yup ya yah sure ok okay okie k kk do set go please pls plz alright
""".split())

# Small talk aimed at the bot, which wants a friendly line rather than knowledge.
_SMALLTALK = re.compile(
    r"^(?:hey |hi |hello |yo )?(?:how are (?:you|u|ya)|how(?:'s| is) it going|how(?:'s| is) your day"
    r"|how was your (?:day|night|weekend)|what'?s up|wassup|wyd|what are you (?:doing|up to)"
    r"|how have you been|you good|are you (?:there|awake|alive|ok|okay)"
    r"|good (?:morning|night|evening|afternoon)(?: everyone| all| guys)?"
    r"|miss(?:ed)? (?:me|you)|i'?m back|i'?m home|i'?m bored)\b",
    re.I,
)

_INTERROGATIVE = re.compile(
    r"^(?:what|whats|what's|why|how|when|where|who|whose|which|can|could|would|should|is|are"
    r"|do|does|did|will|have|has|any|anyone)\b",
    re.I,
)

_CODE = re.compile(
    r"```|Traceback \(most recent call last\)|\bFile \"[^\"]+\", line \d+|\b\w+(?:Error|Exception)\b"
    r"|\bat line \d+|\bnpm ERR!|\bsegfault|\bstack ?trace|\bnull pointer|\bundefined is not"
    r"|\b(?:def|class|function|const|let|var|import|return|public|private|void)\s+\w+"
    r"|\bSELECT\b.+\bFROM\b|=>|\w+\(\)|\{\s*$|^\s*\}|</?\w+>",
    re.I | re.M,
)

_MATH = re.compile(
    r"\d+(?:\.\d+)?\s*[+*/^×÷]\s*\d+|\d+(?:\.\d+)?\s+[-x]\s+\d+|\b(?:integral|derivative|equation|probability|percentage"
    r"|average of|square root|factorial|compound interest|how many (?:ways|combinations))\b",
    re.I,
)

_REASONING = re.compile(
    r"\b(?:explain(?: why| how| the)?|why (?:does|do|is|are|would|did|doesn't|don't|isn't)"
    r"|compare|comparison|difference between|differences between|pros and cons|trade-?offs?"
    r"|step[- ]by[- ]step|walk me through|debug|troubleshoot|optimi[sz]e|refactor|architect"
    r"|design (?:a|an|my|the)|plan (?:a|an|my|the|out)|strategy|strategi[sz]e|analy[sz]e|analysis"
    r"|evaluate|prove|derive|calculate|solve|estimate|what would happen if|which is better"
    r"|should i .+ or |figure out|break(?:ing)? down|reason(?:ing)? (?:about|through))\b",
    re.I,
)

_DEPTH = re.compile(
    r"\b(?:in detail|detailed|in depth|in-depth|thorough(?:ly)?|deep dive|think (?:hard|carefully"
    r"|it through|step by step)|comprehensive|long answer|elaborate|full breakdown|everything about"
    r"|as much detail|go deep|really explain)\b",
    re.I,
)

_LONG_FORM = re.compile(
    r"\b(?:write|draft|compose|rewrite|create)\b(?: me| us| up)?(?: a| an| the| some| my| our)?"
    r"(?: \w+){0,3} (?:essay|story|poem|guide|tutorial|article|lore|backstory|speech|letter"
    r"|script|announcement|description|bio|proposal|outline|summary|rules|faq|report|review"
    r"|itinerary|lesson plan|cover letter|resume|chapter|scene)\b",
    re.I,
)

_LOOKUP = re.compile(
    r"\b(?:what did .{1,40} (?:say|post|mean)|who said|did (?:anyone|someone) (?:say|post|mention)"
    r"|find (?:the|a|that|me|my) (?:message|post|link|thread)|search (?:for|the)|look (?:it )?up"
    r"|google|remember when|what (?:was|were) we talking about"
    r"|what did i miss|catch me up|summari[sz]e (?:the|this|what)"
    r"|according to|the rules|server rules|the schedule|next event|when is the|link to"
    r"|in the docs|the wiki|knowledge base|latest news|news about|price of|weather|score of"
    r"|who won|release date|patch notes)\b|<#\d+>|#[a-z0-9_-]{2,}",
    re.I,
)

_ACTION = re.compile(
    r"\b(?:remind me|set a reminder|reminder|dm (?:me|him|her|them|<@)|send (?:a |an )?(?:dm|message)"
    r"|message (?:him|her|them|<@)|tell <@|post (?:this |that |it )?(?:in|to)|announce|ping"
    r"|remember (?:that|this|my|me|i)|forget (?:that|this|my|about)|change (?:your|the) |set (?:your|the) "
    r"|rename|your status|update your|settings|config|system prompt|persona"
    r"|(?:draw|generate|make|create) (?:me |us )?(?:a |an )?(?:image|picture|pic|drawing|art|wallpaper|logo)"
    r"|image of|picture of|pic of)\b",
    re.I,
)

_INJECTION = re.compile(
    r"\b(?:ignore (?:all |any |the |your )?(?:previous|prior|above|earlier|preceding)?\s*"
    r"(?:instructions|rules|prompts?|messages)|system prompt|your (?:instructions|prompt|rules|guidelines)"
    r"|developer mode|jailbreak|do anything now|pretend (?:you are|you're|to be)|you are now"
    r"|act as (?:a|an|my|the)|roleplay as|i am (?:the |an? )?(?:admin|owner|developer|moderator|mod|dev)"
    r"|as (?:the |an? )?(?:owner|admin|developer)|(?:reveal|repeat|print|show) (?:your|the) "
    r"(?:prompt|instructions|rules|system)|override|bypass|disregard"
    r"|do not (?:mention|reveal|acknowledge|repeat) (?:this|these|the) instructions?"
    r"|new instructions|hidden instructions?|instructions? (?:for|to) (?:the )?(?:ai|assistant|bot|model))\b"
    # A pasted turn dressed as the system's or the model's own: "SYSTEM: ...", "[system]".
    r"|^\s*(?:system|assistant|developer|admin)\s*:|\[\s*(?:system|admin|developer)\s*\]|</?system>",
    re.I | re.M,
)

_URL = re.compile(r"https?://\S+", re.I)
_MENTION = re.compile(r"<@[!&]?\d+>|<#\d+>|<a?:\w+:\d+>")
_WORD = re.compile(r"[\w']+", re.U)
_LIST_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.M)
_ALSO = re.compile(r"\b(?:and also|also,|additionally|secondly|second question|another question|plus,)\b", re.I)


@dataclass(frozen=True)
class Features:
    words: int
    lines: int
    questions: int  # question marks, or 1 for a question with none
    ack: bool  # only acknowledges, greets or laughs
    assent: bool  # an acknowledgement that could be a yes to an offer ("do it", "sure")
    smalltalk: bool  # how are you, what's up
    code: bool
    math: bool
    multi_part: bool  # a list, several questions, "and also"
    reasoning: bool
    depth: bool  # asks for detail outright
    long_form: bool  # asks for a piece of writing
    lookup: bool  # asks about the server, its history, or the web
    action: bool  # asks Olisar to do something (remind, DM, post, change a setting, an image)
    injection: bool
    url: bool
    emoji_only: bool

    def vector(self) -> list[float]:
        """The features as the classifier reads them, in FEATURE_NAMES order."""
        return [
            math.log1p(self.words),
            math.log1p(max(0, self.lines - 1)),
            min(self.questions, 3) / 3,
            float(self.ack),
            float(self.smalltalk),
            float(self.code),
            float(self.math),
            float(self.multi_part),
            float(self.reasoning),
            float(self.depth),
            float(self.long_form),
            float(self.lookup),
            float(self.action),
            float(self.injection),
            float(self.url),
            float(self.emoji_only),
        ]


FEATURE_NAMES = (
    "log_words", "log_extra_lines", "questions", "ack", "smalltalk", "code", "math",
    "multi_part", "reasoning", "depth", "long_form", "lookup", "action", "injection", "url",
    "emoji_only",
)


def _strip_names(text: str, names: tuple[str, ...]) -> str:
    for name in names:
        if name:
            text = re.sub(rf"\b{re.escape(name)}\b", " ", text, flags=re.I)
    return text


def extract(text: str, names: tuple[str, ...] = ()) -> Features:
    """Read ``text``. ``names`` are what the bot answers to (its persona name and name
    triggers); they're dropped first, so "thanks olisar" is as much an acknowledgement as
    "thanks"."""
    raw = text or ""
    body = _MENTION.sub(" ", _strip_names(raw, names))
    words = _WORD.findall(body)
    lowered = [w.lower() for w in words]
    stripped = body.strip()
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    qmarks = raw.count("?")
    question = qmarks > 0 or bool(_INTERROGATIVE.match(stripped))
    emoji_only = bool(raw.strip()) and not words
    ack = bool(lowered) and len(lowered) <= 8 and all(w in _ACK_WORDS for w in lowered) and qmarks == 0
    return Features(
        words=len(words),
        lines=len(lines),
        questions=qmarks if qmarks else int(question),
        ack=ack or emoji_only,
        assent=ack and any(w in _ASSENT_WORDS for w in lowered),
        smalltalk=bool(_SMALLTALK.match(stripped)) and len(words) <= 10,
        code=bool(_CODE.search(raw)),
        math=bool(_MATH.search(raw)),
        multi_part=(
            qmarks >= 2 or len(_LIST_LINE.findall(raw)) >= 2 or bool(_ALSO.search(raw))
        ),
        reasoning=bool(_REASONING.search(raw)),
        depth=bool(_DEPTH.search(raw)),
        long_form=bool(_LONG_FORM.search(raw)),
        lookup=bool(_LOOKUP.search(raw)) or bool(_URL.search(raw)),
        action=bool(_ACTION.search(raw)),
        injection=bool(_INJECTION.search(raw)),
        url=bool(_URL.search(raw)),
        emoji_only=emoji_only,
    )
