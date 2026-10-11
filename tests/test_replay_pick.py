"""The replay's model steps, with Gemini stood in for: moments, the pick (PICK.md), the persona's
lines and the last check of quotes. What's tested is what code does with the answers, which
is where PICK.md puts the safety: ids checked, finalists re-ordered and floored, the check as
the last gate, the writer held to its rules.

Run:  uv run python -m unittest tests.test_replay_pick -v
"""

from __future__ import annotations

import json
from datetime import timedelta

from olisar.db.engine import session_scope
from olisar.db.models import GuildFact, ReplayRun
from olisar.replay import check, pick, runner, store
from tests.replay_fakes import FakeGemini
from olisar.db.models import SearchMessage
from tests.test_replay import BRAM, GAMES, GENERAL, JUNIPER, NIGHTJAR, OKAPI, T0, TESS, FakeRoster, _Case, msg

GOOSE = [
    "absolute goose", "goose energy tonight", "that's so goose", "goose.", "most goose thing ever", "goose of the week",
    "pure goose", "a goose move", "total goose", "goose again", "classic goose", "goose hours",
]


class _Pick(_Case):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        extras = [10_000 + i for i in range(7)]
        people = [JUNIPER, BRAM, TESS, NIGHTJAR, *extras]
        async with session_scope() as s:
            s.add(msg(GAMES, OKAPI, T0 + timedelta(days=50, hours=1), "that was the most goose thing that’s ever happened"))
            for i in range(44):
                s.add(msg(GENERAL if i % 2 else GAMES, people[i % len(people)], T0 + timedelta(days=60 + i * 14, hours=2), GOOSE[i % len(GOOSE)]))
            s.add(GuildFact(guild_id=self.home, subject="goose", fact="a win nobody saw coming", source_channel_id=GENERAL))

    @property
    def home(self) -> int:
        from tests.test_replay import HOME
        return HOME

    async def run_with(self, gemini: FakeGemini) -> dict:
        async with session_scope() as s:
            run = ReplayRun(guild_id=self.home, status="queued", started_by=1)
            s.add(run)
            await s.flush()
            rid = run.id
        self.assertEqual(await runner.advance(rid, FakeRoster(), client=gemini), "done")
        async with session_scope() as s:
            return (await s.get(ReplayRun, rid)).report


class PickTests(_Pick):
    async def test_the_pick_plays_with_real_messages_and_numbers(self) -> None:
        g = FakeGemini()
        report = await self.run_with(g)
        p = report["pick"]
        self.assertEqual((p["title"], p["kind"], p["meaning"]), ("goose", "word", "a win nobody saw coming"))
        self.assertEqual(p["origin"]["text"], "that was the most goose thing that’s ever happened")
        self.assertEqual(report["people"][p["origin"]["who"]]["name"], "okapi")
        self.assertEqual(p["count"], 45)
        self.assertEqual(p["people"], 12)
        self.assertEqual(p["unit"], "times")
        self.assertIn("{people}", p["spread"])
        self.assertGreaterEqual(len(p["spots"]), 4)
        # The judge got the strongest model with thinking on; the checks went to Flash-Lite.
        kinds = [c[0] for c in g.calls]
        self.assertEqual(sum(k.startswith("You're choosing") for k in kinds), 1)
        self.assertIn("Other chapters:", next(p for k, p in g.calls if k.startswith("You're choosing")))

    async def test_an_invented_id_drops_the_finalist(self) -> None:
        judge = FakeGemini.judge
        g = FakeGemini()
        g.o["judge"] = None

        def invented(prompt: str) -> dict:
            out = judge(prompt)
            for f in out["finalists"]:
                f["trail"] = f["trail"] + ["m999"]
            return out
        g.judge = invented  # type: ignore[method-assign]
        self.assertIsNone((await self.run_with(g))["pick"])

    async def test_anything_short_of_a_clear_pass_drops_it(self) -> None:
        self.assertIsNone((await self.run_with(FakeGemini(hurts=None)))["pick"])
        self.assertIsNone((await self.run_with(FakeGemini(hurts=True)))["pick"])

    async def test_a_writer_that_breaks_the_rules_gets_the_template(self) -> None:
        g = FakeGemini(writer={"intro": "The legendary goose era began!", "spread": "212 people said it."})
        report = await self.run_with(g)
        self.assertIn("<name:", report["pick"]["intro"])   # stored as a token, served as a name
        p = store.present(report, set())["pick"]
        self.assertEqual(p["intro"], "Here, goose means a win nobody saw coming. okapi started it.")
        self.assertEqual(p["spread"], "{people} of you have kept it going. {count} times so far.")
        self.assertEqual(sum(p.startswith("Write the two lines") for _, p in g.calls), 2)   # one retry, then the template

    def test_code_reorders_and_floors_the_judges_finalists(self) -> None:
        blocks = {
            "c01": {"handles": {"m101": {"text": "a goose"}, "m102": {"text": "x"}}, "earliest": ["m101"]},
            "c02": {"handles": {"m201": {"text": "movie night at nine"}, "m202": {"text": "x"}}, "earliest": ["m201"]},
            "c03": {"handles": {"m301": {"text": "bridge"}}, "earliest": ["m301"]},
        }
        answer = {
            "candidates": [
                {"id": "c01", "scores": {"ours": 2, "shared": 2, "origin": 2, "lasting": 1, "warm": 2, "plain": 1}},
                {"id": "c02", "scores": {"ours": 3, "shared": 3, "origin": 3, "lasting": 3, "warm": 3, "plain": 3}},
                {"id": "c03", "scores": {"ours": 3, "shared": 1, "origin": 3, "lasting": 3, "warm": 3, "plain": 3}},
            ],
            "finalists": [
                {"id": "c01", "kind": "word", "title": "goose", "meaning": "a win nobody saw coming", "origin": "m101", "trail": ["m102"]},
                {"id": "c02", "kind": "ritual", "title": "movie night", "meaning": "Fridays at nine", "origin": "m201", "trail": ["m202"]},
                {"id": "c03", "kind": "word", "title": "bridge", "meaning": "a bridge", "origin": "m301", "trail": []},
            ],
        }
        out = pick.finalists(answer, blocks, {"okapi"})
        self.assertEqual([f["id"] for f in out], ["c02", "c01"])   # c03 is under the floor on shared
        answer["finalists"][1]["meaning"] = "what okapi does on Fridays"
        self.assertEqual([f["id"] for f in pick.finalists(answer, blocks, {"okapi"})], ["c01"])


class MomentAndCheckTests(_Pick):
    async def test_moments_fill_the_busiest_day_and_the_crowd(self) -> None:
        report = await self.run_with(FakeGemini())
        night = report["night"]
        self.assertEqual(night["day"], "2023-02-03")
        self.assertEqual(night["line"], "It was mostly about one candle.")       # in the persona's words
        self.assertEqual(night["sphere"], "the lights went out")
        self.assertGreaterEqual(len(night["excerpt"]), 3)
        burst = next(m for m in report["arrivals"]["milestones"] if m["kind"] == "burst")
        self.assertEqual(burst["line"], "Someone posted the server on r/cozygames.")
        self.assertGreater(report["moments"]["found"], 0)

    async def test_a_sensitive_busiest_day_gives_way_to_the_next(self) -> None:
        report = await self.run_with(FakeGemini(night_sensitive=True))
        night = report["night"]
        self.assertNotEqual(night["day"], "2023-02-03")
        self.assertGreater(night["rank"], 0)
        self.assertNotIn("candle", json.dumps(report))

    def test_the_last_check_drops_what_it_doesnt_pass(self) -> None:
        report = {
            "first": {"channel": "general", "messages": [{"text": "hi", "who": "1", "at": "x"}, {"text": "DROPME", "who": "2", "at": "x"}]},
            "founders": [{"who": "1", "msg": {"text": "DROPME too", "who": "1", "at": "x"}}],
            "night": {"line": "fine", "sphere": "fine", "excerpt": [
                {"text": "a", "who": "1", "at": "2023-02-04T00:00:00Z", "wi": 0}, {"gap": "1 hour later"},
                {"text": "DROPME", "who": "2", "at": "2023-02-04T01:00:00Z", "wi": 1}, {"gap": "1 hour later"},
                {"text": "b", "who": "1", "at": "2023-02-04T02:00:00Z", "wi": 2}, {"text": "c", "who": "1", "at": "2023-02-04T02:01:00Z", "wi": 3},
            ]},
            "arrive": {"reply": [{"text": "q", "who": "1", "at": "x"}, {"text": "kys", "who": "bot", "at": "x"}]},
            "pick": None, "names": [{"to": "Pinecone"}],
        }
        dropped = [q for q in check._quotes(report) if "DROPME" in str(q[1].get(q[2])) or "kys" in str(q[1].get(q[2]))]
        check._drop(report, dropped)
        self.assertEqual([m["text"] for m in report["first"]["messages"]], ["hi"])
        self.assertIsNone(report["founders"][0]["msg"])
        self.assertEqual([m.get("text", m.get("gap")) for m in report["night"]["excerpt"]], ["a", "2 hours later", "b", "c"])
        self.assertEqual(report["arrive"]["reply"], [])
        self.assertEqual(report["names"], [{"to": "Pinecone"}])


class ReviewFixTests(_Pick):
    async def test_a_pick_whose_first_poster_leaves_is_dropped_not_broken(self) -> None:
        report = await self.run_with(FakeGemini())
        served = store.present(report, {OKAPI})
        self.assertIsNone(served["pick"])
        self.assertNotIn("okapi", json.dumps(served["pick"]))
        # Someone quoted in a spot goes with their quote.
        spot = next(s for s in report["pick"]["spots"] if s.get("said"))
        gone = store.present(report, {int(spot["who"])})
        self.assertNotIn(spot["said"], json.dumps(gone))

    async def test_someone_who_opts_out_mid_run_isnt_in_the_finished_report(self) -> None:
        from olisar.db.models import UserProfile
        g = FakeGemini()
        original = g.generate_json

        async def opt_out_then_answer(**kw):
            if kw["system_instruction"].startswith("You check a replay"):
                async with session_scope() as s:
                    s.add(UserProfile(user_id=TESS, guild_id=self.home, display_name="tess", search_opt_out=True))
            return await original(**kw)
        g.generate_json = opt_out_then_answer  # type: ignore[method-assign]
        report = await self.run_with(g)
        self.assertNotIn(str(TESS), report["people"])
        self.assertNotIn("is this the book club", json.dumps(report))

    async def test_a_chain_full_for_the_minute_waits_rather_than_pausing_for_the_day(self) -> None:
        from unittest import mock

        from olisar.gemini.rate_limiter import RateLimitExceeded
        from olisar.replay import llm
        g = FakeGemini()
        original, failed = g.generate_json, []

        async def busy_once(**kw):
            if not failed:
                failed.append(1)
                raise RateLimitExceeded("m", "minute")
            return await original(**kw)
        g.generate_json = busy_once  # type: ignore[method-assign]
        with mock.patch.object(llm, "MINUTE_WAIT", 0.01):
            report = await self.run_with(g)
        self.assertIsNotNone(report)

    async def test_start_replaces_a_failed_run(self) -> None:
        async with session_scope() as s:
            s.add(ReplayRun(guild_id=self.home, status="failed", started_by=1, error="boom"))
        c = await self.client()
        r = await c.post("/api/replay/start", json={})
        self.assertEqual(r.status_code, 200, r.text)
        async with session_scope() as s:
            from sqlalchemy import select
            statuses = sorted((await s.scalars(select(ReplayRun.status))).all())
        self.assertEqual(statuses, ["abandoned", "queued"])


class MediaAndHighlightTests(_Pick):
    async def test_quoted_gifs_images_and_stickers_show(self) -> None:
        async with session_scope() as s:
            s.add(msg(GENERAL, BRAM, T0 + timedelta(minutes=13), "https://tenor.com/view/hulk-hogan-flex-gif-123\n[embed: https://tenor.com/view/hulk-hogan-flex-gif-123]"))
            s.add(msg(GENERAL, TESS, T0 + timedelta(minutes=13, seconds=30), "[sticker: Wave]"))
        report = await self.run_with(FakeGemini())
        first = report["first"]["messages"]
        gif = next(m for m in first if m["who"] == str(BRAM))
        self.assertEqual(gif["text"], "")
        self.assertEqual(gif["media"][0]["kind"], "gif")
        sticker = next(m for m in first if m["who"] == str(TESS))
        self.assertEqual((sticker["text"], sticker["media"][0]["name"]), ("", "Wave"))

    async def test_an_image_is_served_from_a_fresh_address(self) -> None:
        from types import SimpleNamespace
        report = await self.run_with(FakeGemini())
        quoted = report["first"]["messages"][0]
        quoted["media"] = [{"kind": "image", "i": 0, "w": 640, "h": 480}]
        async with session_scope() as s:
            from sqlalchemy import select
            run = (await s.scalars(select(ReplayRun).where(ReplayRun.status == "done"))).first()
            run.report = dict(report)

        async def fetch_message(mid):
            return SimpleNamespace(attachments=[SimpleNamespace(url=f"https://cdn.discordapp.com/attachments/1/{mid}/a.png?ex=fresh")])
        member = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True))
        guild = SimpleNamespace(get_member=lambda uid: member, chunked=False)
        bot = SimpleNamespace(is_ready=lambda: True, get_channel=lambda cid: SimpleNamespace(fetch_message=fetch_message),
                              get_guild=lambda gid: guild, user=None, get_cog=lambda name: None)
        c = await self.client()
        c._transport.app.state.bot_supervisor = SimpleNamespace(bot=bot)
        r = await c.get(f"/api/replay/media/{quoted['id']}/0", params={"g": str(self.home)}, headers={"x-guild-id": ""})
        self.assertEqual(r.status_code, 302, r.text)
        self.assertTrue(r.headers["location"].endswith("a.png?ex=fresh"))
        # Anything not quoted in this server's replay isn't served.
        self.assertEqual((await c.get("/api/replay/media/12345/0", params={"g": str(self.home)})).status_code, 404)
        outsider = await self.client(admin=222)
        self.assertEqual((await outsider.get(f"/api/replay/media/{quoted['id']}/0", params={"g": str(self.home)})).status_code, 403)

    async def test_a_first_reply_that_wasnt_a_discord_reply_finds_its_question(self) -> None:
        from olisar.db.models import Message
        from tests.test_replay import BOT_ID, BOT_JOINED, sf
        async with session_scope() as s:
            from sqlalchemy import delete
            await s.execute(delete(Message))
            at = BOT_JOINED + timedelta(minutes=30)
            s.add(Message(guild_id=self.home, channel_id=GENERAL, message_id=sf(at, 50), author_id=TESS, author_name="tess",
                          content="anyone know a good book", created_at=at - timedelta(minutes=3)))
            s.add(Message(guild_id=self.home, channel_id=GENERAL, message_id=sf(at, 51), author_id=BRAM, author_name="bram",
                          content="olisar what should i read next", created_at=at - timedelta(minutes=1)))
            s.add(Message(guild_id=self.home, channel_id=GENERAL, message_id=sf(at, 52), author_id=JUNIPER, author_name="juniper",
                          content="lol", created_at=at - timedelta(seconds=30)))
            s.add(Message(guild_id=self.home, channel_id=GENERAL, message_id=sf(at, 53), author_id=BOT_ID, author_is_bot=True,
                          author_name="", content="Piranesi. Trust me on this one.", created_at=at))
        reply = (await self.run_with(FakeGemini()))["arrive"]["reply"]
        self.assertEqual([m["text"] for m in reply], ["olisar what should i read next", "Piranesi. Trust me on this one."])

    async def test_the_busiest_day_keeps_what_passes_the_last_check(self) -> None:
        async with session_scope() as s:
            from sqlalchemy import select
            rows = (await s.scalars(select(SearchMessage).where(SearchMessage.content.like("candle talk%")))).all()
            for r in rows[:-12]:
                r.content = r.content + " DROPME"
        report = await self.run_with(FakeGemini())
        excerpt = report["night"]["excerpt"]
        texts = [m["text"] for m in excerpt if "text" in m]
        self.assertTrue(texts)
        self.assertFalse(any("DROPME" in t for t in texts))
        self.assertNotIn("more", report["night"])
        self.assertFalse(any("wi" in m for m in excerpt))


if __name__ == "__main__":
    import unittest
    unittest.main()
