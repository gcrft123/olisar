"""The scenario file format.

A scenario is a *versioned, replayable input*. That is the whole reason this exists as a
file format rather than the agent improvising conversations: a prompt change can only be
called an improvement if the thing it improved on is held constant. Two runs of a freely
improvised chat differ in the chat, so the comparison measures nothing.

Two lanes:

``fast``  — no Discord. The beats are replayed against ``POST /api/admin/sandbox/chat``,
            which uses the live persona, knowledge base and tools but no memory and no
            Discord actions. Cheap, deterministic, and where bulk sweeps and most of the
            red-team suite belong.
``live``  — the real thing. A real channel in the arena guild, emulator bots posting as
            members, Olisar replying through its full pipeline with memory, recall,
            proactivity and tools. Slower and noisier, and the only lane that can observe
            the behaviours the fast lane excludes by construction.

Beats are executed in order. Each is one of:

    {"speaker": "mika", "beat": "ask where the schedule lives"}   generated in-voice
    {"speaker": "mika", "text": "literally in the pins"}          verbatim
    {"wait": 45}                                                  let Olisar act on its own
    {"speaker": "dtrain", "beat": "...", "address": "name"}       guarantee a reply

``address`` makes the message trigger Olisar deliberately: ``name`` prefixes a configured
name trigger, ``mention`` prefixes a real ``<@id>``. Without it a beat is ordinary channel
chatter, which is what proactivity scenarios need.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arena.config import SCENARIOS_DIR

LANES = ("fast", "live")
ADDRESS_MODES = (None, "name", "mention")


class ScenarioError(RuntimeError):
    pass


@dataclass(frozen=True)
class Beat:
    speaker: str = ""
    beat: str = ""
    text: str = ""
    address: str | None = None
    wait: float = 0.0
    # Block until Olisar posts something (or the per-beat timeout lapses). Distinct from
    # `wait`, which always sleeps the full duration: this returns as soon as it sees a
    # reply, so an addressed exchange doesn't pay a fixed penalty per turn.
    expect_reply: bool = False
    timeout: float = 45.0

    @property
    def is_pause(self) -> bool:
        return not self.speaker and self.wait > 0


@dataclass(frozen=True)
class Checks:
    """Deterministic assertions, evaluated without a model.

    These are the checks that must not be delegated to a judge: whether a reply happened,
    whether a forbidden phrase appears, whether a secret leaked. A judge is for questions
    of quality; a substring is a substring, and grading one with an LLM only adds variance.
    """

    must_reply: bool = False
    must_not_reply: bool = False
    max_reply_chars: int = 0
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    # Case-insensitive by default: "As An AI" is the same failure as "as an ai".
    case_sensitive: bool = False


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    lane: str = "live"
    channel_name: str = "general"
    channel_private: bool = False
    channel_members: list[str] = field(default_factory=list)
    channel_mode: str = "both"
    channel_topic: str = ""
    recreate_channel: bool = True
    cast: list[str] = field(default_factory=list)
    seed: list[Beat] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)
    checks: Checks = field(default_factory=Checks)
    # Rubric dimensions the judge scores this scenario on. Empty means "the defaults".
    rubric: list[str] = field(default_factory=list)
    notes: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def is_fast(self) -> bool:
        return self.lane == "fast"


def _beat(raw: Any, where: str) -> Beat:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: each beat must be an object")
    address = raw.get("address")
    if address not in ADDRESS_MODES:
        raise ScenarioError(f"{where}: address must be one of name/mention, got {address!r}")
    beat = Beat(
        speaker=str(raw.get("speaker", "")).strip().lower(),
        beat=str(raw.get("beat", "")),
        text=str(raw.get("text", "")),
        address=address,
        wait=float(raw.get("wait", 0.0)),
        expect_reply=bool(raw.get("expect_reply", False)),
        timeout=float(raw.get("timeout", 45.0)),
    )
    if not beat.speaker and beat.wait <= 0:
        raise ScenarioError(f"{where}: a beat needs either a speaker or a positive wait")
    if beat.speaker and not (beat.beat or beat.text):
        raise ScenarioError(f"{where}: {beat.speaker} has neither 'beat' nor 'text'")
    return beat


def _checks(raw: Any) -> Checks:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ScenarioError("checks must be an object")
    checks = Checks(
        must_reply=bool(raw.get("must_reply", False)),
        must_not_reply=bool(raw.get("must_not_reply", False)),
        max_reply_chars=int(raw.get("max_reply_chars", 0)),
        must_contain=[str(s) for s in raw.get("must_contain", [])],
        must_not_contain=[str(s) for s in raw.get("must_not_contain", [])],
        case_sensitive=bool(raw.get("case_sensitive", False)),
    )
    if checks.must_reply and checks.must_not_reply:
        raise ScenarioError("checks: must_reply and must_not_reply are contradictory")
    return checks


def parse(raw: dict, *, source: str = "<inline>") -> Scenario:
    for required in ("id", "title"):
        if not raw.get(required):
            raise ScenarioError(f"{source}: missing required field {required!r}")
    lane = str(raw.get("lane", "live"))
    if lane not in LANES:
        raise ScenarioError(f"{source}: lane must be one of {', '.join(LANES)}")

    channel = raw.get("channel") or {}
    beats = [_beat(b, f"{source} beat {i}") for i, b in enumerate(raw.get("beats", []))]
    if not beats:
        raise ScenarioError(f"{source}: a scenario needs at least one beat")

    scenario = Scenario(
        id=str(raw["id"]),
        title=str(raw["title"]),
        lane=lane,
        channel_name=str(channel.get("name", "general")),
        channel_private=bool(channel.get("private", False)),
        channel_members=[str(m) for m in channel.get("members", [])],
        channel_mode=str(channel.get("mode", "both")),
        channel_topic=str(channel.get("topic", "")),
        recreate_channel=bool(channel.get("recreate", True)),
        cast=[str(c).strip().lower() for c in raw.get("cast", [])],
        seed=[_beat(b, f"{source} seed {i}") for i, b in enumerate(raw.get("seed", []))],
        beats=beats,
        checks=_checks(raw.get("checks")),
        rubric=[str(r) for r in raw.get("rubric", [])],
        notes=str(raw.get("notes", "")),
        tags=[str(t) for t in raw.get("tags", [])],
    )

    speakers = {b.speaker for b in [*scenario.seed, *scenario.beats] if b.speaker}
    unknown = speakers - set(scenario.cast)
    if unknown:
        raise ScenarioError(
            f"{source}: speaker(s) {', '.join(sorted(unknown))} are not in the cast "
            f"({', '.join(scenario.cast) or 'empty'})"
        )
    if scenario.channel_private and not scenario.channel_members:
        raise ScenarioError(
            f"{source}: a private channel must list 'members' — use 'olisar' to include the "
            f"bot, or omit it to test that Olisar can't see the channel"
        )
    return scenario


def load_file(path: Path) -> Scenario:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"{path.name}: {exc}") from exc
    return parse(raw, source=path.name)


def load_all(directory: Path | None = None) -> dict[str, Scenario]:
    directory = directory or SCENARIOS_DIR
    out: dict[str, Scenario] = {}
    for path in sorted(directory.glob("*.json")):
        scenario = load_file(path)
        if scenario.id in out:
            raise ScenarioError(f"duplicate scenario id {scenario.id!r} in {path.name}")
        out[scenario.id] = scenario
    return out


def load(scenario_id: str, directory: Path | None = None) -> Scenario:
    scenarios = load_all(directory)
    if scenario_id not in scenarios:
        known = ", ".join(sorted(scenarios)) or "(none)"
        raise ScenarioError(f"unknown scenario {scenario_id!r}. Known: {known}")
    return scenarios[scenario_id]


def select(tags: list[str] | None = None, lane: str = "", directory: Path | None = None) -> list[Scenario]:
    """Scenarios matching every given tag and (optionally) a lane."""
    out = []
    for scenario in load_all(directory).values():
        if lane and scenario.lane != lane:
            continue
        if tags and not set(tags).issubset(set(scenario.tags)):
            continue
        out.append(scenario)
    return sorted(out, key=lambda s: s.id)
