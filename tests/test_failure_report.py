"""Coverage for reporting a blank reply from the message it happened on.

Run:  uv run python -m unittest tests.test_failure_report -v

When Olisar's reply comes back as the blank fallback it now carries a "Report this"
button, and the console it links to opens the Feedback pane already filled in. Four
invariants hold that together, and each of them is a way the feature could quietly become
a leak or a lie:

  * a blank is *marked* as one by the pipeline, so the cogs don't have to guess by
    comparing strings — and a rate limit is not marked, because waiting fixes that
  * the prompt never travels in the URL; the link carries an opaque token, because the
    button sits in a channel where everyone can read it
  * the parked report is claimable only by the account it happened to — not by another
    member, not by an admin, not after it expires
  * no public console, no link, no row: the button is never offered where it would point
    at the operator's loopback address
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from olisar.db.models import Base, FailureReport, utcnow
from olisar.failures import PER_USER_CAP, claim, open_report
from olisar.gemini.rate_limiter import RateLimitExceeded
from olisar.pipeline import Reply

GUILD = 5001
USER = 6001
OTHER_USER = 6002
CHANNEL = 9001


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

    @contextlib.contextmanager
    def reachable(self, *, remote: bool = True, base: str = "https://olisar.example.ts.net"):
        """Pretend remote access is (or isn't) configured, without a tunnel or a DB row."""
        with patch("olisar.failures.runtime_config") as cfg:
            cfg.remote_access_configured = AsyncMock(return_value=remote)
            cfg.public_base_url = AsyncMock(return_value=base)
            yield

    async def park(self, *, user_id: int = USER, prompt: str = "who runs this server") -> str:
        with self.reachable():
            async with self.scope() as session:
                return await open_report(
                    session, user_id=user_id, guild_id=GUILD, channel_id=CHANNEL,
                    trigger="mention", prompt=prompt,
                )

    async def rows(self) -> int:
        async with self.scope() as session:
            return int(await session.scalar(select(func.count()).select_from(FailureReport)) or 0)


class MarkingTests(unittest.TestCase):
    """What the pipeline hands back. The cogs act on ``blanked``, so it has to be the
    pipeline's own answer rather than a string comparison made downstream."""

    def test_a_reply_is_not_blank_by_default(self):
        self.assertFalse(Reply("here you go").blanked)

    def test_str_is_the_text(self):
        """Existing callers treat a reply as its text; keep that true."""
        self.assertEqual(str(Reply("hello")), "hello")

    def test_generate_reply_marks_the_failure_path(self):
        from olisar import pipeline

        with patch.object(pipeline, "_run_tool_loop", AsyncMock(side_effect=RuntimeError("boom"))):
            reply = self._run(pipeline)
        self.assertTrue(reply.blanked)
        self.assertEqual(reply.text, "blank")

    def test_a_rate_limit_is_not_a_blank(self):
        """It says so in the reply, waiting fixes it, and there is nothing to diagnose —
        offering to file a bug report for it would be noise for the team and the member."""
        from olisar import pipeline

        exhausted = RateLimitExceeded("gemini-3.5-flash", "daily")
        with patch.object(pipeline, "_run_tool_loop", AsyncMock(side_effect=exhausted)):
            reply = self._run(pipeline)
        self.assertFalse(reply.blanked)
        self.assertEqual(reply.text, "rate limited")

    def test_the_loop_falling_through_to_the_fallback_is_a_blank(self):
        from olisar import pipeline

        with patch.object(pipeline, "_run_tool_loop", AsyncMock(return_value="blank")):
            reply = self._run(pipeline)
        self.assertTrue(reply.blanked)

    def test_a_real_answer_is_not_a_blank(self):
        from olisar import pipeline

        with patch.object(pipeline, "_run_tool_loop", AsyncMock(return_value="the wifi is guest")):
            reply = self._run(pipeline)
        self.assertFalse(reply.blanked)

    def _run(self, pipeline):
        """Drive generate_reply past everything that needs a database or a model, leaving
        only the branch that decides ``blanked``."""
        import asyncio

        session = AsyncMock()
        session.get = AsyncMock(return_value=None)  # no persona, no guild config -> defaults
        with (
            patch.object(pipeline, "render_message", side_effect=lambda _c, key, **k: {
                "rate_limit": "rate limited", "blank_fallback": "blank",
            }[key]),
            patch.object(pipeline, "build_contents", AsyncMock(return_value=([], []))),
            patch.object(pipeline, "people_directory", AsyncMock(return_value="")),
            patch.object(pipeline, "recall", AsyncMock(return_value="")),
            patch.object(pipeline, "gather_enabled", AsyncMock(side_effect=RuntimeError("skip"))),
        ):
            return asyncio.run(pipeline.generate_reply(
                session,
                guild_id=GUILD, channel_id=CHANNEL, current_message_id=1, bot_user_id=2,
                user_id=USER, display_name="ada", user_text="who runs this server",
            ))


class LinkTests(_DbCase):
    async def test_the_link_carries_a_token_and_nothing_else(self):
        """The button is on a message in a channel: everyone who can read the message can
        read the URL. A prompt in the query string would publish what the member typed —
        and in a DM, publish it to a channel they never posted in."""
        url = await self.park(prompt="my landlord's address is 12 Rowan Way")
        self.assertNotIn("Rowan", url)
        self.assertNotIn("landlord", url)
        self.assertTrue(url.startswith("https://olisar.example.ts.net/?report="))

        async with self.scope() as session:
            row = await session.scalar(select(FailureReport))
        self.assertEqual(url.split("report=")[1], row.token)
        self.assertEqual(row.prompt, "my landlord's address is 12 Rowan Way")

    async def test_no_public_console_means_no_button_and_no_row(self):
        """Until remote access is configured the console is loopback-only, so the link
        would resolve to 127.0.0.1 on the operator's machine — a dead end for everyone in
        the channel. Nothing is offered and nothing is stored."""
        with self.reachable(remote=False):
            async with self.scope() as session:
                url = await open_report(
                    session, user_id=USER, guild_id=GUILD, channel_id=CHANNEL,
                    trigger="dm", prompt="hello",
                )
        self.assertEqual(url, "")
        self.assertEqual(await self.rows(), 0)

    async def test_a_failure_to_park_still_lets_the_apology_through(self):
        """The reply already failed. Failing to park the report on top of that must not
        also cost the user their message."""
        with self.reachable():
            async with self.scope() as session:
                with patch.object(session, "flush", AsyncMock(side_effect=RuntimeError("db gone"))):
                    url = await open_report(
                        session, user_id=USER, guild_id=GUILD, channel_id=CHANNEL,
                        trigger="ask", prompt="hello",
                    )
        self.assertEqual(url, "")


class ClaimTests(_DbCase):
    async def test_the_person_it_happened_to_can_claim_it(self):
        url = await self.park()
        token = url.split("report=")[1]
        async with self.scope() as session:
            row = await claim(session, token, user_id=USER)
        self.assertIsNotNone(row)
        self.assertEqual(row.prompt, "who runs this server")
        self.assertEqual(row.trigger, "mention")

    async def test_nobody_else_can(self):
        """Including an admin. The link is readable by everyone who can see the message,
        and a DM prompt is not an admin's to read back out of the console."""
        url = await self.park()
        token = url.split("report=")[1]
        async with self.scope() as session:
            self.assertIsNone(await claim(session, token, user_id=OTHER_USER))

    async def test_an_unknown_token_claims_nothing(self):
        async with self.scope() as session:
            self.assertIsNone(await claim(session, "not-a-real-token", user_id=USER))

    async def test_an_expired_report_claims_nothing(self):
        url = await self.park()
        token = url.split("report=")[1]
        async with self.scope() as session:
            row = await session.get(FailureReport, token)
            row.expires_at = utcnow() - timedelta(seconds=1)
        async with self.scope() as session:
            self.assertIsNone(await claim(session, token, user_id=USER))


class RetentionTests(_DbCase):
    async def test_a_person_keeps_only_their_most_recent_reports(self):
        """A bot blanking every reply would otherwise write a row per attempt, each holding
        a prompt and 800 lines of logs. The newest few are the ones worth having."""
        for i in range(PER_USER_CAP + 4):
            await self.park(prompt=f"attempt {i}")
        self.assertEqual(await self.rows(), PER_USER_CAP)
        async with self.scope() as session:
            kept = (await session.scalars(select(FailureReport.prompt))).all()
        self.assertIn(f"attempt {PER_USER_CAP + 3}", kept)  # the newest survived
        self.assertNotIn("attempt 0", kept)                 # the oldest did not

    async def test_the_cap_is_per_person(self):
        for i in range(PER_USER_CAP + 2):
            await self.park(prompt=f"mine {i}")
        await self.park(user_id=OTHER_USER, prompt="theirs")
        async with self.scope() as session:
            theirs = (await session.scalars(
                select(FailureReport.prompt).where(FailureReport.user_id == OTHER_USER)
            )).all()
        self.assertEqual(theirs, ["theirs"])

    async def test_expired_rows_are_dropped_on_the_next_write(self):
        """No sweep to schedule: these rows are only ever written a few at a time, so an
        unreported prompt ages out the next time anything touches the table."""
        await self.park(prompt="old")
        async with self.scope() as session:
            row = await session.scalar(select(FailureReport))
            row.expires_at = utcnow() - timedelta(days=1)
        await self.park(user_id=OTHER_USER, prompt="new")
        async with self.scope() as session:
            left = (await session.scalars(select(FailureReport.prompt))).all()
        self.assertEqual(left, ["new"])


if __name__ == "__main__":
    unittest.main()
