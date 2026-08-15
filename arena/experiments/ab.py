"""A controlled A/B between two hand-authored variants.

The loop (``arena.experiments.loop``) tests what a model proposes, which is right for
open-ended search and wrong for a specific question. When you have a hypothesis — *this*
instruction fails *because* of where it sits — you want one variable changed deliberately
and everything else held, and you want to author both sides yourself.

Two design choices do the real work:

**Arms are interleaved, never run in blocks.** A live run depends on free-tier quota that
degrades as the session goes on, so running all of arm A and then all of arm B hands the
second arm worse conditions and calls the difference an effect. Alternating spreads that
across both.

**Inconclusive runs are retried, not scored.** A rate-limited run is not a data point
(see ``arena.fleet.runner._starvation_error``); counting one as a zero would let quota
exhaustion decide the experiment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from arena.config import ArenaConfig
from arena.eval.judge import Judge, Scores
from arena.eval.transcript import Run
from arena.experiments import variants
from arena.fleet.runner import execute
from arena.model import ModelClient
from arena.scenarios.schema import Scenario

log = logging.getLogger("arena.ab")


@dataclass
class Arm:
    """One side of the comparison."""

    variant: str
    runs: list[Run] = field(default_factory=list)
    scores: list[Scores] = field(default_factory=list)
    inconclusive: int = 0

    def graded(self) -> list[Scores]:
        return [s for s in self.scores if s.dimensions]

    def means(self) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for score in self.graded():
            for key, value in score.dimensions.items():
                totals.setdefault(key, []).append(value)
        return {k: sum(v) / len(v) for k, v in totals.items() if v}

    def by_scenario(self, scenario_id: str) -> list[Scores]:
        return [s for s in self.graded() if s.scenario_id == scenario_id]

    def tells(self) -> list[str]:
        return [s.worst_tell for s in self.graded() if s.worst_tell]


async def _one(
    cfg: ArenaConfig, variant_name: str, scenario: Scenario, judge: Judge, retries: int
) -> tuple[Run | None, Scores | None]:
    """Apply a variant and run a scenario, retrying while the result is inconclusive."""
    await variants.apply(cfg, variants.load(variant_name))
    for attempt in range(retries + 1):
        run = await execute(cfg, scenario, variant=variant_name)
        run.save()
        if not run.error:
            return run, await judge.score(run, scenario.rubric or None)
        log.warning("%s / %s: %s", variant_name, scenario.id, run.error)
        if attempt < retries:
            # Long enough for a 120s model park to clear; anything shorter just burns
            # the retry on the same exhausted chain.
            log.info("waiting 130s for quota before retrying")
            await asyncio.sleep(130)
    return None, None


async def run_ab(
    cfg: ArenaConfig,
    variant_a: str,
    variant_b: str,
    scenarios: list[Scenario],
    *,
    reps: int = 2,
    settle: float = 45.0,
    retries: int = 1,
) -> dict:
    """Run the full matrix, alternating arms, and report both arms' scores."""
    model = ModelClient(cfg)
    judge = Judge(cfg, model)
    arms = {variant_a: Arm(variant_a), variant_b: Arm(variant_b)}

    total = len(scenarios) * reps * 2
    done = 0
    for rep in range(reps):
        for scenario in scenarios:
            # Alternate which arm goes first across reps, so neither one is
            # systematically the warmer or the more rate-limited of the pair.
            order = (variant_a, variant_b) if rep % 2 == 0 else (variant_b, variant_a)
            for name in order:
                done += 1
                log.info("[%d/%d] %s / %s (rep %d)", done, total, name, scenario.id, rep + 1)
                run, score = await _one(cfg, name, scenario, judge, retries)
                if run is None or score is None:
                    arms[name].inconclusive += 1
                else:
                    arms[name].runs.append(run)
                    arms[name].scores.append(score)
                if done < total:
                    await asyncio.sleep(settle)

    # Restore whichever arm was the incumbent, so the instance isn't left mid-experiment.
    await variants.apply(cfg, variants.load(variant_a))
    return _report(arms[variant_a], arms[variant_b], scenarios)


def _report(a: Arm, b: Arm, scenarios: list[Scenario]) -> dict:
    a_means, b_means = a.means(), b.means()
    deltas = {
        key: round(b_means.get(key, 0.0) - value, 2)
        for key, value in sorted(a_means.items())
    }
    per_scenario = []
    for scenario in scenarios:
        a_s = [s.mean for s in a.by_scenario(scenario.id)]
        b_s = [s.mean for s in b.by_scenario(scenario.id)]
        per_scenario.append(
            {
                "scenario": scenario.id,
                a.variant: [round(x, 2) for x in a_s],
                b.variant: [round(x, 2) for x in b_s],
                "delta": round(
                    (sum(b_s) / len(b_s) if b_s else 0) - (sum(a_s) / len(a_s) if a_s else 0), 2
                ) if a_s and b_s else None,
            }
        )
    return {
        "a": {
            "variant": a.variant, "graded": len(a.graded()),
            "inconclusive": a.inconclusive, "means": {k: round(v, 2) for k, v in a_means.items()},
        },
        "b": {
            "variant": b.variant, "graded": len(b.graded()),
            "inconclusive": b.inconclusive, "means": {k: round(v, 2) for k, v in b_means.items()},
        },
        "delta_b_minus_a": deltas,
        "per_scenario": per_scenario,
        "a_tells": a.tells(),
        "b_tells": b.tells(),
    }
