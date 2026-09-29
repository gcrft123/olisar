"""The app pins a server's SSH host key the first time it connects, and holds it to that.

Run:  uv run python -m unittest tests.test_ssh_host_keys -v

Every connection to a bot's VM either sends it secrets (the .env on a deploy, the whole
database on a move) or trusts what comes back. So whoever answers on the VM's address has to
be the server the app first connected to. These run a real asyncssh server in-process and
swap its host key underneath the app, the way a rebuilt server, or someone in the middle,
would look.
"""

from __future__ import annotations

import contextlib
import functools
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import asyncssh

from olisar import runtime_config, runtime_keys
from olisar.db import engine
from olisar.runtime import remote


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return False  # no auth needed: this is about the server proving who it is


class HostKeyPinningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from scripts.init_db import create_schema

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        engine.pin_database(str(Path(self._tmp.name) / "olisar.db"))
        runtime_config.invalidate()
        await create_schema()
        await remote.public_key()  # the app's own keypair
        self.server = None
        self.port = 0

    async def asyncTearDown(self) -> None:
        await self.stop()
        for path in list(engine._engines):
            await engine.reset_engine(path)
        engine.pin_database(None)
        runtime_config.invalidate()
        runtime_keys.invalidate()

    async def serve(self, key: asyncssh.SSHKey) -> None:
        """Answer on the same address every time, with ``key`` as the host key."""
        await self.stop()
        self.server = await asyncssh.create_server(
            _Server, "127.0.0.1", self.port, server_host_keys=[key],
            process_factory=lambda process: process.exit(0),
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
            self.server = None

    async def connect(self):
        with mock.patch.object(remote.asyncssh, "connect",
                               functools.partial(asyncssh.connect, port=self.port)):
            return await remote._connect("127.0.0.1", "ubuntu")

    async def pinned(self) -> dict[str, str]:
        cfg = await remote._load()
        return remote.known_hosts(cfg.server_known_hosts)

    async def test_first_connection_pins_the_key_and_later_ones_must_match(self) -> None:
        first = asyncssh.generate_private_key("ssh-ed25519")
        await self.serve(first)
        (await self.connect()).close()
        want = " ".join(first.export_public_key().decode().split()[:2])
        self.assertEqual(await self.pinned(), {"127.0.0.1": want})

        (await self.connect()).close()  # same server, same key: fine

        await self.serve(asyncssh.generate_private_key("ssh-ed25519"))  # someone else answers
        with self.assertRaises(remote.HostKeyChanged) as refused:
            await self.connect()
        self.assertIn("127.0.0.1", str(refused.exception))
        self.assertEqual(await self.pinned(), {"127.0.0.1": want}, "a refused key replaced the pinned one")

    async def test_a_reset_forgets_the_key(self) -> None:
        """A rebuilt server has a new key. Resetting the bot's hosting is the way to start
        over with it."""
        from api.routers.instance import _RESET_CONFIG

        await self.serve(asyncssh.generate_private_key("ssh-ed25519"))
        (await self.connect()).close()
        rebuilt = asyncssh.generate_private_key("ssh-ed25519")
        await self.serve(rebuilt)
        with self.assertRaises(remote.HostKeyChanged):
            await self.connect()

        await runtime_config.save(**_RESET_CONFIG)
        (await self.connect()).close()
        self.assertEqual(await self.pinned(), {"127.0.0.1": " ".join(rebuilt.export_public_key().decode().split()[:2])})

    async def test_each_server_is_pinned_on_its_own(self) -> None:
        await runtime_config.save(server_known_hosts="203.0.113.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq0m2cHn1f3n3xk2c9k0l7wYx2Wq8Vt9kQzQ0n1c2dE\n")
        await self.serve(asyncssh.generate_private_key("ssh-ed25519"))
        (await self.connect()).close()
        self.assertEqual(set(await self.pinned()), {"203.0.113.5", "127.0.0.1"})


if __name__ == "__main__":
    unittest.main()
