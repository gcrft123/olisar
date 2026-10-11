"""The lines in the replay that need understanding, said in the persona's voice.

Everything else Olisar says in the replay is a template the console fills from the report
("Five people the first week."). What the moments step found is said once more here by the
server's own model, as the persona members know: what the busiest day was about, the name its
memory goes by, and why a crowd arrived. One request. Each line is checked the way the pick's
writer is (words.line_problem); a line that doesn't pass keeps what the moments step wrote.
"""

from __future__ import annotations

from sqlalchemy import select

from olisar.db.engine import session_scope
from olisar.db.models import Persona
from olisar.replay.words import blocked, line_problem

WRITE_SYSTEM = """\
You're {name}, a member of the Discord server {server}, narrating a replay of its history for \
the people in it. {persona}

Below are lines the replay will show, written plainly. Say each again in your own voice. Keep \
what each one says and add nothing: no names that aren't in it, no numbers, no dates.

Voice: first person where it fits, plain, a little dry, fond without gushing. No exclamation \
marks, questions or emoji. Don't call anything legendary, iconic, a core memory, an era, or a vibe. \
These are lines on a screen, not chat messages: whatever your chat habits, write full \
sentences with a full stop at the end (night_title is a name, no full stop), all on one line, and no [[break]].

- night: what the server's busiest day was about. One sentence, at most 14 words.
- night_title: a name for that day, 2 to 5 words, lowercase unless a name needs capitals.
- burst: why a crowd of new people arrived one day. One sentence, at most 16 words.

Return only JSON with the keys you were given, for example: {"night": "...", "night_title": "..."}
"""


def _names_in(text: str, known: set[str]) -> set[str]:
    low = (text or "").lower()
    return {n for n in known if n and n.lower() in low}


async def run(ctx) -> None:
    st = ctx.state.setdefault("write", {})
    if st.get("done"):
        return
    report = ctx.report
    night = report.get("night") or {}
    burst = next((m for m in (report.get("arrivals") or {}).get("milestones", []) if m.get("kind") == "burst" and m.get("line")), None)
    lines = {}
    if night.get("line"):
        lines["night"] = night["line"]
    if night.get("sphere"):
        lines["night_title"] = night["sphere"]
    if burst:
        lines["burst"] = burst["line"]
    if not lines:
        st["done"] = True
        return
    async with session_scope() as session:
        p = (await session.execute(select(Persona.name, Persona.tone_notes).where(Persona.guild_id == ctx.guild_id))).first()
    system = (WRITE_SYSTEM.replace("{name}", (p[0] if p else "") or "Olisar")
              .replace("{server}", report.get("guild", {}).get("name", "")).replace("{persona}", (p[1] if p else "") or ""))
    prompt = "\n".join(f"{k}: {v}" for k, v in lines.items())
    answer = await ctx.asker.ask("write", kind="chat", system=system, prompt=prompt, temperature=0.8, max_output_tokens=400) or {}
    known = {p["name"] for p in ctx.people.seen.values()} | set(ctx.roster.names())
    limits = {"night": (14, 1), "night_title": (5, 1), "burst": (16, 1)}
    for key, original in lines.items():
        said = str(answer.get(key) or "").strip().strip('"“”')
        words, sentences = limits[key]
        problem = line_problem(said, max_words=words, max_sentences=sentences, names_known=known, names_allowed=_names_in(original, known))
        if problem or any(ch.isdigit() for ch in said) or blocked(said):
            continue
        if key == "night":
            night["line"] = said
        elif key == "night_title":
            night["sphere"] = said
        elif burst:
            burst["line"] = said
    st["done"] = True
    await ctx.keep()
