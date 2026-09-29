"""An extension's slash command never replaces Olisar's own, or another extension's.

Run:  uv run python -m unittest tests.test_extension_command_names -v

Registering a command under a name already in the tree used to replace what was there, in
every server, whether or not the extension was enabled anywhere: an installed extension
declaring ``killswitch`` took over the panic button, and the next rebuild then dropped the
name altogether until a restart. Now the bot skips such a command, and installing or saving
one is refused.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.routers import extensions as ext_router
from api.schemas import ExtensionAuthoringIn
from bot.client import INITIAL_COGS
from bot.cogs import sdk_commands
from bot.cogs.killswitch import KillSwitch
from bot.cogs.sdk_commands import SdkCommands
from olisar.db.models import Base, ExtensionPackage
from olisar.extensions import bundle, command_names, user_registry


def _source(ext_id: str, *command_names_: str) -> str:
    cmds = ", ".join(
        f'{{ name: "{n}", description: "d", handler: async () => ({{ content: "hi" }}) }}'
        for n in command_names_
    )
    return textwrap.dedent(f"""
        defineExtension({{
          id: "{ext_id}", name: "{ext_id}", version: "1.0.0", permissions: [],
          commands: [{cmds}],
        }});
    """)


class BuiltinListTests(unittest.TestCase):
    def test_the_reserved_names_are_the_commands_the_cogs_register(self) -> None:
        registered: set[str] = set()
        for path in INITIAL_COGS:
            module = importlib.import_module(path)
            for value in vars(module).values():
                if isinstance(value, type) and issubclass(value, commands.Cog):
                    registered |= {c.name for c in getattr(value, "__cog_app_commands__", [])}
        self.assertEqual(registered, set(command_names.BUILTIN_COMMANDS))


class RebuildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
        self.killswitch = KillSwitch(self.bot)
        await self.bot.add_cog(self.killswitch)
        self.cog = SdkCommands(self.bot)

    async def rebuild(self, specs: list[tuple[str, dict]]) -> None:
        self.cog._load_command_specs = AsyncMock(return_value=specs)
        await self.cog.rebuild()

    async def test_an_extension_cannot_take_a_builtin_name(self) -> None:
        specs = [("evil", {"name": "killswitch", "description": "looks legit"}),
                 ("evil", {"name": "forget-me", "description": "x"}),
                 ("evil", {"name": "roll", "description": "fine"})]
        await self.rebuild(specs)
        self.assertIs(self.bot.tree.get_command("killswitch").binding, self.killswitch)
        self.assertIsNotNone(self.bot.tree.get_command("roll"))
        # A second rebuild used to remove the name it had taken over, built-in and all.
        await self.rebuild(specs)
        self.assertIs(self.bot.tree.get_command("killswitch").binding, self.killswitch)
        self.assertIsNotNone(self.bot.tree.get_command("roll"))
        await self.rebuild([])
        self.assertIs(self.bot.tree.get_command("killswitch").binding, self.killswitch)
        self.assertIsNone(self.bot.tree.get_command("roll"))

    async def test_the_first_extension_to_claim_a_name_keeps_it(self) -> None:
        await self.rebuild([("mine", {"name": "roll", "description": "mine"}),
                            ("theirs", {"name": "roll", "description": "theirs"})])
        self.assertEqual(self.bot.tree.get_command("roll").description, "mine")


class _DB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self._tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scope():
            async with Session() as session:
                yield session
                await session.commit()

        self.scope = scope
        for module in (ext_router, sdk_commands):
            p = patch.object(module, "session_scope", scope)
            p.start()
            self.addCleanup(p.stop)
        user_registry.invalidate()
        self.admin = SimpleNamespace(is_allowlisted=True, discord_user_id=1)
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    async def asyncTearDown(self) -> None:
        user_registry.invalidate()
        await self.engine.dispose()

    async def add(self, key: str, *names: str, kind: str = "user", origin: str = "local") -> None:
        async with self.scope() as s:
            s.add(ExtensionPackage(key=key, name=key, kind=kind, origin=origin,
                                   manifest={"id": key, "commands": [{"name": n} for n in names]}))


class InstallTests(_DB):
    async def install(self, ext_id: str, *names: str) -> dict:
        doc = bundle.build_bundle(ext_id=ext_id, name=ext_id, version="1.0.0", category="General",
                                  description="", source=_source(ext_id, *names), permissions=[])
        return await ext_router.install_bundle(json.loads(json.dumps(doc)), [], actor=1, origin="imported")

    async def test_installing_a_builtin_name_is_refused(self) -> None:
        with self.assertRaises(HTTPException) as refused:
            await self.install("evil", "killswitch")
        self.assertEqual(refused.exception.status_code, 409)
        self.assertIn("/killswitch", refused.exception.detail)
        async with self.scope() as s:
            self.assertIsNone(await s.get(ExtensionPackage, "evil"))

    async def test_installing_another_extensions_name_is_refused(self) -> None:
        await self.add("dice", "roll")
        with self.assertRaises(HTTPException) as refused:
            await self.install("copycat", "roll")
        self.assertIn("'dice'", refused.exception.detail)

    async def test_a_name_nobody_has_installs(self) -> None:
        await self.add("dice", "roll")
        out = await self.install("weather", "forecast")
        self.assertTrue(out["ok"])

    async def test_authoring_cannot_save_a_clash_either(self) -> None:
        await self.add("dice", "roll")
        with self.assertRaises(HTTPException):
            await ext_router.create_package(
                ExtensionAuthoringIn(source=_source("mine", "privacy")), self.request, self.admin)
        await ext_router.create_package(ExtensionAuthoringIn(source=_source("mine", "hello")), self.request, self.admin)
        # Saving it again with its own command is fine; taking another's isn't.
        await ext_router.update_package("mine", ExtensionAuthoringIn(source=_source("mine", "hello")),
                                        self.request, self.admin)
        with self.assertRaises(HTTPException):
            await ext_router.update_package("mine", ExtensionAuthoringIn(source=_source("mine", "hello", "roll")),
                                            self.request, self.admin)


class ClaimOrderTests(_DB):
    async def test_builtins_then_the_operators_own_then_installed_ones(self) -> None:
        await self.add("z_market", "roll", origin="marketplace")
        await self.add("a_local", "roll", origin="local")
        await self.add("m_builtin", "roll", kind="builtin")
        specs = await SdkCommands(SimpleNamespace())._load_command_specs()
        self.assertEqual([k for k, _ in specs], ["m_builtin", "a_local", "z_market"])


if __name__ == "__main__":
    unittest.main()
