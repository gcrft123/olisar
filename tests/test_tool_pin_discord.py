"""The Discord half of the tool PIN, driven without Discord.

Run:  uv run python -m unittest tests.test_tool_pin_discord -v

Real ``_PinView``/``_PinModal`` objects and a real database; fake interactions standing in
for the gateway. That covers everything a live click exercises except Discord's transport:
what the three tries do, that the prompt is removed however it ends, that the indicator
stops while it waits, and that the reply parked on it is released with the right verdict.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot import replies
from bot import toolpin as discord_pin
from olisar import toolpin
from olisar.db.models import Base, GuildConfig

GUILD = 1234


class _Response:
    """discord.Interaction.response, as far as this flow uses it."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.modal = None
        self.deferred = False
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, **_) -> None:
        self._done = True
        self.messages.append(content)

    async def send_modal(self, modal) -> None:
        self._done = True
        self.modal = modal

    async def defer(self, **_) -> None:
        self._done = True
        self.deferred = True


class _User:
    """Any member. Deliberately carries no permissions: the PIN is the authorization,
    so an ordinary member who knows it can answer a prompt."""

    def __init__(self, uid: int = 1) -> None:
        self.id = uid


class _Interaction:
    def __init__(self, user: _User) -> None:
        self.user = user
        self.response = _Response()


class _Message:
    def __init__(self, content: str, view) -> None:
        self.content = content
        self.view = view
        self.edits: list[str] = []
        self.deleted = False

    async def edit(self, *, content: str, view=None, **_) -> None:
        self.content = content
        self.view = view
        self.edits.append(content)

    async def delete(self) -> None:
        self.deleted = True


class _Channel:
    def __init__(self) -> None:
        self.sent: list[_Message] = []

    async def send(self, content: str, view=None, **_) -> _Message:
        message = _Message(content, view)
        self.sent.append(message)
        return message


class PinFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / "test.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        @contextlib.asynccontextmanager
        async def scope():
            async with self.Session() as session:
                yield session
                await session.commit()

        self._scope = scope
        self._patch = patch.object(discord_pin, "session_scope", scope)
        self._patch.start()
        async with scope() as session:
            await toolpin.set_pin(session, "4821")
        self.channel = _Channel()

    async def asyncTearDown(self):
        self._patch.stop()
        await self.engine.dispose()
        self._tmp.cleanup()

    def _ask(self, *, timeout: float = 30.0):
        """Start a prompt and hand back the task the parked reply is waiting on."""
        return asyncio.create_task(
            discord_pin.request_pin(
                self.channel, tool="react", guild_id=GUILD, user_id=99, timeout=timeout,
            )
        )

    async def _posted(self, task):
        """Wait for the prompt message to be in the channel and return (message, view)."""
        for _ in range(50):
            if self.channel.sent:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.channel.sent, "no prompt was posted")
        message = self.channel.sent[0]
        return message, message.view

    async def _enter(self, view, pin: str, *, user: _User) -> _Interaction:
        """Click Enter PIN, then submit ``pin`` in the modal it opens."""
        click = _Interaction(user)
        await view.enter.callback(click)
        modal = click.response.modal
        self.assertIsNotNone(modal, "no modal opened")
        modal.pin._value = pin
        submit = _Interaction(user)
        await modal.on_submit(submit)
        return submit

    # ── the happy path ────────────────────────────────────────────────────────

    async def test_correct_pin_approves_and_removes_the_prompt(self):
        task = self._ask()
        message, view = await self._posted(task)
        self.assertIn("react", message.content)

        submit = await self._enter(view, "4821", user=_User())
        self.assertEqual(await task, toolpin.APPROVED)
        # Nothing said back: the reply Olisar is about to send is the answer.
        self.assertEqual(submit.response.messages, [])
        self.assertTrue(submit.response.deferred)
        # And nothing left behind: the prompt is gone, not rewritten into a notice.
        self.assertTrue(message.deleted)

    async def test_a_second_try_after_a_typo_still_works(self):
        task = self._ask()
        _, view = await self._posted(task)
        user = _User()
        first = await self._enter(view, "0000", user=user)
        self.assertEqual(first.response.messages, ["Wrong PIN (1/3)."])
        await self._enter(view, "4821", user=user)
        self.assertEqual(await task, toolpin.APPROVED)

    # ── the refusals ──────────────────────────────────────────────────────────

    async def test_three_wrong_pins_refuse_the_call(self):
        task = self._ask()
        message, view = await self._posted(task)
        user = _User()
        for expected in ("Wrong PIN (1/3).", "Wrong PIN (2/3)."):
            reply = await self._enter(view, "0000", user=user)
            self.assertEqual(reply.response.messages, [expected])
        last = await self._enter(view, "0000", user=user)
        self.assertEqual(await task, toolpin.WRONG)
        self.assertEqual(last.response.messages, ["Wrong PIN entered 3 times."])
        self.assertTrue(message.deleted)

    async def test_cancel_ends_it_immediately_and_says_nothing(self):
        task = self._ask()
        message, view = await self._posted(task)
        click = _Interaction(_User())
        await view.cancel.callback(click)
        self.assertEqual(await task, toolpin.REFUSED)
        self.assertEqual(click.response.messages, [])
        self.assertTrue(click.response.deferred)
        self.assertTrue(message.deleted)

    async def test_timeout_refuses_the_call(self):
        task = self._ask(timeout=0.2)
        message, _ = await self._posted(task)
        self.assertEqual(await task, toolpin.TIMEOUT)
        self.assertTrue(message.deleted)

    async def test_a_late_entry_cannot_reopen_a_closed_prompt(self):
        task = self._ask(timeout=0.2)
        _, view = await self._posted(task)
        self.assertEqual(await task, toolpin.TIMEOUT)
        click = _Interaction(_User())
        await view.enter.callback(click)
        self.assertEqual(click.response.messages, ["Error: Already answered."])
        self.assertIsNone(click.response.modal)  # no form to type into

        cancel = _Interaction(_User())
        await view.cancel.callback(cancel)
        self.assertEqual(cancel.response.messages, ["Error: Already answered."])

    # ── who may answer ────────────────────────────────────────────────────────

    async def test_knowing_the_pin_is_the_only_credential(self):
        """No permission check stands in front of the form. Gating the button on Manage
        Server would mean a member the operator handed the PIN to couldn't use it."""
        task = self._ask()
        _, view = await self._posted(task)
        click = _Interaction(_User(uid=555))  # an ordinary member, no permissions at all
        await view.enter.callback(click)
        self.assertIsNotNone(click.response.modal)
        self.assertEqual(click.response.messages, [])

        await self._enter(view, "4821", user=_User(uid=555))
        self.assertEqual(await task, toolpin.APPROVED)

    # ── the typing indicator ──────────────────────────────────────────────────

    async def test_the_indicator_is_reachable_from_inside_a_reply(self):
        """The handle travels on a ContextVar set by `composing`, which is entered in the
        cog and read three call layers down. If that didn't survive the async context
        manager the pause below would be a silent no-op."""
        async with replies.composing(self.channel):
            state = replies._composing.get()
            self.assertIsNotNone(state)
            with replies.typing_paused():
                self.assertTrue(state._paused)
            self.assertFalse(state._paused)
        self.assertIsNone(replies._composing.get())

    async def test_olisar_stops_typing_while_the_prompt_is_up(self):
        """A prompt can sit there for minutes. Showing "typing…" through it would claim
        Olisar is writing when it is waiting on a person."""
        state = replies._Composing(self.channel)
        token = replies._composing.set(state)
        try:
            task = self._ask(timeout=0.2)
            await self._posted(task)
            self.assertTrue(state._paused, "the indicator kept running under the prompt")
            self.assertEqual(await task, toolpin.TIMEOUT)
            self.assertFalse(state._paused, "the indicator never came back")
        finally:
            replies._composing.reset(token)

    # ── the operator's wording ────────────────────────────────────────────────

    async def test_the_prompt_uses_this_server_s_wording(self):
        async with self._scope() as session:
            session.add(GuildConfig(
                guild_id=GUILD,
                command_messages={"tool_pin_prompt": "key please for {tool} ({seconds}s)"},
            ))
        task = self._ask()
        message, view = await self._posted(task)
        self.assertEqual(message.content, "key please for react (30s)")
        await self._enter(view, "4821", user=_User())
        await task

    async def test_nowhere_to_post_is_not_an_approval(self):
        outcome = await discord_pin.request_pin(
            None, tool="react", guild_id=GUILD, user_id=99, timeout=5,
        )
        self.assertEqual(outcome, toolpin.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
