"""Smaller checks on a billed key, for design/model-routing/EVALUATION.md.

    GEMINI_API_KEY=... uv run python -m arena.experiments.routing_checks pro-rpm [--n 40]
    GEMINI_API_KEY=... uv run python -m arena.experiments.routing_checks search [--n 20]
    uv run python -m arena.experiments.routing_checks concurrent-pro [--n 8]

``pro-rpm`` and ``search`` call Google directly with the key in this process's environment.
``pro-rpm`` sends tiny requests to Pro spread across a minute and reports any 429 with the
quota Google names; Olisar assumes 50 a minute (olisar/gemini/models.py). ``search`` runs
grounded searches on 3.8 Flash, alternating the default thinking and low, the way
``GeminiClient.search`` sends them.

``concurrent-pro`` goes through the arena instance (which must hold the billed key): it
sends deep messages to the test chat all at once and counts how many come back from Pro.
Right after a restart the share cap's floor is 3, so at most 3 should.

Every request is priced and appended to route_eval's ledger, which stops new requests at
``--stop-at``. Results go to ``arena/runs/_route_eval/checks-<name>.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from arena.experiments.route_eval import WORK, Ledger, _now

PRO = "gemini-3.1-pro-preview"
FLASH = "gemini-3.8-flash"

SEARCH_QUERIES = [
    "latest stable python release version",
    "who won the most recent formula 1 grand prix",
    "current price of a steam deck oled",
    "minecraft latest java edition version",
    "when is the next full moon",
    "discord nitro basic price per month",
    "newest nvidia geforce gpu released",
    "weather in tokyo this weekend",
    "latest valorant patch notes changes",
    "how many people live in canada 2026",
]

DEEP_MESSAGES = [
    "my bot crashes with this:\nTraceback (most recent call last):\n  File \"bot.py\", line 42, in on_message\n    await message.channel.send(reply)\nAttributeError: 'NoneType' object has no attribute 'send'\nwhat's going on and how do i fix it",
    "compare postgres, mysql and sqlite for a discord bot with 50k users, considering hosting, concurrency and backups",
    "plan out a raid schedule for our guild across est, cet and aest so everyone can make at least two raids a week",
    "if a train leaves at 3pm going 60 mph and another leaves the same station at 4pm going 80 mph, when does the second catch up? show the steps",
    "why does this keep growing every call and how do i fix it?\n```python\ndef add(item, items=[]):\n    items.append(item)\n    return items\n```",
    "write a function in rust that parses a log file line by line and returns a hashmap of error counts by type, with error handling",
    "help me plan a 5 day trip to japan on a budget, with a day by day itinerary",
    "design a database schema for a server economy with wallets, items, trades and an audit log",
]


def _write(name: str, payload) -> None:
    path = WORK / f"checks-{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {path}")


def _quota_details(exc: Exception) -> list[dict]:
    details = getattr(exc, "details", None) or {}
    err = details.get("error", details) if isinstance(details, dict) else {}
    out = []
    for d in err.get("details", []) or []:
        for v in d.get("violations", []) or []:
            out.append({k: v.get(k) for k in ("quotaMetric", "quotaId", "quotaDimensions", "quotaValue")})
        if d.get("retryDelay"):
            out.append({"retryDelay": d["retryDelay"]})
    return out


async def cmd_pro_rpm(args) -> None:
    from google import genai
    from google.genai import types

    from olisar.gemini.pricing import request_cost
    from olisar.gemini.quota import quota_day

    ledger = Ledger(args.stop_at, None)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    results: list[dict] = []
    started = time.monotonic()

    async def one(n: int) -> None:
        await asyncio.sleep(n * args.spread / args.n)
        t0 = time.monotonic()
        row = {"n": n, "sent_at": round(t0 - started, 2)}
        try:
            resp = await client.aio.models.generate_content(
                model=PRO, contents="Reply with the word ok.",
                config=types.GenerateContentConfig(
                    max_output_tokens=64,
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
                ),
            )
            u = resp.usage_metadata
            inp = (u.prompt_token_count or 0) if u else 0
            out = ((u.candidates_token_count or 0) + (u.thoughts_token_count or 0)) if u else 0
            usd = request_cost(PRO, quota_day(), input_tokens=inp, output_tokens=out)
            ledger.add("pro-rpm", usd, n=n)
            row.update(status=200, usd=round(usd, 6))
        except Exception as exc:  # noqa: BLE001
            row.update(status=getattr(exc, "code", None), error=str(exc)[:300], quota=_quota_details(exc))
        row["seconds"] = round(time.monotonic() - t0, 2)
        results.append(row)
        print(f"#{n:<3} at {row['sent_at']:>5}s -> {row['status']} {row.get('quota') or ''}")

    await asyncio.gather(*(one(n) for n in range(args.n)))
    ok = sum(r["status"] == 200 for r in results)
    refused = [r for r in results if r["status"] == 429]
    print(f"{ok}/{args.n} answered within {args.spread:.0f}s; {len(refused)} refused with 429")
    _write("pro-rpm", {"at": _now(), "n": args.n, "spread_seconds": args.spread, "results": sorted(results, key=lambda r: r["n"])})


async def cmd_search(args) -> None:
    from google import genai
    from google.genai import types

    from olisar.gemini.pricing import request_cost
    from olisar.gemini.quota import quota_day

    ledger = Ledger(args.stop_at, None)
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    results = []
    for n in range(args.n):
        if not ledger.room():
            print("stopping: ledger cap")
            break
        query = SEARCH_QUERIES[(n // 2) % len(SEARCH_QUERIES)]
        mode = "default" if n % 2 == 0 else "low"
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        if mode == "low":
            config = config.model_copy(update={"thinking_config": types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)})
        t0 = time.monotonic()
        try:
            resp = await client.aio.models.generate_content(model=FLASH, contents=query, config=config)
        except Exception as exc:  # noqa: BLE001
            results.append({"n": n, "query": query, "mode": mode, "error": str(exc)[:300]})
            print(f"{mode:<8} {query[:40]:<40} error {exc}")
            continue
        seconds = time.monotonic() - t0
        u = resp.usage_metadata
        inp = ((u.prompt_token_count or 0) + (getattr(u, "tool_use_prompt_token_count", 0) or 0)) if u else 0
        thoughts = (u.thoughts_token_count or 0) if u else 0
        out = ((u.candidates_token_count or 0) + thoughts) if u else 0
        usd = request_cost(FLASH, quota_day(), input_tokens=inp, output_tokens=out)
        ledger.add("search", usd, n=n, mode=mode)
        try:
            meta = resp.candidates[0].grounding_metadata
            chunks = meta.grounding_chunks or []
            queries = list(meta.web_search_queries or [])
        except Exception:  # noqa: BLE001
            chunks, queries = [], []
        row = {"n": n, "query": query, "mode": mode, "seconds": round(seconds, 2), "usd": round(usd, 6),
               "in": inp, "out": out, "thinking": thoughts, "sources": len(chunks),
               "search_queries": len(queries), "chars": len(resp.text or "")}
        results.append(row)
        print(f"{mode:<8} {query[:40]:<40} {seconds:>5.1f}s ${usd:.4f} think={thoughts:<5} sources={len(chunks)} queries={len(queries)}")
    for mode in ("default", "low"):
        mine = [r for r in results if r.get("mode") == mode and "seconds" in r]
        if mine:
            secs = sorted(r["seconds"] for r in mine)
            print(f"{mode}: n={len(mine)} median {secs[len(secs) // 2]:.1f}s, mean {sum(secs) / len(secs):.1f}s, "
                  f"max {secs[-1]:.1f}s, mean ${sum(r['usd'] for r in mine) / len(mine):.4f}, "
                  f"mean sources {sum(r['sources'] for r in mine) / len(mine):.1f}")
    _write("search", {"at": _now(), "results": results})


async def cmd_concurrent_pro(args) -> None:
    from arena.config import load
    from arena.control.dashboard import Dashboard

    ledger = Ledger(args.stop_at, None)
    async with Dashboard(load()) as dash:
        config = await dash.get_config()
        if config.get("default_model") != "auto":
            raise SystemExit(f"the arena guild is pinned to {config.get('default_model')!r}; set it to auto")
        billing = await dash.billing()
        if billing.get("tier") != "paid":
            raise SystemExit(f"the instance's key is {billing.get('tier')!r}, not billed")

        async def one(n: int, text: str) -> dict:
            t0 = time.monotonic()
            payload = await dash.chat_routed([{"role": "user", "content": text}])
            route = payload.get("route") or {}
            return {"n": n, "seconds": round(time.monotonic() - t0, 1), **{k: route.get(k) for k in ("tier", "model", "pro", "capped", "reason", "cost_usd")}}

        results = await asyncio.gather(*(one(n, t) for n, t in enumerate(DEEP_MESSAGES[: args.n])))
    for r in results:
        ledger.add("concurrent-pro", r.get("cost_usd") or 0.0, n=r["n"], via="instance")
        print(f"#{r['n']} {r['tier']:<8} pro={r['pro']!s:<5} capped={r['capped'] or '-':<6} {r['model']:<24} {r['seconds']}s ${r['cost_usd']}")
    print(f"{sum(bool(r['pro']) for r in results)} of {len(results)} came back from Pro")
    _write("concurrent-pro", {"at": _now(), "results": results})


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("pro-rpm", "search", "concurrent-pro"):
        p = sub.add_parser(name)
        p.add_argument("--stop-at", type=float, default=18.0)
        p.add_argument("--n", type=int, default={"pro-rpm": 40, "search": 20, "concurrent-pro": 8}[name])
        p.add_argument("--spread", type=float, default=50.0, help="pro-rpm: seconds to spread the requests over")
    args = parser.parse_args()
    if args.cmd in ("pro-rpm", "search") and not os.environ.get("GEMINI_API_KEY"):
        raise SystemExit("set GEMINI_API_KEY in this process's environment")
    asyncio.run({"pro-rpm": cmd_pro_rpm, "search": cmd_search, "concurrent-pro": cmd_concurrent_pro}[args.cmd](args))


if __name__ == "__main__":
    main()
