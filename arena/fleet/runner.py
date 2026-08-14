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
from arena.control.dashboard import Dashboard
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

        speakers: dict[str, _Speaker] = {}
        try:
            async with Steward(cfg) as steward:
                channel_id = await self._prepare_channel(steward, scenario, olisar_id, members)
                await self._set_channel_mode(channel_id, scenario.channel_mode)

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
                # commonly lands after the beat loop has moved on.
                await asyncio.sleep(4.0)
                await self._collect(run, steward.rest, channel_id, cursor, olisar_id)
        except RunAborted as exc:
            run.error = str(exc)
            log.warning("run %s aborted: %s", run.run_id, exc)
        except Exception as exc:  # noqa: BLE001 — a run must always produce a record
            run.error = f"{type(exc).__name__}: {exc}"
            log.exception("run %s failed", run.run_id)
        finally:
            for speaker in speakers.values():
                await speaker.rest.__aexit__(None, None, None)

        run.ended_at = now_iso()
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

    async def _set_channel_mode(self, channel_id: int, mode: str) -> None:
        """Tell Olisar what it may do in this channel. Without this a freshly created
        channel is ``off`` and Olisar stays silent no matter what is said in it — the
        single most confusing way for a scenario to 'fail'."""
        try:
            async with Dashboard(self._cfg) as dash:
                await dash.set_channels({"channel_id": channel_id, "mode": mode})
        except Exception:
            log.warning("couldn't set channel mode to %s — Olisar may stay silent", mode,
                        exc_info=True)

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
                # Olisar chunks long replies across several messages; give the rest a beat.
                await asyncio.sleep(2.5)
                return await self._collect(run, observer, channel_id, cursor, olisar_id)
        log.info("no reply within %.0fs", timeout)
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
