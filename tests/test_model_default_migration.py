"""Moving existing guilds off the auto-updating model alias.

Run:  uv run python -m unittest tests.test_model_default_migration -v

Pinning the column default only helps guilds created after it. Every install that already
exists carries `gemini-flash-latest` in guild_config.default_model — seeded, not chosen —
and that stored value wins over the column default and over settings, so without this step
the pin is a no-op exactly where the incident happened.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from olisar.db import engine as engine_mod
from olisar.db.models import AppConfig, Base, Guild, GuildConfig
from olisar.gemini.models import AUTO, DEFAULT_CHAT_MODEL, LEGACY_DEFAULT_CHAT_MODEL
from scripts.init_db import migrate_model_default, migrate_to_auto


class MigrationTests(unittest.TestCase):
    """Exercises the real ``migrate_model_default`` against a throwaway SQLite file.

    Engines are keyed by ``current_db_path()``, so pointing that at a temp file gives the
    function its own database without touching the developer's. Testing the shipped
    function rather than a copy of its UPDATE is the whole point — a migration that runs
    on every startup is not somewhere to have a second, untested implementation.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "test.db")
        patcher = patch.object(engine_mod, "current_db_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        asyncio.run(self._create())
        self.addCleanup(lambda: asyncio.run(self._dispose()))

    async def _create(self) -> None:
        async with engine_mod.get_engine().begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Guild.__table__, GuildConfig.__table__],
            )

    async def _dispose(self) -> None:
        eng = engine_mod._engines.pop(self.path, None)
        engine_mod._sessionmakers.pop(self.path, None)
        if eng is not None:
            await eng.dispose()

    async def _seed(self, rows: dict[int, str]) -> None:
        async with engine_mod.session_scope() as s:
            for gid, model in rows.items():
                s.add(Guild(id=gid, name=f"g{gid}", icon="", active=True))
                s.add(GuildConfig(guild_id=gid, default_model=model))

    async def _migrate(self) -> int:
        return await migrate_model_default()

    async def _models(self) -> dict[int, str]:
        async with engine_mod.session_scope() as s:
            rows = (await s.execute(select(GuildConfig))).scalars().all()
            return {r.guild_id: r.default_model for r in rows}

    def test_guilds_on_the_old_default_move(self):
        async def go():
            await self._seed({1: LEGACY_DEFAULT_CHAT_MODEL, 2: LEGACY_DEFAULT_CHAT_MODEL})
            moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual(moved, 2)
        self.assertEqual(models, {1: DEFAULT_CHAT_MODEL, 2: DEFAULT_CHAT_MODEL})

    def test_a_deliberate_choice_is_left_alone(self):
        """Only the old default moves. Anything else is somebody's decision."""
        async def go():
            await self._seed({1: "gemini-2.5-flash", 2: "gemini-flash-lite-latest"})
            moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual(moved, 0)
        self.assertEqual(models, {1: "gemini-2.5-flash", 2: "gemini-flash-lite-latest"})

    def test_mixed_estate_moves_only_the_stragglers(self):
        async def go():
            await self._seed({1: LEGACY_DEFAULT_CHAT_MODEL, 2: "gemini-2.5-flash-lite"})
            moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual(moved, 1)
        self.assertEqual(models, {1: DEFAULT_CHAT_MODEL, 2: "gemini-2.5-flash-lite"})

    def test_a_retired_model_moves_to_its_replacement(self):
        """Google is turning it off, so even a deliberate choice of it moves."""
        from olisar.gemini.models import RETIRED

        async def go():
            await self._seed({1: "gemini-3.5-flash", 2: "gemini-3-flash-preview", 3: "gemini-2.5-flash"})
            moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual(moved, 2)
        self.assertEqual(models, {
            1: RETIRED["gemini-3.5-flash"], 2: RETIRED["gemini-3-flash-preview"], 3: "gemini-2.5-flash",
        })

    def test_running_it_twice_changes_nothing(self):
        """It runs on every startup."""
        async def go():
            await self._seed({1: LEGACY_DEFAULT_CHAT_MODEL})
            first = await self._migrate()
            second = await self._migrate()
            return first, second, await self._models()

        first, second, models = asyncio.run(go())
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(models, {1: DEFAULT_CHAT_MODEL})

    def test_a_fresh_guild_starts_on_automatic_with_no_migration(self):
        """Automatic replies as DEFAULT_CHAT_MODEL until routing is on."""
        async def go():
            async with engine_mod.session_scope() as s:
                s.add(Guild(id=9, name="new", icon="", active=True))
                s.add(GuildConfig(guild_id=9))
            return await self._models()

        self.assertEqual(asyncio.run(go()), {9: AUTO})


class MigrateToAutoTests(MigrationTests):
    """``migrate_to_auto``: the one-time move from the seeded default to Automatic."""

    async def _create(self) -> None:
        async with engine_mod.get_engine().begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Guild.__table__, GuildConfig.__table__, AppConfig.__table__],
            )

    async def _migrate(self) -> int:
        return await migrate_to_auto()

    # The inherited cases are about migrate_model_default; these replace them.
    test_guilds_on_the_old_default_move = None
    test_a_deliberate_choice_is_left_alone = None
    test_mixed_estate_moves_only_the_stragglers = None
    test_a_retired_model_moves_to_its_replacement = None
    test_running_it_twice_changes_nothing = None
    test_a_fresh_guild_starts_on_automatic_with_no_migration = None

    def test_the_seeded_default_moves_and_a_pinned_model_stays(self):
        async def go():
            await self._seed({1: DEFAULT_CHAT_MODEL, 2: "gemini-3.6-flash", 3: DEFAULT_CHAT_MODEL})
            moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual(moved, 2)
        self.assertEqual(models, {1: AUTO, 2: "gemini-3.6-flash", 3: AUTO})

    def test_it_runs_once_so_a_later_deliberate_choice_stays(self):
        """It runs on every start, but only acts on the first: someone who picks the
        default model by hand afterwards keeps it."""
        async def go():
            await self._seed({1: DEFAULT_CHAT_MODEL})
            first = await self._migrate()
            async with engine_mod.session_scope() as s:
                row = await s.get(GuildConfig, 1)
                row.default_model = DEFAULT_CHAT_MODEL
            second = await self._migrate()
            return first, second, await self._models()

        first, second, models = asyncio.run(go())
        self.assertEqual((first, second), (1, 0))
        self.assertEqual(models, {1: DEFAULT_CHAT_MODEL})

    def test_a_custom_default_model_keeps_servers_where_they_were(self):
        """Automatic starts on GEMINI_CHAT_MODEL, so where that's set to something else,
        moving the seeded rows would change what replies start on."""
        from olisar.config import settings

        async def go():
            await self._seed({1: DEFAULT_CHAT_MODEL})
            with patch.object(settings, "gemini_chat_model", "gemini-3.6-flash"):
                moved = await self._migrate()
            return moved, await self._models()

        moved, models = asyncio.run(go())
        self.assertEqual((moved, models), (0, {1: DEFAULT_CHAT_MODEL}))

    def test_without_an_app_config_row_it_makes_one_to_remember(self):
        async def go():
            await self._migrate()
            async with engine_mod.session_scope() as s:
                row = await s.get(AppConfig, 1)
                return row is not None and row.routing_migrated

        self.assertTrue(asyncio.run(go()))


if __name__ == "__main__":
    unittest.main()
