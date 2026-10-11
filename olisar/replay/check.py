"""The last check: every quote the replay will show, read once more before it can play.

There's no draft and nobody reviews a replay before it plays, so this is the last gate. Every
message the report quotes, the pick's spots, the lines Olisar says and the names it was given
go to Flash-Lite in one request, and anything it doesn't clearly pass is dropped, as is anything
on the blocklist (words.py) before it gets that far. A chapter that loses what it needs goes
with it: the pick without its first message, the first reply without both halves.
"""

from __future__ import annotations

from olisar.replay.words import blocked

CHECK_SYSTEM = """\
You check a replay of a Discord server's history before it plays on a big screen in front of \
the whole server. Below is every message and line it will show, each with an id.

For each, answer "ok" if it's fine to show everyone, or "drop" if it could embarrass, hurt or \
single out anyone; if it touches conflict, moderation, health, grief, money, politics, religion \
or sex; or if it shares something private (a full name, an address, a phone number, a place \
someone lives or works). If you're unsure, "drop".

Return only JSON with every id: {"q1": "ok", "q2": "drop"}
"""


def _quotes(report: dict) -> list[tuple[str, dict, str]]:
    """(where, the dict holding it, the key its text is under), for everything shown."""
    out: list[tuple[str, dict, str]] = []
    for m in (report.get("first") or {}).get("messages", []):
        out.append(("first", m, "text"))
    for f in report.get("founders") or []:
        if f.get("msg"):
            out.append(("founder", f["msg"], "text"))
    night = report.get("night") or {}
    for m in night.get("excerpt") or []:
        if "text" in m:
            out.append(("night", m, "text"))
    for key in ("line", "sphere"):
        if night.get(key):
            out.append(("night-" + key, night, key))
    for m in (report.get("arrive") or {}).get("reply", []):
        out.append(("reply", m, "text"))
    for mile in (report.get("arrivals") or {}).get("milestones", []):
        if mile.get("line"):
            out.append(("burst", mile, "line"))
    pick = report.get("pick") or {}
    if pick:
        out.append(("pick-origin", pick["origin"], "text"))
        for key in ("intro", "spread", "meaning", "title", "why"):
            out.append(("pick-" + key, pick, key))
        for spot in pick.get("spots") or []:
            if spot.get("said"):
                out.append(("spot", spot, "said"))
    for n in report.get("names") or []:
        out.append(("name", n, "to"))
    return out


def _drop(report: dict, dropped: list[tuple[str, dict, str]]) -> None:
    gone = {id(holder) for _, holder, _ in dropped}
    kinds = {where for where, _, _ in dropped}
    if report.get("first"):
        report["first"]["messages"] = [m for m in report["first"]["messages"] if id(m) not in gone]
        if not report["first"]["messages"]:
            report["first"] = None
    for f in report.get("founders") or []:
        if f.get("msg") and id(f["msg"]) in gone:
            f["msg"] = None
    night = report.get("night")
    if night:
        if "night-line" in kinds:
            night["line"] = None
        if "night-sphere" in kinds:
            night["sphere"] = None
        kept = [m for m in night.get("excerpt") or [] if id(m) not in gone]
        # No gap at either end, and never two in a row.
        tidy: list[dict] = []
        for m in kept:
            if "gap" in m and (not tidy or "gap" in tidy[-1]):
                continue
            tidy.append(m)
        while tidy and "gap" in tidy[-1]:
            tidy.pop()
        night["excerpt"] = tidy if sum("text" in m for m in tidy) >= 3 else []
    arrive = report.get("arrive")
    if arrive and any(id(m) in gone for m in arrive.get("reply", [])):
        arrive["reply"] = []
    for mile in (report.get("arrivals") or {}).get("milestones", []):
        if id(mile) in gone:
            mile["line"] = None
    pick = report.get("pick")
    if pick:
        if kinds & {"pick-origin", "pick-intro", "pick-spread", "pick-meaning", "pick-title"}:
            report["pick"] = None
        elif "pick-why" in kinds:
            pick["why"] = ""
        else:
            for spot in pick.get("spots") or []:
                if id(spot) in gone:
                    spot["said"] = None
    if report.get("names"):
        report["names"] = [n for n in report["names"] if id(n) not in gone]


async def run(ctx) -> None:
    st = ctx.state.setdefault("check", {})
    if st.get("done"):
        return
    report = ctx.report
    quotes = _quotes(report)
    dropped = [q for q in quotes if blocked(str(q[1].get(q[2]) or ""))]
    ask = [q for q in quotes if q not in dropped]
    if ask:
        lines = []
        for i, (_, holder, key) in enumerate(ask, 1):
            lines.append(f"q{i}: {' '.join(str(holder.get(key) or '').split())[:400]}")
        answer = None
        for attempt in (1, 2):
            answer = await ctx.asker.ask(f"check:{attempt}", kind="lite", system=CHECK_SYSTEM, prompt="\n".join(lines),
                                         temperature=0, max_output_tokens=200 + 12 * len(ask))
            if answer:
                break
        if not answer:
            raise RuntimeError("the last check of the replay's quotes gave no answer")
        dropped += [q for i, q in enumerate(ask, 1) if answer.get(f"q{i}") != "ok"]
    _drop(report, dropped)
    st["done"] = True
    st["dropped"] = len(dropped)
    await ctx.keep()
