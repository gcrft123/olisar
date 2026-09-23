"""Coverage for the tool PIN — the 4-digit confirmation a gated tool call has to pass.

Run:  uv run python -m unittest tests.test_tool_pin -v

What matters here is what happens when the answer is *no*, so most of these are about
refusal rather than approval:

  * a server gates Olisar changing its own settings until an admin says otherwise, and
    that choice isn't one the gated tools can reach
  * every way of not confirming (silence, wrong digits, an outright no, nowhere to ask)
    stops the call and hands the model a denial it can read
  * one refusal holds for the rest of the reply, so a retry can't re-prompt the channel,
    and one approval covers the action, so a two-part edit asks once
  * a gated tool with no PIN set fails closed
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar import self_settings, toolpin
from olisar.db.models import Base, GuildConfig
from olisar.guild_setup import ensure_guild_defaults
from olisar.tools import ToolContext, execute_tool

GUILD = 1


class NormalizeTests(unittest.TestCase):
    def test_four_digits_pass(self):
        self.assertEqual(toolpin.normalize("1234"), "1234")
        self.assertEqual(toolpin.normalize("0042"), "0042")  # leading zeros survive

    def test_human_spacing_is_accepted(self):
        for raw in ("1 2 3 4", " 1234 ", "12-34", "12.34"):
            with self.subTest(raw=raw):
                self.assertEqual(toolpin.normalize(raw), "1234")

    def test_anything_else_is_rejected(self):
        for raw in ("123", "12345", "abcd", "12a4", "", None):
            with self.subTest(raw=raw):
                self.assertEqual(toolpin.normalize(raw), "")


class HashTests(unittest.TestCase):
    def test_round_trip(self):
        encoded = toolpin.hash_pin("1234")
        self.assertTrue(toolpin.check_pin("1234", encoded))
        self.assertFalse(toolpin.check_pin("1235", encoded))

    def test_salted_per_pin(self):
        """Two installs with the same PIN must not share a hash."""
        self.assertNotEqual(toolpin.hash_pin("1234"), toolpin.hash_pin("1234"))

    def test_garbage_reads_as_wrong_not_crash(self):
        for encoded in ("", "not-a-hash", "scrypt$x$y$z", "md5$1$1$1$a$b"):
            with self.subTest(encoded=encoded):
                self.assertFalse(toolpin.check_pin("1234", encoded))


class GateConfigTests(unittest.TestCase):
    def test_the_override_is_empty_by_default(self):
        with patch.object(toolpin.settings, "pin_gated_tools", ""):
            self.assertEqual(toolpin.gated_tools(), frozenset())

    def test_the_override_parses_a_list(self):
        with patch.object(toolpin.settings, "pin_gated_tools", "react, send_dm"):
            self.assertEqual(toolpin.gated_tools(), frozenset({"react", "send_dm"}))

    def test_self_edit_covers_every_settings_write(self):
        """A write tool added to self_settings without being listed here would run
        unconfirmed on every server that thinks it has self-edit behind the PIN."""
        writes = {d.name for d in self_settings.WRITE_DECLARATIONS}
        self.assertEqual(toolpin.ACTIONS["self_edit"], writes)
        self.assertNotIn("open_settings", toolpin.ACTIONS["self_edit"])

    def test_the_gate_is_not_a_setting_the_gated_tools_can_change(self):
        keys = {**self_settings._PERSONA, **self_settings._BEHAVIOR}
        self.assertNotIn("pin_actions", {f.attr for f in keys.values()})
        self.assertNotIn("pin_actions", keys)

    def test_new_rows_start_with_the_default(self):
        """The column default and toolpin's own (used when there's no row) must agree."""
        default = GuildConfig.__table__.c.pin_actions.default.arg(None)
        self.assertEqual(default, list(toolpin.DEFAULT_ACTIONS))


class DenialNoteTests(unittest.TestCase):
    def test_every_outcome_names_the_tool_and_forbids_a_retry(self):
        for outcome in (toolpin.TIMEOUT, toolpin.WRONG, toolpin.REFUSED, toolpin.UNAVAILABLE):
            with self.subTest(outcome=outcome):
                note = toolpin.denial_note("send_dm", outcome)
                self.assertTrue(note.startswith("DENIED:"))
                self.assertIn("send_dm", note)
                self.assertIn("Do not call send_dm again", note)

    def test_the_denial_is_never_mistaken_for_a_result(self):
        """The pipeline keeps 'useful' tool output for its last-resort fallback; a refusal
        is an instruction to the model, and must not be counted as something it gathered."""
        from olisar.pipeline import _useful

        self.assertFalse(_useful(toolpin.denial_note("react", toolpin.TIMEOUT)))


class _DbCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self._tmp.cleanup()

    @contextlib.asynccontextmanager
    async def scope(self):
        async with self.Session() as session:
            yield session
            await session.commit()


class StoredPinTests(_DbCase):
    async def test_unset_verifies_nothing(self):
        """An unset PIN confirms nothing — it does not confirm everything."""
        async with self.scope() as session:
            self.assertFalse((await toolpin.get_state(session)).is_set)
            self.assertFalse(await toolpin.verify(session, "1234"))
            self.assertFalse(await toolpin.verify(session, ""))

    async def test_set_then_verify(self):
        async with self.scope() as session:
            await toolpin.set_pin(session, "4821", actor=1)
        async with self.scope() as session:
            state = await toolpin.get_state(session)
            self.assertTrue(state.is_set)
            self.assertEqual(state.timeout_sec, toolpin.DEFAULT_TIMEOUT_SEC)
            self.assertTrue(await toolpin.verify(session, "4821"))
            self.assertTrue(await toolpin.verify(session, "48 21"))
            self.assertFalse(await toolpin.verify(session, "4822"))

    async def test_changing_replaces_the_old_one(self):
        async with self.scope() as session:
            await toolpin.set_pin(session, "1111")
        async with self.scope() as session:
            await toolpin.set_pin(session, "2222")
        async with self.scope() as session:
            self.assertFalse(await toolpin.verify(session, "1111"))
            self.assertTrue(await toolpin.verify(session, "2222"))

    async def test_clearing_leaves_nothing_to_verify(self):
        async with self.scope() as session:
            await toolpin.set_pin(session, "1111")
        async with self.scope() as session:
            await toolpin.clear_pin(session)
        async with self.scope() as session:
            self.assertFalse((await toolpin.get_state(session)).is_set)
            self.assertFalse(await toolpin.verify(session, "1111"))

    async def test_a_bad_pin_is_refused_before_it_is_stored(self):
        async with self.scope() as session:
            with self.assertRaises(ValueError):
                await toolpin.set_pin(session, "12")
            self.assertFalse((await toolpin.get_state(session)).is_set)

    async def test_timeout_is_clamped(self):
        async with self.scope() as session:
            self.assertEqual(await toolpin.set_timeout(session, 5), toolpin.MIN_TIMEOUT_SEC)
            self.assertEqual(await toolpin.set_timeout(session, 99999), toolpin.MAX_TIMEOUT_SEC)
            self.assertEqual(await toolpin.set_timeout(session, 60), 60)


class ServerGateTests(_DbCase):
    """What each server chose, read at the moment of the call."""

    async def _set(self, actions: list[str] | None) -> None:
        async with self.scope() as session:
            await ensure_guild_defaults(session, GUILD)
            if actions is not None:
                (await session.get(GuildConfig, GUILD)).pin_actions = actions

    async def _gate(self, tool: str) -> str:
        async with self.Session() as session:
            with patch.object(toolpin.settings, "pin_gated_tools", ""):
                return await toolpin.gate(session, GUILD, tool)

    async def test_a_new_server_gates_self_edit(self):
        await self._set(None)
        self.assertEqual(await self._gate("change_setting"), "self_edit")
        self.assertEqual(await self._gate("settings_action"), "self_edit")

    async def test_reading_settings_is_never_gated(self):
        await self._set(None)
        self.assertEqual(await self._gate("open_settings"), "")

    async def test_a_server_can_turn_it_off(self):
        await self._set([])
        self.assertEqual(await self._gate("change_setting"), "")

    async def test_no_config_row_falls_back_to_the_default(self):
        self.assertEqual(await self._gate("change_setting"), "self_edit")

    async def test_other_tools_never_touch_the_database(self):
        """The gate runs on every tool call, so it may only read config for tools an
        action covers. A None session would raise if it tried."""
        with patch.object(toolpin.settings, "pin_gated_tools", ""):
            self.assertEqual(await toolpin.gate(None, GUILD, "recall_memory"), "")
        with patch.object(toolpin.settings, "pin_gated_tools", "react"):
            self.assertEqual(await toolpin.gate(None, GUILD, "react"), "react")

    async def test_the_override_still_gates_with_the_action_off(self):
        await self._set([])
        async with self.Session() as session:
            with patch.object(toolpin.settings, "pin_gated_tools", "change_setting"):
                self.assertEqual(
                    await toolpin.gate(session, GUILD, "change_setting"), "change_setting"
                )


class ConfigApiTests(_DbCase):
    """The Access page's switches, through the real router against a real session."""

    async def _put(self, body: dict):
        from api.routers.admin import put_config
        from api.schemas import ConfigIn

        gctx = MagicMock()
        gctx.guild_id = GUILD
        gctx.admin.discord_user_id = 42
        with patch("api.routers.admin.session_scope", self.scope):
            return await put_config(ConfigIn(**body), gctx)

    async def _stored(self) -> list:
        async with self.scope() as session:
            return (await session.get(GuildConfig, GUILD)).pin_actions

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.scope() as session:
            await ensure_guild_defaults(session, GUILD)

    async def test_turning_it_off_and_on(self):
        await self._put({"pin_actions": []})
        self.assertEqual(await self._stored(), [])
        await self._put({"pin_actions": ["self_edit", "self_edit"]})
        self.assertEqual(await self._stored(), ["self_edit"])

    async def test_an_unknown_action_is_refused_not_stored(self):
        with self.assertRaises(HTTPException) as caught:
            await self._put({"pin_actions": ["self_edit", "launch_missiles"]})
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(await self._stored(), ["self_edit"])

    async def test_the_change_is_audited_on_its_own(self):
        from sqlalchemy import select

        from olisar.db.models import AuditLog

        await self._put({"pin_actions": []})
        async with self.scope() as session:
            rows = (await session.scalars(select(AuditLog))).all()
        self.assertEqual([r.action for r in rows], ["set_pin_actions"])
        self.assertEqual(rows[0].before, {"pin_actions": ["self_edit"]})
        self.assertEqual(rows[0].after, {"pin_actions": []})


class _Actions:
    """Stands in for the Discord side: records the prompts and answers with a script."""

    def __init__(self, *outcomes: str) -> None:
        self.outcomes = list(outcomes)
        self.asked: list[str] = []

    async def request_pin(self, *, tool: str, guild_id: int, user_id: int, timeout: float) -> str:
        self.asked.append(tool)
        return self.outcomes.pop(0) if self.outcomes else toolpin.TIMEOUT


class GatedExecutionTests(_DbCase):
    """execute_tool is the chokepoint: a gated call goes through the prompt or not at all."""

    async def _run(self, tool: str, actions, *, pin: str | None = "1234", ctx=None):
        if pin is not None:
            async with self.scope() as session:
                await toolpin.set_pin(session, pin)
        async with self.Session() as session:
            ctx = ctx or ToolContext(
                session=session, cfg_guild=1, channel_id=2, user_id=3, display_name="ada",
                actions=actions,
            )
            ctx.session = session
            with patch.object(toolpin.settings, "pin_gated_tools", tool), patch(
                "olisar.tools._dispatch", new=AsyncMock(return_value="tool ran")
            ) as dispatch:
                result = await execute_tool(tool, {}, ctx)
            return result, dispatch, ctx

    async def test_approved_runs_the_tool(self):
        actions = _Actions(toolpin.APPROVED)
        result, dispatch, _ = await self._run("react", actions)
        self.assertEqual(result, "tool ran")
        self.assertEqual(actions.asked, ["react"])
        dispatch.assert_awaited_once()

    async def test_timeout_denies_it(self):
        actions = _Actions(toolpin.TIMEOUT)
        result, dispatch, _ = await self._run("react", actions)
        self.assertIn("DENIED", result)
        self.assertIn("expired", result)
        dispatch.assert_not_awaited()

    async def test_wrong_pin_denies_it(self):
        actions = _Actions(toolpin.WRONG)
        result, dispatch, _ = await self._run("react", actions)
        self.assertIn("wrong", result)
        dispatch.assert_not_awaited()

    async def test_refusal_denies_it(self):
        actions = _Actions(toolpin.REFUSED)
        result, dispatch, _ = await self._run("react", actions)
        self.assertIn("admin refused", result)
        dispatch.assert_not_awaited()

    async def test_no_pin_set_fails_closed(self):
        """Gating a tool without a PIN means it can't run — not that it runs unchecked."""
        actions = _Actions(toolpin.APPROVED)
        result, dispatch, _ = await self._run("react", actions, pin=None)
        self.assertIn("DENIED", result)
        self.assertEqual(actions.asked, [])  # never even asked
        dispatch.assert_not_awaited()

    async def test_nowhere_to_ask_fails_closed(self):
        """The console's test chat has no Discord surface, so a gated tool is refused."""
        result, dispatch, _ = await self._run("react", None)
        self.assertIn("DENIED", result)
        dispatch.assert_not_awaited()

    async def test_ungated_tools_are_untouched(self):
        actions = _Actions(toolpin.APPROVED)
        async with self.scope() as session:
            await toolpin.set_pin(session, "1234")
        async with self.Session() as session:
            ctx = ToolContext(
                session=session, cfg_guild=1, channel_id=2, user_id=3, display_name="ada",
                actions=actions,
            )
            with patch.object(toolpin.settings, "pin_gated_tools", "send_dm"), patch(
                "olisar.tools._dispatch", new=AsyncMock(return_value="tool ran")
            ):
                self.assertEqual(await execute_tool("react", {}, ctx), "tool ran")
        self.assertEqual(actions.asked, [])

    async def test_a_refusal_holds_for_the_rest_of_the_reply(self):
        """The model retrying the same call must not put a second prompt in the channel."""
        actions = _Actions(toolpin.REFUSED, toolpin.APPROVED)
        async with self.scope() as session:
            await toolpin.set_pin(session, "1234")
        async with self.Session() as session:
            ctx = ToolContext(
                session=session, cfg_guild=1, channel_id=2, user_id=3, display_name="ada",
                actions=actions,
            )
            with patch.object(toolpin.settings, "pin_gated_tools", "react"), patch(
                "olisar.tools._dispatch", new=AsyncMock(return_value="tool ran")
            ) as dispatch:
                first = await execute_tool("react", {}, ctx)
                second = await execute_tool("react", {}, ctx)
        self.assertEqual(first, second)
        self.assertEqual(actions.asked, ["react"])  # asked once, not twice
        dispatch.assert_not_awaited()


class SelfEditTests(_DbCase):
    """Olisar changing its own settings, on a server that left the default alone."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.scope() as session:
            await ensure_guild_defaults(session, GUILD)
            await toolpin.set_pin(session, "1234")

    async def _calls(self, actions, *tools: str):
        async with self.Session() as session:
            ctx = ToolContext(
                session=session, cfg_guild=GUILD, channel_id=2, user_id=3,
                display_name="ada", actions=actions,
            )
            with patch.object(toolpin.settings, "pin_gated_tools", ""), patch(
                "olisar.tools._dispatch", new=AsyncMock(return_value="tool ran")
            ) as dispatch:
                results = [await execute_tool(t, {}, ctx) for t in tools]
        return results, dispatch

    async def test_one_confirmation_covers_the_rest_of_the_reply(self):
        """"Rename yourself and rewrite your bio" is two calls and one decision."""
        actions = _Actions(toolpin.APPROVED)
        results, dispatch = await self._calls(
            actions, "change_setting", "change_setting", "settings_action"
        )
        self.assertEqual(results, ["tool ran"] * 3)
        self.assertEqual(actions.asked, ["change_setting"])
        self.assertEqual(dispatch.await_count, 3)

    async def test_a_refusal_covers_both_tools(self):
        actions = _Actions(toolpin.REFUSED, toolpin.APPROVED)
        results, dispatch = await self._calls(actions, "change_setting", "settings_action")
        self.assertTrue(all("DENIED" in r for r in results))
        self.assertIn("settings_action", results[1])  # the note names the call it refuses
        self.assertEqual(actions.asked, ["change_setting"])
        dispatch.assert_not_awaited()

    async def test_reading_needs_no_pin(self):
        actions = _Actions()
        results, _ = await self._calls(actions, "open_settings")
        self.assertEqual(results, ["tool ran"])
        self.assertEqual(actions.asked, [])

    async def test_turned_off_it_runs_unasked(self):
        async with self.scope() as session:
            (await session.get(GuildConfig, GUILD)).pin_actions = []
        actions = _Actions()
        results, _ = await self._calls(actions, "change_setting")
        self.assertEqual(results, ["tool ran"])
        self.assertEqual(actions.asked, [])


if __name__ == "__main__":
    unittest.main()
