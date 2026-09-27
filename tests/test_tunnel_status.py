"""Whether turning remote access on can reuse a stored Tailscale key.

Run:  uv run python -m unittest tests.test_tunnel_status -v

Only shared hosting asks for a Tailscale auth key in setup, so a bot set up for this machine
alone has none. Settings → Remote access offered it a switch anyway, and turning it on failed
with "a Tailscale auth key is required" and nowhere to enter one. The status now says whether
a key is stored (never the key), so the console can ask for one instead.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from api.routers import tunnel as tunnel_api
from olisar import runtime_config
from olisar.db import engine


class TunnelStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        engine.pin_database(str(Path(self._tmp.name) / "olisar.db"))
        runtime_config.invalidate()
        await create_schema()
        # A developer's .env could carry a key; the stored one is what's under test.
        patcher = mock.patch.object(runtime_config.settings, "tunnel_token", "")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()

    async def _status(self) -> dict:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tunnel=None)))
        return await tunnel_api.status(request)

    async def test_a_bot_set_up_without_remote_access_has_no_key(self) -> None:
        out = await self._status()
        self.assertFalse(out["has_key"])
        self.assertEqual(out["node"], "")

    async def test_a_stored_key_is_reported_but_never_returned(self) -> None:
        await runtime_config.save(tunnel_token="tskey-auth-secret", tunnel_node="nebula")
        out = await self._status()
        self.assertTrue(out["has_key"])
        self.assertEqual(out["node"], "nebula")
        self.assertNotIn("tskey-auth-secret", repr(out))


if __name__ == "__main__":
    unittest.main()
