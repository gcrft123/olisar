"""Execute a scenario and capture what happened.

Two lanes, one transcript shape, so everything downstream — checks, judge, scorecard —
treats a fast-lane and a live-lane run identically.

**Turn-taking is driven from here, never emergent.** An emulator posts only when the
scenario says so. The obvious-looking alternative — give each bot a reply policy and let
them talk — produces a two-party loop between Olisar and whichever emulator reacts fastest,
which burns the day's free-tier quota in minutes and yields a transcript nobody wants to
read. Three governors make that structural rather than hoped-for: a hard message ceiling, a
minimum gap between posts, and a wall-clock timeout on the whole run.

The live lane observes by polling ``GET /channels/{id}/messages?after=`` rather than holding
a gateway connection. Polling also catches what Olisar does *unprompted* — a proactive
chime, a reaction, an image posted by a tool — which is a large part of what the arena is
for.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from arena.config import ArenaConfig
from arena.control.dashboard import Dashboard, DashboardError
from arena.control.guild import Steward, olisar_user_id
from arena.discord_rest import DiscordRest
from arena.eval.transcript import Run, Turn, apply_checks, new_run_id, now_iso
from arena.fleet import registry
from arena.fleet.dialogue import compose
from arena.fleet.persona import Persona, load_all as load_personas
from arena.model import ModelClient
from arena.scenarios.schema import Beat, Scenario

log = logging.getLogger("arena.runner")

_POLL_SECONDS = 2.0

# How long Olisar must stay quiet before a reply counts as finished. Sized from the
# delivery pacing in bot/replies.py — a break pause of up to 1.1s plus typing time at
# ~18 chars/sec, so a ~200-character follow-on message lands about twelve seconds after
# the one before it. Under-sizing this doesn't fail loudly; it quietly truncates the
# transcript, which is worse.
_REPLY_SETTLE_SECONDS = 14.0
# A backstop for the pathological case (a reply that keeps going, or a channel someone
# else is posting into). Bounds one beat, not the run — the scenario timeout still applies.
_REPLY_DRAIN_CEILING = 90.0
# The bot mirrors the guild's channels into GuildChannelInfo on a 90s loop, and the
# indexing flag needs that row. Slightly over one full cycle, so a channel created just
# after a sync still gets caught by the next one.
_ROSTER_SYNC_TIMEOUT = 105.0


# Lines Olisar logs when its model chain has nothing left to try. Free-tier Gemini is
# ~10 RPM per model, and the reply path competes with Olisar's own background work —
# embedding, summaries, glossary mining, and a proactivity classifier that scans every 25s.
_STARVED_MARKERS = ("parked for", "RateLimitExceeded", "no model available")


class RunAborted(RuntimeError):
    """A governor stopped the run. Not a bug — the transcript up to that point is kept."""


@dataclass
class _Speaker:
    persona: Persona
    rest: DiscordRest
    user_id: int


def _address(text: str, mode: str | None, olisar_id: int, name_trigger: str) -> str:
    """Prefix a line so it definitely triggers Olisar, when the scenario asks for that."""
    if mode == "mention":
        return f"<@{olisar_id}> {text}"
    if mode == "name":
        return f"{name_trigger}, {text}"
    return text


def starved_lines(log_lines: list[str]) -> list[str]:
    """Lines showing Olisar's model chain had nothing available."""
    return [line for line in log_lines if any(m in line for m in _STARVED_MARKERS)]


def _starvation_error(cfg: ArenaConfig, run: Run) -> str:
    """Report a run as inconclusive when silence can't be attributed to a decision.

    Applies to ``must_not_reply`` scenarios as much as ``must_reply`` ones, which is the
    whole point: a rate-limited run where Olisar stayed quiet *passes* a silence check, for
    entirely the wrong reason. Marking it an error rather than a pass is the difference
    between a suite that measures restraint and one that rewards an exhausted quota.

    Only silence is treated as suspect. A run where the chain was partly parked but Olisar
    still answered — it walks down to a lower model — is fine, and is common enough that
    flagging it would discard most live runs.
    """
    from arena.control import supervisor

    if run.olisar_turns:
        return ""
    hits = starved_lines(supervisor.tail(cfg, lines=800))
    if not hits:
        return ""
    return (
        "inconclusive: Olisar's model chain was rate-limited during this run, so its "
        f"silence can't be read as a decision — {hits[-1].strip()[:160]}"
    )


class LiveRunner:
    """Runs one scenario against the real arena guild."""

    def __init__(self, cfg: ArenaConfig, model: ModelClient) -> None:
        self._cfg = cfg
        self._model = model
        self._posted = 0
        self._last_post = 0.0

    async def run(self, scenario: Scenario, *, variant: str = "baseline") -> Run:
        cfg = self._cfg
        run = Run(
            run_id=new_run_id(scenario.id, variant),
            scenario_id=scenario.id,
            lane="live",
            variant=variant,
            started_at=now_iso(),
        )
        personas = load_personas()
        members = {m.key: m for m in registry.cached(cfg)}

        missing = [k for k in scenario.cast if k not in personas]
        if missing:
            run.error = f"cast references unknown persona(s): {', '.join(missing)}"
            return run
        unresolved = [k for k in scenario.cast if k not in members]
        if unresolved:
            run.error = (
                f"no resolved bot for {', '.join(unresolved)} — set ARENA_BOT_TOKEN_"
                f"{unresolved[0].upper()} and run `arena fleet resolve`"
            )
            return run

        olisar_id = await olisar_user_id(cfg)
        name_trigger = await self._name_trigger()
        restore: dict = {}
        # Scope the instance log to this run, so the starvation check below reads only
        # what happened during it. Done here rather than in the CLI so `arena loop` gets
        # it too — that's the caller whose results most need the guard.
        from arena.control import supervisor

        supervisor.truncate_log(cfg)

        speakers: dict[str, _Speaker] = {}
        try:
            restore = await self._apply_config(scenario)
            async with Steward(cfg) as steward:
                channel_id = await self._prepare_channel(steward, scenario, olisar_id, members)
                await self._set_channel_mode(
                    channel_id, scenario.channel_mode, scenario.channel_indexed
                )

                for key in scenario.cast:
                    rest = DiscordRest(cfg.fleet_tokens[key], label=key)
                    await rest.__aenter__()
                    speakers[key] = _Speaker(personas[key], rest, members[key].user_id)

                cursor = await self._latest_message_id(steward.rest, channel_id)
                deadline = time.monotonic() + cfg.scenario_timeout_seconds

                for beat in scenario.seed:
                    cursor = await self._play(
                        run, speakers, steward.rest, channel_id, beat, cursor,
                        olisar_id, name_trigger, deadline, seeding=True,
                    )
                for beat in scenario.beats:
                    cursor = await self._play(
                        run, speakers, steward.rest, channel_id, beat, cursor,
                        olisar_id, name_trigger, deadline,
                    )
                # A final sweep: Olisar's reply to the last beat, or a proactive chime,
                # commonly lands after the beat loop has moved on. Drained the same way as
                # an awaited reply, so a multi-message answer to the last beat isn't cut
                # off at whichever chunk happened to have arrived.
                await asyncio.sleep(_POLL_SECONDS)
                await self._drain_reply(run, steward.rest, channel_id, cursor, olisar_id)
        except RunAborted as exc:
            run.error = str(exc)
            log.warning("run %s aborted: %s", run.run_id, exc)
        except Exception as exc:  # noqa: BLE001 — a run must always produce a record
            run.error = f"{type(exc).__name__}: {exc}"
            log.exception("run %s failed", run.run_id)
        finally:
            for speaker in speakers.values():
                await speaker.rest.__aexit__(None, None, None)
            await self._restore_config(restore)

        run.ended_at = now_iso()
        if not run.error:
            run.error = _starvation_error(self._cfg, run)
        return apply_checks(run, scenario)

    # ── setup ─────────────────────────────────────────────────────────────

    async def _name_trigger(self) -> str:
        """The first configured name trigger, so an ``address: name`` beat uses whatever
        this variant actually answers to rather than a hard-coded 'olisar'."""
        try:
            async with Dashboard(self._cfg) as dash:
                triggers = (await dash.get_config()).get("name_triggers") or []
            return str(triggers[0]) if triggers else "olisar"
        except Exception:
            log.warning("couldn't read name triggers; assuming 'olisar'", exc_info=True)
            return "olisar"

    async def _apply_config(self, scenario: Scenario) -> dict:
        """Set the guild config this scenario depends on, returning the previous values.

        Reads the current config first so the restore is to what was actually there, not to
        a default — a scenario running under a variant that changes config must hand that
        config back, not the stock one.
        """
        if not scenario.config and not scenario.proactivity:
            return {}
        previous: dict = {}
        async with Dashboard(self._cfg) as dash:
            if scenario.config:
                current = await dash.get_config()
                previous["config"] = {k: current.get(k) for k in scenario.config if k in current}
                await dash.set_config(**scenario.config)
            if scenario.proactivity:
                current = await dash.get_proactivity()
                previous["proactivity"] = {
                    k: current.get(k) for k in scenario.proactivity if k in current
                }
                await dash.set_proactivity(**scenario.proactivity)
        log.info("scenario config: %s proactivity: %s", scenario.config, scenario.proactivity)
        return previous

    async def _restore_config(self, previous: dict) -> None:
        """Put back whatever the scenario changed.

        In a ``finally``, and never allowed to raise: a scenario that leaks
        ``name_requires_address=false`` into the next one silently invalidates it, and the
        symptom — a later scenario answering something it shouldn't — looks like a
        behaviour regression rather than a harness fault.
        """
        if not previous:
            return
        try:
            async with Dashboard(self._cfg) as dash:
                if previous.get("config"):
                    await dash.set_config(**previous["config"])
                if previous.get("proactivity"):
                    await dash.set_proactivity(**previous["proactivity"])
        except Exception:
            log.error(
                "COULD NOT RESTORE guild config %s — later scenarios in this run are "
                "compromised; re-apply the variant before trusting them", previous,
                exc_info=True,
            )

    async def _prepare_channel(
        self, steward: Steward, scenario: Scenario, olisar_id: int, members: dict
    ) -> int:
        allowed: list[int] = []
        if scenario.channel_private:
            for name in scenario.channel_members:
                if name == "olisar":
                    allowed.append(olisar_id)
                elif name in members:
                    allowed.append(members[name].user_id)
            # The steward must be able to read the channel back, or the run records
            # nothing and reports it as Olisar staying silent.
            allowed.append(await steward.rest.my_id())
            # Every cast member has to be able to post into it.
            allowed += [members[k].user_id for k in scenario.cast if k in members]
        return await steward.ensure_channel(
            scenario.channel_name,
            private=scenario.channel_private,
            members=sorted(set(allowed)),
            topic=scenario.channel_topic,
            recreate=scenario.recreate_channel,
        )

    async def _set_channel_mode(
        self, channel_id: int, mode: str, indexed: bool | None = None
    ) -> None:
        """Tell Olisar what it may do in this channel, and whether to index it.

        Both are sent as separate calls, and both are fatal on failure. That is a
        correction, not caution: these were one call whose errors were logged and
        swallowed, so a channel left at ``off`` produced a run where Olisar said nothing
        and the transcript recorded "FAIL must_reply" — a harness fault wearing the costume
        of a finding, which is the worst thing this harness can produce.

        The mode always works: it writes ``ChannelAllowlist``, keyed by raw channel id.
        Indexing does not, immediately — it needs a ``GuildChannelInfo`` row, and that
        roster is synced by the bot on a 90-second loop (bot/cogs/context_channels.py), so
        a channel the steward created seconds ago is genuinely unknown to it. Hence the
        wait rather than a retry-once.
        """
        async with Dashboard(self._cfg) as dash:
            await dash.set_channels({"channel_id": channel_id, "mode": mode})
        if indexed is not None:
            await self._set_channel_indexed(channel_id, indexed)

    async def _set_channel_indexed(self, channel_id: int, indexed: bool) -> None:
        """Set the channel's search-index flag, waiting for the bot's roster sync.

        Fatal if it never lands. A scenario only asks for this when it matters — every
        message reaches the server-wide search index regardless of author, on a separate
        path from conversational context, so a case asserting Olisar *can't* see something
        depends on this to close the other door. Proceeding without it would quietly test
        something else.
        """
        deadline = time.monotonic() + _ROSTER_SYNC_TIMEOUT
        attempt = 0
        while True:
            try:
                async with Dashboard(self._cfg) as dash:
                    await dash.set_channels({"channel_id": channel_id, "indexed": indexed})
                return
            except DashboardError as exc:
                if exc.status != 404 or time.monotonic() > deadline:
                    raise RunAborted(
                        f"couldn't set indexed={indexed} on the channel: {exc}"
                    ) from exc
                attempt += 1
                if attempt == 1:
                    log.info(
                        "channel not in the bot's roster yet — waiting up to %.0fs for its "
                        "sync before setting indexed=%s", _ROSTER_SYNC_TIMEOUT, indexed,
                    )
                await asyncio.sleep(5.0)

    # ── the beat loop ─────────────────────────────────────────────────────

    async def _play(
        self,
        run: Run,
        speakers: dict[str, _Speaker],
        observer: DiscordRest,
        channel_id: int,
        beat: Beat,
        cursor: int,
        olisar_id: int,
        name_trigger: str,
        deadline: float,
        *,
        seeding: bool = False,
    ) -> int:
        if time.monotonic() > deadline:
            raise RunAborted(f"scenario timeout ({self._cfg.scenario_timeout_seconds:.0f}s)")
        if self._posted >= self._cfg.max_messages_per_scenario:
            raise RunAborted(f"message ceiling ({self._cfg.max_messages_per_scenario}) reached")

        if beat.is_pause:
            waited = 0.0
            while waited < beat.wait:
                await asyncio.sleep(_POLL_SECONDS)
                waited += _POLL_SECONDS
                cursor = await self._collect(run, observer, channel_id, cursor, olisar_id)
            return cursor

        speaker = speakers[beat.speaker]
        text = beat.text or await compose(
            self._model,
            speaker.persona,
            beat=beat.beat,
            transcript=[{"author": t.author, "content": t.content} for t in run.turns],
            channel_topic="",
        )
        if not text:
            log.warning("skipping %s's beat — no line produced", beat.speaker)
            return cursor
        text = _address(text, beat.address, olisar_id, name_trigger)

        gap = self._cfg.min_seconds_between_fleet_messages - (time.monotonic() - self._last_post)
        if gap > 0:
            await asyncio.sleep(gap)
        # Typing first: it paces the channel the way a person does, and it gives Olisar's
        # own typing indicator something to interleave with when a human watches a run.
        await speaker.rest.typing(channel_id)
        await asyncio.sleep(min(1.2 + len(text) / 90.0, 4.0))
        posted = await speaker.rest.send(channel_id, text)
        self._posted += 1
        self._last_post = time.monotonic()
        run.turns.append(
            Turn(
                author=speaker.persona.display_name,
                content=text,
                is_olisar=False,
                author_id=speaker.user_id,
                message_id=int(posted.get("id", 0) or 0),
                at=now_iso(),
            )
        )
        cursor = max(cursor, int(posted.get("id", 0) or 0))

        if seeding:
            return cursor
        if beat.expect_reply:
            return await self._await_reply(run, observer, channel_id, cursor, olisar_id, beat.timeout)
        await asyncio.sleep(_POLL_SECONDS)
        return await self._collect(run, observer, channel_id, cursor, olisar_id)

    async def _await_reply(
        self, run: Run, observer: DiscordRest, channel_id: int, cursor: int,
        olisar_id: int, timeout: float,
    ) -> int:
        """Poll until Olisar says something or the beat's timeout lapses.

        A lapse is recorded as silence rather than raised: "didn't answer when addressed"
        is a finding, and the deterministic ``must_reply`` check is what turns it into a
        failure. Aborting here would throw away the transcript that explains why.
        """
        deadline = time.monotonic() + timeout
        before = len(run.olisar_turns)
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_SECONDS)
            cursor = await self._collect(run, observer, channel_id, cursor, olisar_id)
            if len(run.olisar_turns) > before:
                return await self._drain_reply(run, observer, channel_id, cursor, olisar_id)
        log.info("no reply within %.0fs", timeout)
        return cursor

    async def _drain_reply(
        self, run: Run, observer: DiscordRest, channel_id: int, cursor: int, olisar_id: int
    ) -> int:
        """Keep collecting until Olisar has gone quiet for ``_REPLY_SETTLE_SECONDS``.

        One turn is delivered as the one to three messages the model asked for, each
        preceded by a typing indicator held for roughly as long as it would take to write
        (``bot/replies.py``: ~18 chars/sec, plus a 0.4-1.1s pause between them). So the
        second half of a reply can land ten seconds after the first, and a fixed short wait
        would capture the opening line and nothing else — then hand the judge a truncated
        reply to score as if it were the whole thing.

        The window resets on each new message rather than being a single fixed sleep, so a
        three-part reply is followed to its end without paying the worst case every time.
        """
        last_seen = time.monotonic()
        count = len(run.olisar_turns)
        hard_stop = time.monotonic() + _REPLY_DRAIN_CEILING
        while time.monotonic() - last_seen < _REPLY_SETTLE_SECONDS:
            if time.monotonic() > hard_stop:
                log.warning("reply drain hit its ceiling — the transcript may be partial")
                break
            await asyncio.sleep(_POLL_SECONDS)
            cursor = await self._collect(run, observer, channel_id, cursor, olisar_id)
            if len(run.olisar_turns) > count:
                count = len(run.olisar_turns)
                last_seen = time.monotonic()
        return cursor

    async def _collect(
        self, run: Run, observer: DiscordRest, channel_id: int, cursor: int, olisar_id: int
    ) -> int:
        """Fold everything posted since ``cursor`` into the transcript, skipping the
        emulator messages this runner already recorded at send time."""
        try:
            fetched = await observer.messages(channel_id, after=cursor or None, limit=50)
        except Exception:
            log.warning("polling #%s failed", channel_id, exc_info=True)
            return cursor
        known = {t.message_id for t in run.turns if t.message_id}
        for message in fetched:
            message_id = int(message.get("id", 0) or 0)
            cursor = max(cursor, message_id)
            if message_id in known:
                continue
            author = message.get("author") or {}
            author_id = int(author.get("id", 0) or 0)
            content = message.get("content", "") or ""
            # Tool-posted images arrive with empty content and an attachment; record the
            # fact, or "Olisar generated an image" reads as "Olisar said nothing".
            if not content and message.get("attachments"):
                names = ", ".join(a.get("filename", "file") for a in message["attachments"])
                content = f"[attachment: {names}]"
            if not content:
                continue
            run.turns.append(
                Turn(
                    author=author.get("global_name") or author.get("username") or str(author_id),
                    content=content,
                    is_olisar=author_id == olisar_id,
                    author_id=author_id,
                    message_id=message_id,
                    at=message.get("timestamp", "") or now_iso(),
                )
            )
        return cursor

    async def _latest_message_id(self, observer: DiscordRest, channel_id: int) -> int:
        try:
            recent = await observer.messages(channel_id, limit=1)
        except Exception:
            return 0
        return int(recent[-1]["id"]) if recent else 0


class FastRunner:
    """Replays a scenario's beats against the console's memory-free test chat.

    No Discord, no memory, no proactivity — and therefore no waiting. What survives is the
    prompt itself: persona, operating rules, tool briefing, knowledge base. That is the
    right lane for sweeping variants and for most of the red-team suite, and the wrong lane
    for anything about recall or channel behaviour.
    """

    def __init__(self, cfg: ArenaConfig, model: ModelClient) -> None:
        self._cfg = cfg
        self._model = model

    async def run(self, scenario: Scenario, *, variant: str = "baseline") -> Run:
        run = Run(
            run_id=new_run_id(scenario.id, variant),
            scenario_id=scenario.id,
            lane="fast",
            variant=variant,
            started_at=now_iso(),
        )
        personas = load_personas()
        history: list[dict] = []
        try:
            async with Dashboard(self._cfg) as dash:
                for beat in [*scenario.seed, *scenario.beats]:
                    if beat.is_pause:
                        continue  # nothing acts on its own in this lane
                    persona = personas.get(beat.speaker)
                    text = beat.text or (
                        await compose(
                            self._model, persona, beat=beat.beat,
                            transcript=[{"author": t.author, "content": t.content} for t in run.turns],
                        )
                        if persona
                        else beat.beat
                    )
                    if not text:
                        continue
                    display = persona.display_name if persona else beat.speaker
                    # The test chat takes a flat user/assistant transcript, so speaker
                    # identity is carried in the text. Without it a multi-party scenario
                    # collapses into one interlocutor and stops testing what it meant to.
                    history.append({"role": "user", "content": f"{display}: {text}"})
                    run.turns.append(Turn(author=display, content=text, at=now_iso()))

                    reply = await dash.chat(history)
                    if reply:
                        history.append({"role": "assistant", "content": reply})
                        run.turns.append(
                            Turn(author="Olisar", content=reply, is_olisar=True, at=now_iso())
                        )
        except Exception as exc:  # noqa: BLE001
            run.error = f"{type(exc).__name__}: {exc}"
            log.exception("fast run %s failed", run.run_id)

        run.ended_at = now_iso()
        return apply_checks(run, scenario)


async def execute(cfg: ArenaConfig, scenario: Scenario, *, variant: str = "baseline") -> Run:
    """Run a scenario in whichever lane it declares."""
    model = ModelClient(cfg)
    runner = FastRunner(cfg, model) if scenario.is_fast else LiveRunner(cfg, model)
    return await runner.run(scenario, variant=variant)
