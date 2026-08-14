"""The arena CLI — the agent's single entrypoint.

    uv run python -m arena <command> [...]

Everything the harness can do is a subcommand, and every subcommand prints a short,
greppable result. That shape is deliberate: the intended operator is an agent making one
short call at a time, reading stdout, and deciding what to do next — not a human holding a
session open.

``arena doctor`` first. It reports exactly which pieces of setup are missing and what to do
about each, which is faster than watching a scenario fail for a reason three layers down.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from arena import config as arena_config
from arena.config import ArenaConfig, ConfigError

log = logging.getLogger("arena.cli")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _print(payload: Any) -> None:
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(payload)


# ── setup & health ────────────────────────────────────────────────────────


async def cmd_doctor(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    """Check every prerequisite and say what's missing."""
    from arena.backends import ClaudeCliBackend
    from arena.control import supervisor
    from arena.control.dashboard import wait_until_healthy
    from arena.discord_rest import DiscordRest
    from arena.fleet import registry
    from arena.fleet.persona import load_all as load_personas
    from arena.scenarios.schema import load_all as load_scenarios

    problems: list[str] = []
    notes: list[str] = []

    def check(label: str, ok: bool, fix: str = "") -> None:
        print(f"  {'ok  ' if ok else 'MISS'}  {label}")
        if not ok and fix:
            problems.append(f"{label}: {fix}")

    print(f"config file: {arena_config.ENV_FILE}"
          f"{'' if arena_config.ENV_FILE.is_file() else '  (does not exist yet)'}")
    print("\nsettings")
    check("ARENA_DISCORD_TOKEN", bool(cfg.discord_token), "the arena Olisar bot token")
    check("ARENA_GUILD_ID", bool(cfg.guild_id), "the test server's id")
    check("ARENA_OPERATOR_ID", bool(cfg.operator_id), "your Discord user id")
    check("ARENA_STEWARD_TOKEN", bool(cfg.steward_token), "an Administrator bot for server management")
    # The instance under test always needs a Gemini key. The harness only needs one if a
    # role is pointed back at Gemini — checked separately under "harness models" below, so
    # a Claude-only harness doesn't report a problem it doesn't have.
    check("GEMINI_API_KEY", bool(cfg.gemini_api_key),
          "inherited from .env — the instance under test needs it whatever the harness uses")
    check(
        f"fleet tokens ({len(cfg.fleet_tokens)} found)",
        bool(cfg.fleet_tokens),
        "at least one ARENA_BOT_TOKEN_<PERSONA>",
    )

    print("\nharness models")
    for role, backend, model in (
        ("dialogue", cfg.dialogue_backend, cfg.dialogue_model),
        ("judge", cfg.judge_backend, cfg.judge_model),
    ):
        if backend == "claude":
            found = ClaudeCliBackend.available(cfg.claude_binary)
            check(f"{role}: claude/{model}", found,
                  f"{cfg.claude_binary!r} is not on PATH — install the Claude CLI or set "
                  f"ARENA_{role.upper()}_BACKEND=gemini")
        elif backend == "gemini":
            check(f"{role}: gemini/{model}", bool(cfg.gemini_api_key), "needs GEMINI_API_KEY")
        else:
            check(f"{role}: {backend!r}", False, "backend must be 'claude' or 'gemini'")

    print("\ncontent")
    personas = load_personas()
    scenarios = load_scenarios()
    check(f"personas ({len(personas)})", bool(personas))
    check(f"scenarios ({len(scenarios)})", bool(scenarios))
    unbacked = sorted(set(personas) - set(cfg.fleet_tokens))
    if unbacked:
        notes.append(
            f"personas with no bot token, unusable in the live lane: {', '.join(unbacked)} "
            f"(set ARENA_BOT_TOKEN_{unbacked[0].upper()})"
        )

    print("\ndiscord")
    if cfg.discord_token:
        try:
            async with DiscordRest(cfg.discord_token, label="olisar") as rest:
                me = await rest.me()
            check(f"Olisar token -> {me.get('username')}", True)
        except Exception as exc:  # noqa: BLE001
            check("Olisar token", False, f"rejected by Discord: {exc}")
    if cfg.steward_token and cfg.guild_id:
        try:
            async with DiscordRest(cfg.steward_token, label="steward") as rest:
                me = await rest.me()
                guild = await rest.guild(cfg.guild_id)
                check(f"steward {me.get('username')} in '{guild.get('name')}'", True)
                try:
                    members = await rest.members(cfg.guild_id, limit=5)
                    check(f"member list readable ({len(members)} sampled)", True)
                except Exception:
                    check(
                        "Server Members Intent",
                        False,
                        "enable it on the steward application in the Developer Portal",
                    )
        except Exception as exc:  # noqa: BLE001
            check("steward token / guild access", False, str(exc))

    resolved = registry.cached(cfg)
    check(
        f"emulator ids resolved ({len(resolved)}/{len(cfg.fleet_tokens)})",
        len(resolved) == len(cfg.fleet_tokens) and bool(resolved),
        "run: uv run python -m arena fleet resolve",
    )

    print("\ninstance")
    pid = supervisor.running_pid(cfg)
    check(f"process (pid {pid or '-'})", bool(pid), "run: uv run python -m arena up")
    if pid:
        try:
            health = await wait_until_healthy(cfg, timeout=10)
            check(f"api healthy (model self-test: {health.get('model') or 'not yet run'})", True)
        except Exception as exc:  # noqa: BLE001
            check("api healthy", False, f"{exc}")

    if notes:
        print("\nnotes")
        for note in notes:
            print(f"  - {note}")
    if problems:
        print(f"\n{len(problems)} thing(s) to fix:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nSee arena/README.md for how to obtain each value.")
        return 1
    print("\nready.")
    return 0


async def cmd_up(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control import supervisor
    from arena.control.dashboard import wait_until_healthy

    pid = supervisor.start(cfg)
    health = await wait_until_healthy(cfg, timeout=args.timeout)
    _print({"pid": pid, "api": cfg.api_base, "health": health})
    return 0


async def cmd_down(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control import supervisor

    _print({"stopped": supervisor.stop(cfg)})
    return 0


async def cmd_restart(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    """Deploy: pick up a source change in one process restart."""
    from arena.control import supervisor
    from arena.control.dashboard import wait_until_healthy

    pid = supervisor.restart(cfg)
    health = await wait_until_healthy(cfg, timeout=args.timeout)
    _print({"pid": pid, "health": health})
    return 0


async def cmd_status(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control import supervisor
    from arena.control.dashboard import Dashboard
    from arena.fleet import registry
    from arena.model import ModelClient

    out: dict = {
        "pid": supervisor.running_pid(cfg),
        "api": cfg.api_base,
        "data_dir": str(cfg.data_dir),
        "guild_id": cfg.guild_id,
        "fleet": [{"key": m.key, "user_id": m.user_id, "username": m.username}
                  for m in registry.cached(cfg)],
    }
    try:
        client = ModelClient(cfg)
        out["harness_models"] = client.describe()
        out["harness_budget"] = client.usage()
    except (ConfigError, ValueError) as exc:
        out["harness_models"] = str(exc)
    if out["pid"]:
        try:
            async with Dashboard(cfg) as dash:
                out["health"] = await dash.health()
                out["usage"] = await dash.usage()
        except Exception as exc:  # noqa: BLE001
            out["dashboard_error"] = str(exc)
    _print(out)
    return 0


async def cmd_logs(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control import supervisor

    for line in supervisor.tail(cfg, lines=args.lines, grep=args.grep):
        print(line)
    return 0


# ── fleet ─────────────────────────────────────────────────────────────────


async def cmd_fleet(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.fleet import registry
    from arena.fleet.persona import load_all as load_personas

    if args.fleet_action == "resolve":
        members = await registry.resolve(cfg, force=args.force)
        _print([{"key": m.key, "user_id": m.user_id, "username": m.username} for m in members])
        print(
            f"\n{len(members)} emulator(s) resolved. Restart the instance so it picks up "
            f"OLISAR_PEER_BOT_IDS:\n  uv run python -m arena restart"
        )
        return 0

    personas = load_personas()
    resolved = {m.key: m for m in registry.cached(cfg)}
    rows = []
    for key, persona in sorted(personas.items()):
        member = resolved.get(key)
        rows.append(
            {
                "key": key,
                "display_name": persona.display_name,
                "token": key in cfg.fleet_tokens,
                "user_id": member.user_id if member else None,
                "traits": persona.traits,
            }
        )
    _print(rows)
    return 0


# ── discord server management ─────────────────────────────────────────────


async def cmd_guild(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control.guild import Steward, olisar_user_id
    from arena.fleet import registry

    async with Steward(cfg) as steward:
        if args.guild_action == "snapshot":
            snapshot = await steward.snapshot()
            _print(
                {
                    "guild": snapshot.name,
                    "channels": [
                        {"id": c["id"], "name": c.get("name"), "type": c.get("type")}
                        for c in snapshot.channels
                    ],
                    "roles": [{"id": r["id"], "name": r.get("name")} for r in snapshot.roles],
                    "members": [
                        {"id": (m.get("user") or {}).get("id"),
                         "name": (m.get("user") or {}).get("username"),
                         "bot": (m.get("user") or {}).get("bot", False)}
                        for m in snapshot.members
                    ],
                }
            )
            return 0

        if args.guild_action == "channel":
            if args.delete:
                _print({"deleted": await steward.delete_channel(args.name)})
                return 0
            allowed: list[int] = []
            if args.private:
                for who in args.members:
                    if who == "olisar":
                        allowed.append(await olisar_user_id(cfg))
                    else:
                        match = [m for m in registry.cached(cfg) if m.key == who]
                        if match:
                            allowed.append(match[0].user_id)
                        else:
                            try:
                                allowed.append(int(who))
                            except ValueError:
                                print(f"unknown member {who!r} — use a persona key, "
                                      f"'olisar', or a raw user id", file=sys.stderr)
                                return 2
                allowed.append(await steward.rest.my_id())
            channel_id = await steward.ensure_channel(
                args.name,
                private=args.private,
                members=sorted(set(allowed)),
                category=args.category,
                topic=args.topic,
                recreate=args.recreate,
            )
            _print({"channel_id": channel_id, "name": args.name, "private": args.private})
            return 0

        if args.guild_action == "role":
            if args.assign:
                await steward.assign_role(args.name, int(args.assign))
                _print({"role": args.name, "assigned_to": args.assign})
                return 0
            _print({"role_id": await steward.ensure_role(args.name)})
            return 0

        if args.guild_action == "reset":
            _print(await steward.reset())
            return 0
    return 0


# ── configuration ─────────────────────────────────────────────────────────


def _kv(pairs: list[str]) -> dict:
    """Parse ``key=value`` arguments, JSON-decoding values so numbers, booleans and lists
    arrive as the types the API expects rather than as strings."""
    out: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


async def cmd_persona(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control.dashboard import Dashboard

    async with Dashboard(cfg) as dash:
        if args.set:
            await dash.set_persona(**_kv(args.set))
        _print(await dash.get_persona())
    return 0


async def cmd_config(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control.dashboard import Dashboard

    async with Dashboard(cfg) as dash:
        if args.set:
            await dash.set_config(**_kv(args.set))
        _print(await dash.get_config())
    return 0


async def cmd_proactivity(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control.dashboard import Dashboard

    async with Dashboard(cfg) as dash:
        if args.set:
            await dash.set_proactivity(**_kv(args.set))
        _print(await dash.get_proactivity())
    return 0


async def cmd_models(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    """Show which backend each role uses, and optionally prove each one works.

    ``--test`` makes one real call per role, which is the only way to find out that the
    CLI is logged out or the Gemini key is stale before a scenario discovers it halfway
    through a run.
    """
    from arena.model import DIALOGUE, JUDGE, ModelClient

    client = ModelClient(cfg)
    out: dict = {"roles": client.describe(), "budget": client.usage()}
    if args.test:
        out["dialogue_sample"] = await client.generate(
            "Someone in the channel just said the server wiki is out of date. "
            "Reply in one short lowercase line, like a regular member would.",
            system="You write a single Discord message. Output only the message text.",
            role=DIALOGUE,
        ) or "(empty — see the log)"
        verdict = await client.generate_json(
            'Return {"ok": true} and nothing else.',
            system="You are a test harness probe.",
            role=JUDGE,
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"], "additionalProperties": False},
        )
        out["judge_reachable"] = bool(verdict)
        out["budget_after"] = client.usage()
    _print(out)
    return 0


async def cmd_chat(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    """One turn against the fast lane — the quickest way to see a prompt change land."""
    from arena.control.dashboard import Dashboard

    async with Dashboard(cfg) as dash:
        print(await dash.ask(" ".join(args.message)))
    return 0


async def cmd_clear_memory(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control.dashboard import Dashboard

    if not args.yes:
        print("this erases everything the arena instance has learned. re-run with --yes")
        return 2
    async with Dashboard(cfg) as dash:
        _print(await dash.clear_memory())
    return 0


# ── scenarios, runs, scoring ──────────────────────────────────────────────


async def cmd_scenarios(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.scenarios.schema import load_all

    _print(
        [
            {"id": s.id, "lane": s.lane, "tags": s.tags, "cast": s.cast, "title": s.title}
            for s in sorted(load_all().values(), key=lambda s: s.id)
        ]
    )
    return 0


async def cmd_run(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.control import supervisor
    from arena.eval.judge import Judge
    from arena.experiments import variants
    from arena.fleet.runner import execute
    from arena.scenarios.schema import load

    scenario = load(args.scenario)
    if args.variant:
        await variants.apply(cfg, variants.load(args.variant))
    if not scenario.is_fast:
        supervisor.truncate_log(cfg)

    run = await execute(cfg, scenario, variant=args.variant or "baseline")
    directory = run.save(olisar_log=supervisor.tail(cfg, lines=400) if not scenario.is_fast else None)

    print(f"\n--- {run.run_id} ---")
    print(run.render() or "(nothing was said)")
    print("\nchecks:")
    for check in run.checks:
        print(f"  {'PASS' if check.passed else 'FAIL'}  {check.name}"
              f"{'  — ' + check.detail if check.detail else ''}")
    if run.error:
        print(f"\nERROR: {run.error}")
    if args.judge and not run.error:
        scores = await Judge(cfg).score(run, scenario.rubric or None)
        print("\nscores:")
        _print({"dimensions": scores.dimensions, "worst_tell": scores.worst_tell, "note": scores.note})
    print(f"\nsaved to {directory}")
    return 0 if run.ok else 1


async def cmd_redteam(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.eval import redteam
    from arena.experiments import variants

    if args.variant:
        await variants.apply(cfg, variants.load(args.variant))
    result = await redteam.run_gate(cfg, variant=args.variant or "baseline")
    print(result.summary())
    for failure in result.failures:
        print(f"\n  {failure['scenario']} — {failure['title']}")
        for check in failure["checks"]:
            print(f"    FAIL {check['name']} {check['detail']}")
        for reply in failure["replies"]:
            print(f"    reply: {reply[:300]}")
    for error in result.errors:
        print(f"\n  {error['scenario']}: could not run — {error['error']}")
    return 0 if result.passed else 1


async def cmd_calibrate(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.eval.judge import Judge

    result = await Judge(cfg).calibrate()
    _print(result)
    print(
        f"\nfloor       {result['correct']}/{result['total']} "
        f"({result['order_flips']} order flip(s)) — can it spot obvious slop?"
    )
    sensitivity = result["sensitivity"]
    print(f"sensitivity {sensitivity:.0%} — can it separate a human from a *good* bot reply?")
    if not result["trustworthy"]:
        print("\nThe judge cannot reliably pick a human reply out of a lineup. Naturalness "
              "verdicts this session carry no signal — fix the rubric or the judge model "
              "before reading any comparison.")
    elif not result["sensitive"]:
        print("\nThe judge passes the floor but is near chance on subtle pairs. It will "
              "still return confident verdicts; they just won't track quality. Treat narrow "
              "margins as noise, and prefer a stronger judge model before trusting a "
              "promotion.")
    return 0 if result["trustworthy"] else 1


async def cmd_variant(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.experiments import loop, variants

    if args.variant_action == "list":
        champion = loop.read_champion()
        _print(
            [
                {
                    "name": v.name,
                    "champion": v.name == champion,
                    "parent": v.parent,
                    "changes": v.describe(),
                    "hypothesis": v.hypothesis,
                }
                for v in sorted(variants.load_all().values(), key=lambda v: v.name)
            ]
        )
        return 0
    if args.variant_action == "show":
        variant = variants.load(args.name)
        _print(
            {
                "name": variant.name,
                "hypothesis": variant.hypothesis,
                "prompt_overrides": variant.prompt_overrides,
                "persona": variant.persona,
                "config": variant.config,
                "proactivity": variant.proactivity,
            }
        )
        print("\n" + variants.landing_instructions(variant))
        return 0
    if args.variant_action == "apply":
        _print(await variants.apply(cfg, variants.load(args.name)))
        return 0
    return 0


async def cmd_loop(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.experiments.loop import run_round

    for _ in range(args.rounds):
        record = await run_round(cfg, tags=args.tags, lane=args.lane, block=args.block)
        _print(asdict_round(record))
        if record.stopped:
            print(f"\nloop stopped: {record.stopped}")
            return 1
    return 0


def asdict_round(record: Any) -> dict:
    from dataclasses import asdict

    return asdict(record)


async def cmd_report(cfg: ArenaConfig, args: argparse.Namespace) -> int:
    from arena.eval.scorecard import SCORECARD_DIR, load_scorecard
    from arena.experiments import loop, variants

    champion = loop.read_champion()
    cards = []
    if SCORECARD_DIR.is_dir():
        for path in sorted(SCORECARD_DIR.glob("*.json")):
            card = load_scorecard(path.stem)
            if card:
                cards.append(
                    {
                        "variant": card.variant,
                        "champion": card.variant == champion,
                        "mean": round(card.mean, 2),
                        "dimensions": {k: round(v, 2) for k, v in card.dimension_means.items()},
                        "checks_pass_rate": round(card.checks_pass_rate, 2),
                        "gate": card.gate_summary or "(not gated)",
                        "top_tells": card.tells()[:3],
                    }
                )
    _print({"champion": champion, "scorecards": cards})
    if champion != variants.BASELINE:
        print("\n" + variants.landing_instructions(variants.load(champion)))
    return 0


# ── wiring ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arena", description=__doc__.split("\n")[0])
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check every prerequisite and report what's missing")

    up = sub.add_parser("up", help="start the Olisar instance under test")
    up.add_argument("--timeout", type=float, default=120.0)
    sub.add_parser("down", help="stop the instance")
    restart = sub.add_parser("restart", help="deploy a code change (stop + start)")
    restart.add_argument("--timeout", type=float, default=120.0)
    sub.add_parser("status", help="process, health, quota, fleet")
    logs = sub.add_parser("logs", help="tail the instance log")
    logs.add_argument("-n", "--lines", type=int, default=120)
    logs.add_argument("--grep", default="")

    fleet = sub.add_parser("fleet", help="the member emulators")
    fleet_sub = fleet.add_subparsers(dest="fleet_action", required=True)
    fleet_sub.add_parser("list", help="personas and whether each has a token/resolved id")
    resolve = fleet_sub.add_parser("resolve", help="map each token to its Discord account")
    resolve.add_argument("--force", action="store_true")

    guild = sub.add_parser("guild", help="manage the test server")
    guild_sub = guild.add_subparsers(dest="guild_action", required=True)
    guild_sub.add_parser("snapshot", help="channels, roles, members")
    channel = guild_sub.add_parser("channel", help="create or delete a channel")
    channel.add_argument("name")
    channel.add_argument("--private", action="store_true")
    channel.add_argument("--members", nargs="*", default=[],
                         help="persona keys, 'olisar', or raw user ids (private channels)")
    channel.add_argument("--category", default="")
    channel.add_argument("--topic", default="")
    channel.add_argument("--recreate", action="store_true", help="delete and rebuild if it exists")
    channel.add_argument("--delete", action="store_true")
    role = guild_sub.add_parser("role", help="create a role or assign it")
    role.add_argument("name")
    role.add_argument("--assign", default="", help="user id to grant it to")
    guild_sub.add_parser("reset", help="delete every arena-created channel and role")

    persona = sub.add_parser("persona", help="read/write Olisar's operator-editable prompt")
    persona.add_argument("--set", nargs="*", metavar="KEY=VALUE")
    cfg_cmd = sub.add_parser("config", help="read/write guild behaviour config")
    cfg_cmd.add_argument("--set", nargs="*", metavar="KEY=VALUE")
    proactivity = sub.add_parser("proactivity", help="read/write proactivity settings")
    proactivity.add_argument("--set", nargs="*", metavar="KEY=VALUE")

    models = sub.add_parser("models", help="which backend each harness role uses")
    models.add_argument("--test", action="store_true", help="make one real call per role")

    chat = sub.add_parser("chat", help="one turn against the fast lane (no Discord, no memory)")
    chat.add_argument("message", nargs="+")
    clear = sub.add_parser("clear-memory", help="wipe what the instance has learned")
    clear.add_argument("--yes", action="store_true")

    sub.add_parser("scenarios", help="list the scenario library")
    run = sub.add_parser("run", help="execute one scenario")
    run.add_argument("scenario")
    run.add_argument("--variant", default="")
    run.add_argument("--judge", action="store_true", help="also score the transcript")

    rt = sub.add_parser("redteam", help="run the guardrail regression gate")
    rt.add_argument("--variant", default="")
    sub.add_parser("calibrate", help="check whether the judge can spot a human reply")

    variant = sub.add_parser("variant", help="named configurations under test")
    variant_sub = variant.add_subparsers(dest="variant_action", required=True)
    variant_sub.add_parser("list")
    show = variant_sub.add_parser("show")
    show.add_argument("name")
    apply_cmd = variant_sub.add_parser("apply")
    apply_cmd.add_argument("name")

    loop_cmd = sub.add_parser("loop", help="autonomous measure/propose/gate/compare rounds")
    loop_cmd.add_argument("--rounds", type=int, default=1)
    loop_cmd.add_argument("--tags", nargs="*", default=["everyday"])
    loop_cmd.add_argument("--lane", default="")
    loop_cmd.add_argument("--block", default="operating_rules",
                          choices=["operating_rules", "tools_note", "proactive_note"])

    sub.add_parser("report", help="scorecards, the current champion, and how to land it")
    return parser


_HANDLERS = {
    "doctor": cmd_doctor, "up": cmd_up, "down": cmd_down, "restart": cmd_restart,
    "status": cmd_status, "logs": cmd_logs, "fleet": cmd_fleet, "guild": cmd_guild,
    "persona": cmd_persona, "config": cmd_config, "proactivity": cmd_proactivity,
    "models": cmd_models, "chat": cmd_chat, "clear-memory": cmd_clear_memory,
    "scenarios": cmd_scenarios,
    "run": cmd_run, "redteam": cmd_redteam, "calibrate": cmd_calibrate,
    "variant": cmd_variant, "loop": cmd_loop, "report": cmd_report,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = arena_config.load()
    try:
        return asyncio.run(_HANDLERS[args.command](cfg, args))
    except ConfigError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
