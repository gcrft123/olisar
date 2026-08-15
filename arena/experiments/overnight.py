"""Run rounds unattended until a deadline, and never lose what they produced.

Written for a session nobody is watching, which changes the requirements in three ways:

**Nothing may live only in stdout.** An hour of live runs was already reduced to unusable
once by a ``tail`` on the console. Every round appends a line to a journal on disk before
anything else happens, so the record survives a truncated terminal, a killed process, or a
context that has moved on.

**A single failure must not end the night.** One round hitting a Discord error, a model
timeout, or an unparseable proposal costs that round and nothing more. The loop catches per
round, records what happened, and continues.

**A spent budget is a reason to change backends, not to stop.** When the provider a role
uses runs out, the role moves to one with room. That is a real cost: a judge swap changes
what every score means, so the journal marks the boundary and the next round re-calibrates.
Scores either side of it are not comparable and nothing downstream should average across it.

The loop deliberately does not chase a single hypothesis. It cycles the block it proposes
against, so a night spent failing to improve the operating rules still tries the tool
briefing and the proactive note.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from arena.config import RUNS_DIR, ArenaConfig
from arena.eval.judge import Judge
from arena.experiments import loop as loop_mod
from arena.model import DIALOGUE, JUDGE, BudgetExhausted, ModelClient

log = logging.getLogger("arena.overnight")

JOURNAL = RUNS_DIR / "_overnight.jsonl"

# Cycled so a night isn't spent entirely on one block. Ordered by how much evidence
# there is that each is worth attacking.
BLOCKS = ("operating_rules", "tools_note", "proactive_note", "follow_up_note")

# Where a role goes when its provider is spent. Gemini is deliberately absent: it is the
# instance-under-test's quota, and borrowing it would starve the thing being measured.
FAILOVER = {
    "claude": ("grok", {"judge": "grok-4.6", "dialogue": "grok-4.6"}),
    "grok": ("claude", {"judge": "sonnet", "dialogue": "haiku"}),
}


@dataclass
class Entry:
    """One journal line. Written before the next round starts."""

    at: str
    round: int = 0
    block: str = ""
    champion: str = ""
    challenger: str = ""
    promoted: bool = False
    stopped: str = ""
    note: str = ""
    budget: dict = field(default_factory=dict)

    def write(self) -> None:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(self)) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _failover(model: ModelClient, cfg: ArenaConfig) -> str:
    """Move any role whose provider is spent onto one with room. Returns a note, or ""."""
    moved = []
    for role in (JUDGE, DIALOGUE):
        if model.has_budget(role):
            continue
        kind = model.describe()[role]["backend"]
        target = FAILOVER.get(kind)
        if not target:
            continue
        new_kind, models = target
        model.switch(role, new_kind, models[role])
        moved.append(f"{role}: {kind} -> {new_kind}")
    return "; ".join(moved)


async def run_overnight(
    cfg: ArenaConfig,
    *,
    hours: float = 8.0,
    tags: list[str] | None = None,
    lane: str = "fast",
    max_rounds: int = 200,
) -> dict:
    """Run rounds until the deadline, the round cap, or every backend is spent."""
    deadline = time.monotonic() + hours * 3600
    model = ModelClient(cfg)
    started = _now()
    Entry(at=started, note=f"start: {hours}h, lane={lane}, tags={tags}",
          budget=model.usage()).write()

    rounds = promoted = failed = 0
    needs_calibration = True

    while time.monotonic() < deadline and rounds < max_rounds:
        note = _failover(model, cfg)
        if note:
            # A judge swap invalidates comparability, so the next round re-calibrates and
            # the boundary is on the record rather than inferred later from a cost ledger.
            needs_calibration = True
            Entry(at=_now(), note=f"backend failover — {note}", budget=model.usage()).write()

        if not model.has_budget(JUDGE):
            Entry(at=_now(), note="every backend is spent; stopping",
                  budget=model.usage()).write()
            break

        block = BLOCKS[rounds % len(BLOCKS)]
        try:
            record = await loop_mod.run_round(
                cfg, tags=tags, lane=lane, block=block,
                model=model, calibrate=needs_calibration,
            )
            needs_calibration = False
            rounds += 1
            promoted += bool(record.promoted)
            if record.stopped:
                failed += 1
            Entry(
                at=_now(), round=record.number, block=block, champion=record.champion,
                challenger=record.challenger, promoted=record.promoted,
                stopped=record.stopped, budget=model.usage(),
            ).write()
            log.info("round %d (%s): %s", record.number, block,
                     "PROMOTED " + record.challenger if record.promoted
                     else (record.stopped or "kept champion"))
        except BudgetExhausted as exc:
            # Not fatal: the next pass through the loop fails the role over.
            Entry(at=_now(), block=block, stopped=str(exc)[:300],
                  budget=model.usage()).write()
            await asyncio.sleep(5)
        except Exception as exc:  # noqa: BLE001 — one bad round must not end the night
            failed += 1
            rounds += 1
            log.exception("round failed")
            Entry(at=_now(), block=block, stopped=f"{type(exc).__name__}: {exc}"[:300],
                  budget=model.usage()).write()
            await asyncio.sleep(10)

    summary = {
        "started": started,
        "ended": _now(),
        "rounds": rounds,
        "promoted": promoted,
        "failed": failed,
        "champion": loop_mod.read_champion(),
        "budget": model.usage(),
        "journal": str(JOURNAL),
    }
    Entry(at=summary["ended"], note=f"done: {rounds} rounds, {promoted} promoted, "
          f"{failed} failed", budget=model.usage()).write()
    return summary


def read_journal(limit: int = 40) -> list[dict]:
    """The last ``limit`` journal lines — how to see what happened without a terminal."""
    if not JOURNAL.is_file():
        return []
    lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


async def calibrate_now(cfg: ArenaConfig, model: ModelClient | None = None) -> dict:
    """Calibration on demand, for after a failover."""
    return await Judge(cfg, model or ModelClient(cfg)).calibrate()


__all__ = ["run_overnight", "read_journal", "calibrate_now", "JOURNAL", "BLOCKS"]


def journal_path() -> Path:
    return JOURNAL
