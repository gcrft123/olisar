"""Renaming the Tailscale device, which moves the console to a new address.

Run:  uv run python -m unittest tests.test_tunnel_rename -v

The name is the first label of ``https://<name>.<tailnet>.ts.net``, so the rules are DNS's. A
rename on this machine restarts the funnel under the new name, and one that fails puts the old
name back rather than leave the console with no address. Either way the operator is told when
Tailscale gave the device some other name than the one asked for. A server bot's rename runs
over SSH and is covered in test_shared_server.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from api.routers import tunnel as tunnel_api
from api.schemas import TunnelRenameIn
from olisar import runtime_config
from olisar.db import engine
from olisar.runtime import state
from olisar.runtime.tunnel import device_name, rename_note


class FakeFunnel:
    """The FunnelManager surface the endpoint uses. Each start answers from ``answers`` by
    hostname: ``(True, url)`` or ``(False, reason)``."""

    def __init__(self, answers: dict[str, tuple[bool, str]]) -> None:
        self.answers = answers
        self.running = True
        self.started: list[str] = []

    async def stop(self) -> None:
        self.running = False

    async def start(self, auth_key: str, hostname: str, target: str, state_dir: str) -> tuple[bool, str]:
        self.started.append(hostname)
        ok, out = self.answers[hostname]
        self.running = ok
        return ok, out


class LocalRenameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        engine.pin_database(str(self.dir / "olisar.db"))
        runtime_config.invalidate()
        await create_schema()
        state._last = {}
        patcher = mock.patch.object(state, "state_path", lambda: self.dir / state.STATE_FILENAME)
        patcher.start()
        self.addCleanup(patcher.stop)
        await runtime_config.save(
            tunnel_enabled=True, tunnel_node="olisar", tunnel_hostname="olisar.tail1.ts.net",
            tunnel_token="tskey-auth-kept",
        )

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        state._last = {}

    def request(self, funnel: FakeFunnel) -> SimpleNamespace:
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tunnel=funnel)))

    def published(self) -> dict:
        return json.loads((self.dir / state.STATE_FILENAME).read_text("utf-8"))

    async def test_moves_the_console_to_the_new_name(self) -> None:
        funnel = FakeFunnel({"everest": (True, "https://everest.tail1.ts.net/")})
        out = await tunnel_api.rename(TunnelRenameIn(hostname=" Everest "), self.request(funnel))
        self.assertEqual(out, {
            "ok": True,
            "public_url": "https://everest.tail1.ts.net",
            "redirect_uri": "https://everest.tail1.ts.net/auth/callback",
            "note": "",
        })
        self.assertEqual(funnel.started, ["everest"])
        self.assertEqual(await runtime_config.tunnel_node(), "everest")
        self.assertEqual(await runtime_config.public_base_url(), "https://everest.tail1.ts.net")
        self.assertEqual(await runtime_config.tunnel_token(), "tskey-auth-kept")
        self.assertEqual(self.published()["public_url"], "https://everest.tail1.ts.net")

    async def test_says_when_tailscale_picked_another_name(self) -> None:
        funnel = FakeFunnel({"everest": (True, "https://everest-1.tail1.ts.net")})
        out = await tunnel_api.rename(TunnelRenameIn(hostname="everest"), self.request(funnel))
        self.assertIn("already called everest", out["note"])
        self.assertEqual(await runtime_config.tunnel_hostname(), "everest-1.tail1.ts.net")

    async def test_a_failed_rename_puts_the_old_name_back(self) -> None:
        funnel = FakeFunnel({
            "everest": (False, "tailnet unreachable"),
            "olisar": (True, "https://olisar.tail1.ts.net"),
        })
        with self.assertRaises(HTTPException) as caught:
            await tunnel_api.rename(TunnelRenameIn(hostname="everest"), self.request(funnel))
        self.assertIn("tailnet unreachable", caught.exception.detail)
        self.assertEqual(funnel.started, ["everest", "olisar"])
        self.assertTrue(funnel.running)
        self.assertEqual(await runtime_config.tunnel_node(), "olisar")
        self.assertEqual(await runtime_config.public_base_url(), "https://olisar.tail1.ts.net")

    async def test_refuses_a_bad_name_or_a_funnel_that_is_off(self) -> None:
        funnel = FakeFunnel({})
        for bad in ("my bot", "-olisar", "olisar.tail1", "a" * 64, ""):
            with self.assertRaises(HTTPException, msg=bad) as caught:
                await tunnel_api.rename(TunnelRenameIn(hostname=bad), self.request(funnel))
            self.assertEqual(caught.exception.status_code, 400)
        funnel.running = False
        with self.assertRaises(HTTPException) as caught:
            await tunnel_api.rename(TunnelRenameIn(hostname="everest"), self.request(funnel))
        self.assertIn("on first", caught.exception.detail)
        self.assertEqual(funnel.started, [])
        self.assertEqual(await runtime_config.tunnel_node(), "olisar")


class NameTests(unittest.TestCase):
    def test_device_name(self) -> None:
        self.assertEqual(device_name(" Everest-2 "), "everest-2")
        self.assertEqual(device_name("a" * 63), "a" * 63)
        for bad in (None, "", "my bot", "olisar-", "-olisar", "olisar.net", "a" * 64, "olisär"):
            self.assertIsNone(device_name(bad), bad)

    def test_rename_note(self) -> None:
        self.assertEqual(rename_note("everest", "https://everest.tail1.ts.net"), "")
        self.assertIn("already called everest", rename_note("everest", "https://everest-3.tail1.ts.net"))
        # Not a suffix Tailscale adds: the name didn't take.
        kept = rename_note("everest", "https://everest-bot.tail1.ts.net")
        self.assertIn("kept the name everest-bot", kept)
        self.assertIn("Auto-generate from OS hostname", kept)


if __name__ == "__main__":
    unittest.main()
