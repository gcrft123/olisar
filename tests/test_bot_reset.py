"""Coverage for resetting a bot's configuration, and setting it up again afterwards.

Run:  uv run python -m unittest tests.test_bot_reset -v

A reset used to keep ``hosting_mode``, so a bot that had been on a server stayed a server bot
with no server: the console opened the connect screen instead of setup, and finishing local
setup left the bot unstarted behind a server panel saying no server was configured. These pin
that a reset starts the bot over as a new, local one, and that local setup always ends local.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from api.routers import instance, setup
from api.schemas import SetupSaveIn
from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.db.engine import session_scope
from olisar.db.models import AppConfig, AppSecret


class _Tunnel:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def _request(**state) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state)))


class ResetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine.pin_database(os.path.join(tmp.name, "bot.db"))
        runtime_config.invalidate()
        runtime_keys.invalidate()
        await create_schema()
        # state.json lives in the real data dir; nothing here should write it.
        patcher = mock.patch("olisar.runtime.state.write")
        self.state_write = patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def _server_bot(self) -> None:
        await runtime_config.save(
            discord_token="tok", discord_client_id="111", discord_client_secret="sec",
            target_guild_id=42, hosting_mode="server", server_host="203.0.113.9",
            server_ssh_user="opc", server_ssh_pubkey="ssh-ed25519 AAAA", server_ssh_privkey="PRIV",
            server_app_dir="olisar-beta", server_synced_version="2.0.0",
            tunnel_enabled=True, tunnel_hostname="olisar.tail.ts.net", tunnel_token="tskey",
            configured=True,
        )
        async with session_scope() as session:
            session.add(AppSecret(id=1, gemini_api_key="gem"))

    async def _config(self) -> AppConfig:
        async with session_scope() as session:
            return await session.get(AppConfig, 1)

    async def test_a_reset_server_bot_is_a_new_local_bot(self) -> None:
        await self._server_bot()
        tunnel = _Tunnel()
        r = await instance.reset(_request(tunnel=tunnel))

        self.assertEqual(r["hosting_mode"], "server")  # what it was
        self.assertEqual(await runtime_config.hosting_mode(), "local")
        self.assertFalse(await runtime_config.is_configured())
        cfg = await self._config()
        self.assertEqual(cfg.server_host, "")
        self.assertEqual(cfg.server_ssh_user, "ubuntu")
        self.assertEqual(cfg.server_synced_version, "")
        self.assertEqual(cfg.discord_token, "")
        self.assertFalse(cfg.tunnel_enabled)
        # Its identity stays, so it can still connect back to the VM it ran on.
        self.assertEqual(cfg.server_ssh_pubkey, "ssh-ed25519 AAAA")
        self.assertEqual(cfg.server_ssh_privkey, "PRIV")
        async with session_scope() as session:
            self.assertEqual((await session.get(AppSecret, 1)).gemini_api_key, "")

    async def test_a_reset_stops_the_funnel(self) -> None:
        await self._server_bot()
        tunnel = _Tunnel()
        await instance.reset(_request(tunnel=tunnel))
        self.assertTrue(tunnel.stopped)
        self.state_write.assert_called_with(public_url="")

    async def test_local_setup_after_a_reset_runs_the_bot_here(self) -> None:
        await self._server_bot()
        await instance.reset(_request(tunnel=_Tunnel()))
        # A reset from before resets cleared hosting: still marked server, with no server.
        await runtime_config.save(hosting_mode="server")

        body = SetupSaveIn(discord_token="tok2", discord_client_id="222", discord_client_secret="sec2")
        with mock.patch.object(runtime_config, "oauth_redirect_uri", mock.AsyncMock(return_value="x")):
            await setup.save(body, _request())

        self.assertEqual(await runtime_config.hosting_mode(), "local")
        self.assertTrue(await runtime_config.is_configured())
        self.assertEqual((await self._config()).server_host, "")


if __name__ == "__main__":
    unittest.main()
