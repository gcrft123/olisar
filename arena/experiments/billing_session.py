"""Run the arena instance on a key with billing on, and keep an eye on what it costs.

    uv run python -m arena.experiments.billing_session status
    uv run python -m arena.experiments.billing_session use-key GOOGLE_BILLING_KEY
    uv run python -m arena.experiments.billing_session budget 12.5 --action stop
    uv run python -m arena.experiments.billing_session drop-key

``use-key`` reads the named variable from ``.env.arena`` (never printing it), checks it the
way the console's API keys page does, and saves it as the instance's dashboard key, which
wins over ``GEMINI_API_KEY`` until ``drop-key`` removes it. ``status`` prints the key's
tier, the month's spend by the instance's own count, the budget and the routing switches.

A free key's usage counts toward the month too, priced as if it were billed, so set a
budget relative to ``status``'s figure, not from zero.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re

from arena.config import REPO_ROOT, load
from arena.control.dashboard import Dashboard


def _env_value(name: str) -> str:
    for line in (REPO_ROOT / ".env.arena").read_text().splitlines():
        m = re.match(rf"\s*{re.escape(name)}\s*=\s*(.*)\s*$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    raise SystemExit(f"{name} isn't in .env.arena")


async def status(dash: Dashboard) -> dict:
    billing = await dash.billing()
    live = await dash._call("GET", "/api/usage/live")
    b = live.get("billing") or {}
    out = {
        "tier": billing.get("tier"),
        "month_usd": round(b.get("month", 0.0), 4),
        "today_usd": round(b.get("today", 0.0), 4),
        "budget_usd": billing.get("monthly_budget_usd"),
        "budget_action": billing.get("budget_action"),
        "budget_state": b.get("state"),
        "routing_free": billing.get("routing_free"),
        "routing_paid": billing.get("routing_paid"),
        "gemini_images": billing.get("gemini_images"),
    }
    print(" ".join(f"{k}={v}" for k, v in out.items()))
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    use = sub.add_parser("use-key")
    use.add_argument("var", help="the .env.arena variable holding the key")
    sub.add_parser("drop-key")
    budget = sub.add_parser("budget")
    budget.add_argument("usd", type=float)
    budget.add_argument("--action", choices=("stop", "cheapest"), default="stop")
    args = parser.parse_args()

    async with Dashboard(load()) as dash:
        if args.cmd == "use-key":
            key = _env_value(args.var)
            print(f"key fingerprint {hashlib.sha256(key.encode()).hexdigest()[:12]}")
            check = await dash._call("POST", "/api/keys/check/gemini", json={"key": key})
            print(f"check: ok={check.get('ok')} tier={check.get('tier')}")
            if not check.get("ok"):
                raise SystemExit("Google didn't accept the key; not saving it")
            await dash._call("PUT", "/api/keys", json={"gemini_api_key": key})
            print("saved as the instance's dashboard key")
        elif args.cmd == "drop-key":
            await dash._call("DELETE", "/api/keys/gemini_api_key")
            print("dashboard key removed; the instance is back on GEMINI_API_KEY")
        elif args.cmd == "budget":
            await dash.set_billing(monthly_budget_usd=args.usd, budget_action=args.action)
        await status(dash)


if __name__ == "__main__":
    asyncio.run(main())
