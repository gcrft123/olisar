"""The install-wide settings are the operator's.

Run:  uv run python -m unittest tests.test_install_wide_settings -v

There is one tool PIN for the whole install, and one update channel, which moves every
server the bot is in (and a server-hosted VM) onto beta. Both took any console session, so
Manage Server on one server was enough to remove the PIN every other server relies on, or to
put the whole install on beta. Same line the API keys draw (tests/test_key_checks.py).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.auth.deps import require_operator
from api.routers import settings as settings_router
from api.routers.settings import require_operator_or_local


def _deps(path: str, method: str) -> list:
    for route in settings_router.router.routes:
        if getattr(route, "path", "") == path and method in route.methods:
            return [d.call for d in route.dependant.dependencies]
    raise AssertionError(f"no route {method} {path}")


class RouteGateTests(unittest.TestCase):
    def test_changing_or_removing_the_pin_is_operator_only(self) -> None:
        self.assertIn(require_operator, _deps("/api/settings/pin", "PUT"))
        self.assertIn(require_operator, _deps("/api/settings/pin", "DELETE"))

    def test_reading_whether_a_pin_is_set_is_not(self) -> None:
        """The Access page warns every admin when their server needs a PIN that isn't set."""
        self.assertNotIn(require_operator, _deps("/api/settings/pin", "GET"))

    def test_the_update_channel_is_operator_or_local(self) -> None:
        self.assertIn(require_operator_or_local, _deps("/api/settings/updates/channel", "PUT"))


class OperatorOrLocalTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, *, local: bool, admin):
        with patch.object(settings_router, "is_local_request", return_value=local), \
             patch.object(settings_router, "require_admin", AsyncMock(return_value=admin)):
            return await require_operator_or_local(SimpleNamespace(), olisar_session="tok")

    async def test_someone_at_the_machine_is_let_through(self) -> None:
        """The desktop app's own window is loopback, signed in or not."""
        self.assertIsNone(await self._call(local=True, admin=None))

    async def test_a_remote_manage_server_admin_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            await self._call(local=False, admin=SimpleNamespace(is_allowlisted=False))
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_the_remote_operator_is_let_through(self) -> None:
        operator = SimpleNamespace(is_allowlisted=True)
        self.assertIs(await self._call(local=False, admin=operator), operator)


if __name__ == "__main__":
    unittest.main()
