"""The activity feed behind the server app's final screen (olisar/activity.py), and the SSH
side that reads it back (olisar.runtime.remote.parse_activity).

Run:  uv run python -m unittest tests.test_activity_feed -v

The feed runs inside the bot's container against the live database, so what matters is:
  * the shapes, which the console is written against
  * what it must never show: a DM (including the rows a DM files under the home server),
    anyone who opted out or is paused, and servers the bot has left
  * that it can't write, whatever it's asked
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from olisar import activity
from olisar.activity import (
    ITEM_LIMIT,
    REPLY_LIMIT,
    group_replies,
    reply_trigger,
    select_items,
    status_how,
)
from olisar.db.models import (
    Base,
    BotActivity,
    Guild,
    GuildChannelInfo,
    GuildFact,
    KBChunk,
    KBSource,
    KBSourceType,
    KBStatus,
    Message,
    Reminder,
    SearchMessage,
    UserMemory,
    UserMemoryKind,
    UserProfile,
)
from olisar.runtime.remote import parse_activity

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
GUILD, LEFT_GUILD = 5001, 5002
GENERAL, ART, DM_CHANNEL = 11, 12, 99
ADA, BEN, CY, DEE, OLD = 101, 102, 103, 104, 105
BOT = 900


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


class TriggerTests(unittest.TestCase):
    def test_known_triggers_pass_through(self) -> None:
        for t in ("ask", "name", "mention", "reply", "proactive", "catchup"):
            self.assertEqual(reply_trigger(t), t)

    def test_anything_else_is_unknown(self) -> None:
        """"dm" is stored but a DM is never shown; an old row has none at all."""
        for t in (None, "", "dm", "loose", "whatever"):
            self.assertEqual(reply_trigger(t), "")

    def test_case_and_space_are_forgiven(self) -> None:
        self.assertEqual(reply_trigger(" Mention "), "mention")


class ShapingTests(unittest.TestCase):
    def test_iso_is_utc_to_the_millisecond(self) -> None:
        self.assertEqual(activity.iso(datetime(2026, 9, 26, 8, 15, 2, 123999)), "2026-09-26T08:15:02.123Z")
        plus2 = timezone(timedelta(hours=2))
        self.assertEqual(activity.iso(datetime(2026, 9, 26, 10, 0, tzinfo=plus2)), "2026-09-26T08:00:00.000Z")
        self.assertIsNone(activity.iso(None))

    def test_clip(self) -> None:
        self.assertEqual(activity.clip("  hi  "), "hi")
        long = "x" * 700
        self.assertEqual(len(activity.clip(long)), 600)
        self.assertTrue(activity.clip(long).endswith("…"))
        self.assertEqual(activity.clip(None), "")

    def test_status_how(self) -> None:
        self.assertEqual(status_how(None, ""), "Set when it started")
        self.assertEqual(status_how(GUILD, "#general"), "Set during a conversation in #general")
        self.assertEqual(status_how(0, ""), "Set during a conversation")  # a DM is never named
        self.assertEqual(status_how(GUILD, ""), "Set during a conversation")

    def test_a_reply_sent_as_several_messages_is_one_reply(self) -> None:
        rows = [
            {"message_id": 3, "guild_id": GUILD, "channel_id": GENERAL, "at": ago(minutes=29),
             "content": "want the route?", "answering": 1, "trigger": "mention"},
            {"message_id": 2, "guild_id": GUILD, "channel_id": GENERAL, "at": ago(minutes=29),
             "content": "it's at pyro", "answering": 1, "trigger": "mention"},
        ]
        [reply] = group_replies(rows)
        self.assertEqual(reply["id"], "reply:2")
        self.assertEqual(reply["text"], "it's at pyro\nwant the route?")
        self.assertEqual((reply["answering"], reply["trigger"]), (1, "mention"))

    def test_old_parts_group_by_how_close_they_are(self) -> None:
        rows = [
            {"message_id": 10, "guild_id": GUILD, "channel_id": GENERAL, "at": ago(minutes=60),
             "content": "one", "answering": None, "trigger": None},
            {"message_id": 11, "guild_id": GUILD, "channel_id": GENERAL, "at": ago(minutes=60) + timedelta(seconds=5),
             "content": "two", "answering": None, "trigger": None},
            {"message_id": 12, "guild_id": GUILD, "channel_id": GENERAL, "at": ago(minutes=30),
             "content": "later", "answering": None, "trigger": None},
        ]
        replies = group_replies(rows)
        self.assertEqual([r["text"] for r in replies], ["later", "one\ntwo"])  # newest first
        self.assertTrue(all(r["trigger"] == "" for r in replies))

    def test_empty_rows_are_dropped(self) -> None:
        rows = [{"message_id": 1, "guild_id": GUILD, "channel_id": GENERAL, "at": NOW,
                 "content": "  ", "answering": 5, "trigger": "name"}]
        self.assertEqual(group_replies(rows), [])

    def test_replies_are_capped_so_other_kinds_surface(self) -> None:
        items = [
            {"id": f"reply:{i}", "kind": "reply", "at": activity.iso(ago(minutes=i))} for i in range(20)
        ] + [
            {"id": f"glossary:{i}", "kind": "glossary", "at": activity.iso(ago(hours=1, minutes=i))}
            for i in range(20)
        ]
        out = select_items(items)
        self.assertEqual(len(out), ITEM_LIMIT)
        self.assertEqual(sum(1 for i in out if i["kind"] == "reply"), REPLY_LIMIT)
        self.assertEqual([i["at"] for i in out], sorted((i["at"] for i in out), reverse=True))


def _seed(session: Session) -> None:
    """A server with a bit of everything, and a matching thing that mustn't show for each."""
    session.add_all([
        Guild(id=GUILD, name="Home", active=True, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
              roster_count=176, roster_synced_at=ago(minutes=10)),
        Guild(id=LEFT_GUILD, name="Gone", active=False, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Guild(id=0, name="Direct messages", active=False),
        GuildChannelInfo(channel_id=GENERAL, guild_id=GUILD, name="general"),
        GuildChannelInfo(channel_id=ART, guild_id=GUILD, name="art"),
    ])

    def profile(uid, name, **kw):
        kw.setdefault("guild_id", GUILD)
        kw.setdefault("avatar", f"https://cdn.discordapp.com/avatars/{uid}/a.png")
        return UserProfile(user_id=uid, display_name=name, roles=kw.pop("roles", []), notes={},
                           last_seen=kw.pop("last_seen", ago(minutes=5)), **kw)

    session.add_all([
        profile(ADA, "Ada", roles=[{"id": "1", "name": "Pilot"}], joined_at=ago(days=2),
                persona_summary="Ada flies cargo out of Area18.", persona_updated_at=ago(hours=1)),
        profile(BEN, "Ben", memory_opt_out=True, joined_at=ago(days=1),
                persona_summary="Ben likes mining.", persona_updated_at=ago(minutes=50)),
        profile(CY, "Cy", pause_until=NOW + timedelta(days=1), joined_at=ago(days=1)),
        profile(DEE, "Dee", pause_until=ago(days=1), avatar="", last_seen=ago(days=3)),
        profile(OLD, "Old", joined_at=datetime(2025, 6, 1, tzinfo=timezone.utc)),
        # A DM profile's impression is built from DMs.
        profile(ADA, "Ada", guild_id=0, persona_summary="Ada's DMs.", persona_updated_at=ago(minutes=1)),
    ])

    def human(mid, uid, text, at, *, guild=GUILD, channel=GENERAL, name=""):
        return Message(guild_id=guild, channel_id=channel, message_id=mid, author_id=uid,
                       author_is_bot=False, author_name=name, content=text, created_at=at)

    def mine(mid, text, at, *, answering=None, trigger=None, guild=GUILD, channel=GENERAL):
        return Message(guild_id=guild, channel_id=channel, message_id=mid, author_id=BOT,
                       author_is_bot=True, author_name="", content=text, created_at=at,
                       reply_to_message_id=answering, trigger=trigger)

    session.add_all([
        # Ada asks by mention; Olisar answers in two messages.
        human(1000, ADA, "olisar where's the jump point?", ago(minutes=30), name="Ada"),
        mine(1001, "It's at Pyro.", ago(minutes=29), answering=1000, trigger="mention"),
        mine(1002, "Want the route?", ago(minutes=29), answering=1000, trigger="mention"),
        # Ben opted out: Olisar's answer to him stays out.
        human(1003, BEN, "olisar hi", ago(minutes=20)),
        mine(1004, "Hey Ben.", ago(minutes=19), answering=1003, trigger="name"),
        # A DM, both sides.
        human(1005, ADA, "secret plans", ago(minutes=15), guild=0, channel=DM_CHANNEL),
        mine(1006, "Noted.", ago(minutes=14), answering=1005, trigger="dm", guild=0, channel=DM_CHANNEL),
        # A reply from before replies recorded what they answered.
        human(900, DEE, "anyone know a good ship for salvage?", ago(hours=3), channel=ART, name="Dee"),
        mine(901, "The Vulture.", ago(hours=3) + timedelta(seconds=20), channel=ART),
        # A turn answered with a reaction, and another bot: neither is a reply.
        mine(-1000, "[reacted 👍]", ago(minutes=28)),
        Message(guild_id=GUILD, channel_id=GENERAL, message_id=1007, author_id=77, author_is_bot=True,
                author_name="MEE6", content="GG you leveled up", created_at=ago(minutes=10)),
        # A server the bot has left.
        human(1008, ADA, "olisar?", ago(minutes=9), guild=LEFT_GUILD, channel=55),
        mine(1009, "Yes?", ago(minutes=8), answering=1008, trigger="name", guild=LEFT_GUILD, channel=55),
        # A chime-in on Ada, and a /ask-shaped trigger (for a future recorder) on Ada.
        human(1010, ADA, "the quantum drive keeps failing", ago(minutes=7), name="Ada"),
        mine(1011, "Check the cooler first.", ago(minutes=6), answering=1010, trigger="proactive"),
    ])

    session.add_all([
        UserMemory(user_id=ADA, guild_id=GUILD, kind=UserMemoryKind.preference, content="Prefers cargo runs",
                   source_message_id=1000, created_at=ago(minutes=28)),
        # No server message behind it: saved in a DM (or before this was recorded).
        UserMemory(user_id=ADA, guild_id=GUILD, content="Lives in Berlin", created_at=ago(minutes=27)),
        UserMemory(user_id=BEN, guild_id=GUILD, content="Mines quantanium", source_message_id=1003,
                   created_at=ago(minutes=26)),
        # Its source was pruned from memory, but the search index still has it.
        UserMemory(user_id=DEE, guild_id=GUILD, kind=UserMemoryKind.event, content="Flying to Pyro on Friday",
                   source_message_id=777, created_at=ago(minutes=25)),
        SearchMessage(guild_id=GUILD, channel_id=ART, channel_name="art", message_id=777, author_id=DEE,
                      author_name="Dee", content="heading to pyro friday", created_at=ago(minutes=26)),
    ])

    session.add_all([
        GuildFact(guild_id=GUILD, subject="ICA", fact="ICA is Ironclad Assault", source_channel_id=GENERAL,
                  created_at=ago(minutes=40)),
        GuildFact(guild_id=GUILD, subject="Griefernet", fact="An enemy org", source_channel_id=None,
                  created_at=ago(minutes=41)),
        GuildFact(guild_id=GUILD, subject="Secret", fact="Said in a DM", source_channel_id=DM_CHANNEL,
                  created_at=ago(minutes=39)),
        GuildFact(guild_id=0, subject="DM", fact="Mined from a DM", created_at=ago(minutes=38)),
    ])

    session.add_all([
        BotActivity(kind="status", text="watching the stars", created_at=ago(hours=5)),
        BotActivity(kind="status", text="plotting a route", guild_id=GUILD, channel_id=GENERAL,
                    request_message_id=1000, created_at=ago(minutes=29)),
        BotActivity(kind="status", text="thinking", guild_id=0, channel_id=DM_CHANNEL, created_at=ago(minutes=14)),
        BotActivity(kind="image", text="a Vulture over Pyro", guild_id=GUILD, channel_id=ART,
                    request_message_id=1000, created_at=ago(minutes=29)),
        BotActivity(kind="image", text="a secret", guild_id=0, channel_id=DM_CHANNEL,
                    request_message_id=1005, created_at=ago(minutes=14)),
        BotActivity(kind="image", text="a drill", guild_id=GUILD, channel_id=ART,
                    request_message_id=1003, created_at=ago(minutes=19)),
    ])

    wiki = KBSource(guild_id=GUILD, type=KBSourceType.website, uri="https://starcitizen.tools",
                    title="Star Citizen wiki", status=KBStatus.ready, added_by=ADA,
                    last_ingested_at=ago(hours=2))
    doc = KBSource(guild_id=GUILD, type=KBSourceType.doc, uri="/var/lib/olisar/kb_uploads/abc.pdf",
                   title="", status=KBStatus.ready, added_by=BEN, last_ingested_at=ago(hours=4))
    pending = KBSource(guild_id=GUILD, type=KBSourceType.url, uri="https://example.com", title="Pending",
                       status=KBStatus.pending, created_at=ago(minutes=1))
    session.add_all([wiki, doc, pending])
    session.flush()
    session.add_all([KBChunk(source_id=wiki.id, guild_id=GUILD, ordinal=i, content="…") for i in range(3)])

    session.add_all([
        Reminder(guild_id=GUILD, channel_id=GENERAL, user_id=ADA, content="stand up", fired=True,
                 scheduled_at=ago(hours=1)),
        Reminder(guild_id=GUILD, channel_id=DM_CHANNEL, user_id=ADA, content="dm reminder", fired=True,
                 scheduled_at=ago(minutes=50)),
        Reminder(guild_id=GUILD, channel_id=GENERAL, user_id=ADA, content="later", fired=False,
                 scheduled_at=NOW + timedelta(hours=1)),
        Reminder(guild_id=GUILD, channel_id=GENERAL, user_id=BEN, content="ben's", fired=True,
                 scheduled_at=ago(minutes=40)),
    ])


def _memory_db() -> Session:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return Session(engine)


class FeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _memory_db()
        _seed(self.session)
        self.session.commit()
        self.out = activity.feed(self.session, NOW)
        self.items = self.out["items"]

    def tearDown(self) -> None:
        self.session.close()

    def of(self, kind: str) -> list[dict]:
        return [i for i in self.items if i["kind"] == kind]

    def test_every_item_has_an_id_a_kind_and_a_time(self) -> None:
        self.assertTrue(self.items)
        for item in self.items:
            self.assertRegex(item["id"], r"^[a-z]+:")
            self.assertRegex(item["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertEqual(len({i["id"] for i in self.items}), len(self.items))
        self.assertEqual([i["at"] for i in self.items], sorted((i["at"] for i in self.items), reverse=True))
        json.dumps(self.out)  # it has to survive the trip

    def test_a_reply(self) -> None:
        replies = {i["id"]: i for i in self.of("reply")}
        self.assertEqual(replies["reply:1001"], {
            "id": "reply:1001",
            "kind": "reply",
            "at": "2026-09-26T11:31:00.000Z",
            "who": {"name": "Ada", "avatar": f"https://cdn.discordapp.com/avatars/{ADA}/a.png"},
            "where": "#general",
            "trigger": "mention",
            "ask": "olisar where's the jump point?",
            "text": "It's at Pyro.\nWant the route?",
        })
        self.assertEqual(replies["reply:1011"]["trigger"], "proactive")

    def test_an_old_reply_is_matched_to_the_message_before_it(self) -> None:
        old = next(i for i in self.of("reply") if i["id"] == "reply:901")
        self.assertEqual(old["trigger"], "")
        self.assertEqual(old["who"]["name"], "Dee")  # Dee's pause has run out
        self.assertEqual(old["ask"], "anyone know a good ship for salvage?")
        self.assertEqual(old["where"], "#art")

    def test_no_dm_opted_out_other_bot_reaction_or_left_server_reply(self) -> None:
        self.assertEqual(sorted(i["id"] for i in self.of("reply")), ["reply:1001", "reply:1011", "reply:901"])

    def test_nothing_from_a_dm_anywhere(self) -> None:
        blob = json.dumps(self.out)
        for secret in ("secret plans", "Noted.", "Said in a DM", "Mined from a DM", "dm reminder",
                       "a secret", "Ada's DMs.", "Lives in Berlin"):
            self.assertNotIn(secret, blob)

    def test_nobody_who_opted_out_or_is_paused(self) -> None:
        blob = json.dumps(self.out)
        for hidden in ("Ben", "Cy", "Mines quantanium", "Ben likes mining.", "a drill", "ben's"):
            self.assertNotIn(hidden, blob)

    def test_new_members_are_recent_joins_since_the_bot_arrived(self) -> None:
        self.assertEqual(self.of("member"), [{
            "id": f"member:{GUILD}:{ADA}",
            "kind": "member",
            "at": "2026-09-24T12:00:00.000Z",
            "who": {"name": "Ada", "avatar": f"https://cdn.discordapp.com/avatars/{ADA}/a.png"},
            "roles": ["Pilot"],
        }])

    def test_an_impression(self) -> None:
        [imp] = self.of("impression")
        self.assertEqual(imp["who"]["name"], "Ada")
        self.assertEqual(imp["text"], "Ada flies cargo out of Area18.")
        self.assertEqual(imp["messages"], 2)  # her two stored messages in this server
        self.assertTrue(imp["id"].startswith("impression:"))

    def test_remembered_facts_need_a_server_message_behind_them(self) -> None:
        facts = {i["text"]: i for i in self.of("remembered")}
        self.assertEqual(set(facts), {"Prefers cargo runs", "Flying to Pyro on Friday"})
        cargo = facts["Prefers cargo runs"]
        self.assertEqual((cargo["type"], cargo["said"], cargo["where"]),
                         ("preference", "olisar where's the jump point?", "#general"))
        pyro = facts["Flying to Pyro on Friday"]
        self.assertEqual((pyro["type"], pyro["said"], pyro["where"]), ("event", "heading to pyro friday", "#art"))

    def test_glossary(self) -> None:
        facts = {i["subject"]: i for i in self.of("glossary")}
        self.assertEqual(set(facts), {"ICA", "Griefernet"})
        self.assertEqual(facts["ICA"]["where"], "#general")
        self.assertEqual(facts["Griefernet"]["where"], "")
        self.assertEqual(facts["ICA"]["text"], "ICA is Ironclad Assault")

    def test_statuses_say_how_they_were_set(self) -> None:
        how = {i["text"]: i["how"] for i in self.of("status")}
        self.assertEqual(how, {
            "watching the stars": "Set when it started",
            "plotting a route": "Set during a conversation in #general",
            "thinking": "Set during a conversation",
        })

    def test_learned(self) -> None:
        learned = {i["how"]: i for i in self.of("learned")}
        self.assertEqual(set(learned), {"site", "doc"})  # not the one still pending
        site = learned["site"]
        self.assertEqual((site["title"], site["url"], site["count"]),
                         ("Star Citizen wiki", "https://starcitizen.tools", 3))
        self.assertEqual(site["who"]["name"], "Ada")
        doc = learned["doc"]
        self.assertEqual((doc["title"], doc["url"], doc["count"], doc["who"]), ("Document", "", 0, None))

    def test_reminders_that_fired_in_a_server_channel(self) -> None:
        self.assertEqual([(i["text"], i["where"], i["who"]["name"]) for i in self.of("reminder")],
                         [("stand up", "#general", "Ada")])

    def test_images(self) -> None:
        self.assertEqual([(i["text"], i["where"], i["who"]["name"]) for i in self.of("image")],
                         [("a Vulture over Pyro", "#art", "Ada")])

    def test_the_roster_sync(self) -> None:
        members = self.out["members"]
        self.assertEqual(members["count"], 176)
        self.assertEqual(members["at"], "2026-09-26T11:50:00.000Z")
        names = [f["name"] for f in members["faces"]]
        self.assertIn("Ada", names)
        self.assertNotIn("Ben", names)
        self.assertNotIn("Cy", names)
        self.assertNotIn("Dee", names)  # no avatar to show
        self.assertEqual(len(names), len(set(names)))

    def test_an_empty_database(self) -> None:
        with _memory_db() as session:
            self.assertEqual(activity.feed(session, NOW), {"items": [], "members": None})


class ReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "olisar db.sqlite"  # a space, like Application Support
        engine = create_engine(f"sqlite:///{self.path}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            _seed(session)
            session.commit()
        engine.dispose()

    def test_the_feed_reads_through_a_connection_that_cannot_write(self) -> None:
        engine = activity.open_readonly(self.path)
        try:
            with Session(engine) as session:
                self.assertTrue(activity.feed(session, NOW)["items"])
            with engine.connect() as conn:
                with self.assertRaises(Exception) as caught:
                    conn.exec_driver_sql("DELETE FROM message")
                self.assertIsInstance(caught.exception.orig, sqlite3.OperationalError)
        finally:
            engine.dispose()
        engine = create_engine(f"sqlite:///{self.path}")
        with engine.connect() as conn:
            self.assertGreater(conn.exec_driver_sql("SELECT count(*) FROM message").scalar(), 0)
        engine.dispose()

    def test_collect_is_the_whole_answer(self) -> None:
        checks = {"vec": True, "sandbox": True, "model": "ok"}
        with mock.patch.object(activity, "database_path", lambda: self.path), \
                mock.patch.object(activity, "health", lambda: checks):
            out = activity.collect()
        self.assertEqual((out["ok"], out["supported"], out["health"]), (True, True, checks))
        self.assertTrue(out["items"])
        self.assertIsNotNone(out["members"])

    def test_no_database_yet(self) -> None:
        with mock.patch.object(activity, "database_path", lambda: self.path.with_name("none.db")), \
                mock.patch.object(activity, "health", lambda: None):
            out = activity.collect()
        self.assertFalse(out["ok"])
        self.assertFalse(self.path.with_name("none.db").exists())  # and it didn't make one

    def test_the_cli_prints_one_json_line(self) -> None:
        stdout = io.StringIO()
        with mock.patch.object(activity, "database_path", lambda: self.path), \
                mock.patch.object(activity, "health", lambda: None), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(activity.main(), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(json.loads(lines[0])["ok"])


class HealthTests(unittest.TestCase):
    """The backend's own /api/health, passed through; nothing when it doesn't answer."""

    def serve(self, body: bytes) -> int:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200 if self.path == "/api/health" else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_passes_the_checks_through(self) -> None:
        port = self.serve(json.dumps({
            "ok": True, "vec": True, "sandbox": False, "transpile": True, "model": "inconclusive",
            "model_failed": [],
        }).encode())
        self.assertEqual(activity.health(port), {"vec": True, "sandbox": False, "model": "inconclusive"})

    def test_checks_that_have_not_run_are_null(self) -> None:
        port = self.serve(json.dumps({"ok": True, "vec": None, "sandbox": None, "model": None}).encode())
        self.assertEqual(activity.health(port), {"vec": None, "sandbox": None, "model": None})

    def test_unreachable_is_none(self) -> None:
        port = self.serve(b"")
        self.assertIsNone(activity.health(port + 1 if port < 65535 else port - 1, timeout=0.5))

    def test_garbage_is_none(self) -> None:
        self.assertIsNone(activity.health(self.serve(b"<html>"), timeout=1))


class ParseActivityTests(unittest.TestCase):
    """What the control panel makes of what the container printed."""

    def test_a_feed(self) -> None:
        body = {"ok": True, "supported": True, "items": [{"id": "status:1"}],
                "members": {"count": 3, "at": None, "faces": []}, "health": None}
        out = parse_activity("some noise\n" + json.dumps(body) + "\n")
        self.assertEqual(out, body)

    def test_an_image_from_before_the_feed(self) -> None:
        err = "/app/.venv/bin/python: No module named olisar.activity\n"
        self.assertEqual(parse_activity("", err), {"ok": True, "supported": False, "items": []})

    def test_a_stopped_server(self) -> None:
        out = parse_activity("__OLISAR_NOT_RUNNING__\n")
        self.assertFalse(out["ok"])
        self.assertIn("isn't running", out["error"])

    def test_no_install(self) -> None:
        self.assertFalse(parse_activity("__OLISAR_NO_INSTALL__\n")["ok"])

    def test_the_feed_s_own_error_is_passed_on(self) -> None:
        out = parse_activity(json.dumps({"ok": False, "error": "The bot has no database yet."}))
        self.assertEqual(out, {"ok": False, "error": "The bot has no database yet."})

    def test_anything_else_is_an_error_with_what_was_said(self) -> None:
        out = parse_activity("", "Error response from daemon: container abc is restarting\n")
        self.assertFalse(out["ok"])
        self.assertIn("is restarting", out["error"])
        self.assertFalse(parse_activity("{not json")["ok"])
        self.assertFalse(parse_activity("")["ok"])

    def test_missing_pieces_come_back_empty(self) -> None:
        out = parse_activity(json.dumps({"ok": True, "items": "nope"}))
        self.assertEqual(out, {"ok": True, "supported": True, "items": [], "members": None, "health": None})


if __name__ == "__main__":
    unittest.main()
