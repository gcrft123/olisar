"""Words the replay checks in code: what never goes on the big screen, what never counts as
a server's own word, and the rules a line Olisar says has to pass before it's shown."""

from __future__ import annotations

import re

# Never quoted, never a pick. Kept short and blunt on purpose: the model checks after it
# catch what a list can't, and this catches what a model might wave through.
_BLOCK = (
    r"n[i1]gg(?:a|er|ers|as|uh)?", r"f[a@]gg?(?:ot|ots|y)?", r"retard(?:ed|s)?", r"tr[a@]nn(?:y|ie|ies)",
    r"k[i1]ke", r"sp[i1]c", r"ch[i1]nk", r"wetback", r"dyke",
    r"porn\w*", r"nsfw", r"nudes?", r"horny", r"blow ?job", r"handjob", r"dick ?pic\w*",
    r"cum(?:ming|shot)?", r"pussy", r"cock", r"boobs?", r"tits", r"dildo\w*", r"hentai", r"onlyfans",
    r"rape[ds]?", r"suicid\w*", r"kill (?:myself|yourself|urself)", r"kys", r"kms", r"self[- ]?harm", r"cutting myself",
)
BLOCKED = re.compile(r"(?<![\w])(?:" + "|".join(_BLOCK) + r")(?![\w])", re.IGNORECASE)


def blocked(text: str) -> bool:
    return bool(BLOCKED.search(text or ""))


# English that every server says, and chat filler. A phrase made only of these is never a
# server's own word.
STOPWORDS = frozenset("""
a about above after again against all almost also am an and any are aren't around as at be because been before
being below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during
each else even ever every few for from further get gets getting go goes going gone got had hadn't has hasn't have
haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd i'll i'm i've if in
into is isn't it it's its itself just let's like make made me more most much must mustn't my myself new no nor not
now of off on once one only or other ought our ours ourselves out over own really same say said see seen shan't she
she'd she'll she's should shouldn't so some still such take than that that's the their theirs them themselves then
there there's these they they'd they'll they're they've thing things think this those though through time to too
two under until up upon us very want was wasn't way we we'd we'll we're we've well were weren't what what's when
when's where where's which while who who's whom why why's will with won't would wouldn't yeah yes yet you you'd
you'll you're you've your yours yourself yourselves
lol lmao lmfao rofl haha hahaha hah heh xd ok okay k kk yep yup nope nah oh ah uh um hmm hm wow omg omfg idk idc
tbh imo imho btw fr ngl rn ty thx thanks thank pls plz please hi hey hello bye gn gm sup yo ya u ur im dont cant
wont didnt doesnt isnt thats whats theres ive id ill youre theyre gonna wanna gotta kinda sorta lemme gimme
""".split())

# Common enough that alone they're nobody's own word, though "movie night" or "game day" can be.
COMMON = frozenset("""
good bad great nice cool sure right literally actually probably maybe anyway lot lots day days today tomorrow
yesterday night week year years month guys guy people person someone something anything everything nothing stuff
game games play playing played server channel discord message messages chat bot know thought feel look looks
looking need help work home friend friends love fun funny man dude bro best first last next back first long little
""".split())

_WORD = re.compile(r"[^\W\d_][\w'’]*", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD.findall(text or "")]


def is_filler(phrase: str) -> bool:
    """A phrase that can't be anyone's own: function words or filler at either end, or one
    common word on its own."""
    words = phrase.split()
    if not words or words[0] in STOPWORDS or words[-1] in STOPWORDS:
        return True
    return len(words) == 1 and (words[0] in COMMON or len(words[0]) < 3)


# ── Lines Olisar says ──
# PICK.md's writer rules, used for every line a model writes for the replay.
BANNED = ("legendary", "iconic", "core memory", "an era", "the era", "vibe", "vibes", "delve", "tapestry", "testament")
_SENTENCE = re.compile(r"[.?]+(?:\s|$)")


def sentences(text: str) -> int:
    return max(1, len(_SENTENCE.findall(text.strip())))


def line_problem(text: str, *, max_words: int = 32, max_sentences: int = 2, names_allowed: set[str] | None = None,
                 names_known: set[str] | None = None, placeholders: tuple[str, ...] = ()) -> str | None:
    """Why a written line can't be shown, or None if it can."""
    t = (text or "").strip()
    if not t:
        return "it's empty"
    if "!" in t:
        return "it has an exclamation mark"
    if "?" in t:
        return "it asks a question"
    if blocked(t):
        return "it uses a word the replay never shows"
    low = t.lower()
    for word in BANNED:
        if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", low):
            return f'it calls something "{word}"'
    stripped = t
    for ph in placeholders:
        stripped = stripped.replace(ph, "")
    if len(stripped.split()) > max_words:
        return f"it's over {max_words} words"
    if sentences(t) > max_sentences:
        return f"it's more than {max_sentences} sentences"
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", t):
        return "it has an emoji"
    if names_known is not None and names_allowed is not None:
        for name in names_known - names_allowed:
            if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", t, re.IGNORECASE):
                return f"it names {name}, who isn't in the evidence"
    return None
