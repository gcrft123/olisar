"""What a run produced, and the checks that need no model to decide.

A run is stored as a directory under ``arena/runs/<run_id>/``: the transcript, the metadata
(which scenario, which variant, when), and the slice of Olisar's own log covering the run.
The log matters as much as the transcript — "Olisar didn't reply" has a dozen causes, and
the difference between a rate limit, a role gate, a channel mode and a genuinely bad
decision is in the log, not in the silence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from arena.config import RUNS_DIR
from arena.scenarios.schema import Checks, Scenario

log = logging.getLogger("arena.transcript")


@dataclass
class Turn:
    author: str
    content: str
    is_olisar: bool = False
    author_id: int = 0
    message_id: int = 0
    at: str = ""

    def render(self) -> str:
        return f"{self.author}: {self.content}"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Run:
    """One scenario execution under one variant."""

    run_id: str
    scenario_id: str
    lane: str
    variant: str = "baseline"
    turns: list[Turn] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    started_at: str = ""
    ended_at: str = ""
    # Populated when the run couldn't complete — a Discord error, a timeout, an exhausted
    # budget. A run with an error is never scored: a judge grading a truncated transcript
    # produces a number that looks like a quality signal and is a plumbing failure.
    error: str = ""

    @property
    def olisar_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.is_olisar]

    @property
    def ok(self) -> bool:
        return not self.error and all(c.passed for c in self.checks)

    def render(self) -> str:
        return "\n".join(t.render() for t in self.turns)

    def directory(self) -> Path:
        return RUNS_DIR / self.run_id

    def save(self, olisar_log: list[str] | None = None) -> Path:
        directory = self.directory()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "run.json").write_text(
            json.dumps(asdict(self), indent=2), encoding="utf-8"
        )
        if olisar_log:
            (directory / "olisar.log").write_text("\n".join(olisar_log), encoding="utf-8")
        return directory


def load_run(run_id: str) -> Run:
    path = RUNS_DIR / run_id / "run.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["turns"] = [Turn(**t) for t in raw.get("turns", [])]
    raw["checks"] = [CheckResult(**c) for c in raw.get("checks", [])]
    return Run(**raw)


def list_runs(scenario_id: str = "", variant: str = "") -> list[str]:
    """Run ids, newest first, optionally filtered."""
    if not RUNS_DIR.is_dir():
        return []
    out = []
    for directory in sorted(RUNS_DIR.iterdir(), reverse=True):
        meta = directory / "run.json"
        if not meta.is_file():
            continue
        if scenario_id or variant:
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if scenario_id and raw.get("scenario_id") != scenario_id:
                continue
            if variant and raw.get("variant") != variant:
                continue
        out.append(directory.name)
    return out


def new_run_id(scenario_id: str, variant: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{scenario_id}-{variant}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluate_checks(run: Run, checks: Checks) -> list[CheckResult]:
    """Run the deterministic assertions over a completed transcript.

    Every check reads only Olisar's turns. A ``must_not_contain`` that matched an
    emulator's own message would be scoring the harness, not the bot — and the red-team
    cases deliberately put forbidden strings in the *input*, so this distinction is the
    difference between the suite working and the suite always failing.
    """
    results: list[CheckResult] = []
    replies = run.olisar_turns
    joined = "\n".join(t.content for t in replies)
    haystack = joined if checks.case_sensitive else joined.lower()

    def normalise(needle: str) -> str:
        return needle if checks.case_sensitive else needle.lower()

    if checks.must_reply:
        results.append(
            CheckResult("must_reply", bool(replies), "" if replies else "Olisar never replied")
        )
    if checks.must_not_reply:
        results.append(
            CheckResult(
                "must_not_reply",
                not replies,
                "" if not replies else f"Olisar replied {len(replies)}x when it shouldn't have",
            )
        )
    if checks.max_reply_chars:
        longest = max((len(t.content) for t in replies), default=0)
        results.append(
            CheckResult(
                "max_reply_chars",
                longest <= checks.max_reply_chars,
                f"longest reply {longest} chars (limit {checks.max_reply_chars})",
            )
        )
    for needle in checks.must_contain:
        results.append(
            CheckResult(
                f"must_contain:{needle[:40]}",
                normalise(needle) in haystack,
                "" if normalise(needle) in haystack else "absent from every reply",
            )
        )
    for needle in checks.must_not_contain:
        present = normalise(needle) in haystack
        results.append(
            CheckResult(
                f"must_not_contain:{needle[:40]}",
                not present,
                "found in a reply" if present else "",
            )
        )
    return results


def apply_checks(run: Run, scenario: Scenario) -> Run:
    run.checks = evaluate_checks(run, scenario.checks)
    return run
