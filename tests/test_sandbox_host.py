"""Extension code runs in a child process the backend can stop, within its time limits.

Run:  uv run python -m unittest tests.test_sandbox_host -v

Extension code used to run in QuickJS on a thread of the backend, and three limits leaked.
QuickJS's regex engine never checks for an interrupt, so a backtracking pattern kept a sandbox
thread for as long as it took (days, for a long enough input), and nothing can stop a thread;
the import preview runs an extension's top-level code, so a file someone only looked at was
enough. The CPU limit restarted on every ``await``, so code that yielded between bursts of work
was never stopped by it. And a slow host call (a drip-fed fetch) held the run for 900 s,
whatever the run's own wall budget was.

Now the code runs in a sandbox host process that spends one CPU budget across the whole run.
The backend kills a host that keeps the turn well past that budget, and cancels a host call
when the run's wall budget runs out. These start real host processes.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from olisar.runtime.sandbox_host import write_frame
from olisar.sandbox import capabilities, engine, runner
from olisar.sandbox.capabilities import Invocation

PAYLOAD = {"args": {}, "ctx": {}}


def _tool(body: str) -> str:
    """A one-tool extension whose async handler runs ``body``."""
    return (
        "defineExtension({id:'x',tools:[{name:'t',parameters:{type:'object',properties:{}},"
        "handler: async function(args, ctx){ " + body + " }}]});"
    )


def _invoke(body: str, perform=lambda *a: None, **limits):
    return engine.invoke(_tool(body), "tool", "t", PAYLOAD, perform, **limits)


# Takes seconds at n=25 and doubles with each extra "a".
BACKTRACK = "/^(a+)+$/.test('a'.repeat(30) + '!')"


class LimitTests(unittest.TestCase):
    def test_backtracking_regex_is_stopped(self) -> None:
        started = time.monotonic()
        with self.assertRaisesRegex(engine.SandboxError, "CPU budget"):
            _invoke(f"return {BACKTRACK};", cpu_seconds=0.3, wall_seconds=30)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_backtracking_regex_in_top_level_code_is_stopped(self) -> None:
        # What an import or marketplace preview runs, before anyone agrees to anything.
        started = time.monotonic()
        with mock.patch.object(engine, "TOOL_CPU_SECONDS", 0.3):
            with self.assertRaises(engine.SandboxError):
                engine.extract_manifest(f"var hit = {BACKTRACK};" + _tool("return 1;"))
        self.assertLess(time.monotonic() - started, 3.0)

    def test_cpu_budget_spans_awaits(self) -> None:
        # 250 ms of work between awaits: each turn alone is inside a 0.3 s budget.
        started = time.monotonic()
        with self.assertRaises(engine.SandboxError):
            _invoke("for(;;){ var t=Date.now(); while(Date.now()-t<250){} await null; }",
                    cpu_seconds=0.3, wall_seconds=30)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_work_within_budget_still_finishes(self) -> None:
        body = "var n=0; for(var i=0;i<5;i++){ var t=Date.now(); while(Date.now()-t<20){} n++; await null; } return n;"
        self.assertEqual(_invoke(body, cpu_seconds=2.0), 5)

    def test_a_stopped_host_is_replaced(self) -> None:
        with self.assertRaises(engine.SandboxError):
            _invoke(f"return {BACKTRACK};", cpu_seconds=0.2)
        self.assertEqual(_invoke("return 'next';"), "next")

    def test_malformed_host_calls_are_a_sandbox_error(self) -> None:
        body = "globalThis.__drainOutbox = function(){ return '\"not a list\"'; }; await host.log('x'); return 1;"
        with self.assertRaises(engine.SandboxError):
            _invoke(body)


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_runs_each_get_their_own_result(self) -> None:
        results: dict[int, object] = {}

        def run(i: int) -> None:
            try:
                if i % 4 == 0:  # a runaway next to well-behaved runs
                    _invoke(f"return {BACKTRACK};", cpu_seconds=0.2)
                else:
                    body = "var n=0; for(var k=0;k<20;k++){ n += await host.log('x'); } return [args.i, n];"
                    results[i] = engine.invoke(
                        _tool(body), "tool", "t", {"args": {"i": i}, "ctx": {}}, lambda *a: 1,
                    )
            except engine.SandboxError as exc:
                results[i] = str(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        for i in range(12):
            if i % 4 == 0:
                self.assertIn("CPU budget", results[i])
            else:
                self.assertEqual(results[i], [i, 20])


class WallBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_host_call_is_cancelled_at_the_wall_budget(self) -> None:
        cancelled = asyncio.Event()

        async def slow_dispatch(inv, cap, method, args):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        inv = Invocation(ext_key="x", permissions=set(), guild_id=1)
        started = time.monotonic()
        with mock.patch.object(capabilities, "dispatch", slow_dispatch):
            with self.assertRaisesRegex(engine.SandboxError, "time budget"):
                await runner._invoke(inv, _tool("await host.log('x'); return 'done';"),
                                     "tool", "t", PAYLOAD, wall_seconds=0.5)
        self.assertLess(time.monotonic() - started, 3.0)
        await asyncio.wait_for(cancelled.wait(), 5)

    async def test_a_host_call_that_times_out_itself_is_the_extensions_to_catch(self) -> None:
        async def timing_out(inv, cap, method, args):
            raise TimeoutError("nobody clicked")

        inv = Invocation(ext_key="x", permissions=set(), guild_id=1)
        with mock.patch.object(capabilities, "dispatch", timing_out):
            out = await runner._invoke(
                inv, _tool("try { await host.log('x'); } catch (e) { return 'caught'; } return 'no';"),
                "tool", "t", PAYLOAD,
            )
        self.assertEqual(out, "caught")

    async def test_tool_runs_are_bounded_by_the_tool_budget(self) -> None:
        async def slow_dispatch(inv, cap, method, args):
            await asyncio.sleep(30)

        ctx = SimpleNamespace(
            cfg_guild=1, channel_id=2, user_id=3, display_name="u", session=None,
            actions=None, is_dm=False, readable=lambda: None,
        )
        started = time.monotonic()
        with mock.patch.object(engine, "TOOL_WALL_SECONDS", 0.5), \
                mock.patch.object(capabilities, "dispatch", slow_dispatch):
            with self.assertRaises(engine.SandboxError):
                await runner.run_tool(
                    ext_key="x", compiled_js=_tool("await host.log('x'); return 'done';"),
                    permissions=[], tool_name="t", args={}, ctx=ctx,
                )
        self.assertLess(time.monotonic() - started, 3.0)


class HostProcessTests(unittest.TestCase):
    def test_host_gets_none_of_the_backends_environment(self) -> None:
        seen: list[dict] = []
        real = subprocess.Popen

        def spy(*args, **kwargs):
            seen.append(kwargs["env"])
            return real(*args, **kwargs)

        with mock.patch.dict(os.environ, {"DISCORD_TOKEN": "canary", "GEMINI_API_KEY": "canary"}), \
                mock.patch.object(engine.subprocess, "Popen", spy):
            host = engine._Host()
        host.close()
        self.assertEqual(len(seen), 1)
        self.assertNotIn("canary", seen[0].values())
        self.assertLessEqual(set(seen[0]), set(engine._ENV_KEEP) | {"PYTHONUTF8"})

    def test_idle_host_exits_when_the_backend_goes_away(self) -> None:
        host = engine._Host()
        host.proc.stdin.close()
        self.assertEqual(host.proc.wait(10), 0)

    def test_stuck_host_exits_when_the_backend_goes_away(self) -> None:
        # A run stuck in a regex never reads stdin, so it can't see the pipe close; it ends
        # itself once it's far past its CPU budget instead of running on unowned.
        host = engine._Host()
        write_frame(host.proc.stdin, {
            "op": "invoke", "js": _tool("return /^(a+)+$/.test('a'.repeat(40) + '!');"),
            "kind": "tool", "name": "t", "payload": PAYLOAD, "cpu": 0.2, "memory": 64 << 20,
        })
        host.proc.stdin.close()
        try:
            self.assertNotEqual(host.proc.wait(30), 0)
        finally:
            host.kill()

    def test_self_check_runs_through_a_host(self) -> None:
        self.assertTrue(engine.self_check())


if __name__ == "__main__":
    unittest.main()
