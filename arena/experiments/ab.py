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
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from arena.config import RUNS_DIR, ArenaConfig
from arena.eval.judge import Judge, Scores
from arena.eval.transcript import Run
from arena.experiments import variants
from arena.fleet.runner import execute
from arena.model import ModelClient
from arena.scenarios.schema import Scenario

log = logging.getLogger("arena.ab")

AB_DIR = RUNS_DIR / "_ab"


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
        for index, scenario in enumerate(scenarios):
            # Alternate on rep *and* scenario, not rep alone. Going first is worth
            # something real — the free-tier chain depletes monotonically through a
            # session, so the earlier arm of each pair gets the stronger model — and
            # alternating on rep alone gives every scenario the same A,B,A pattern. At
            # three reps that put arm A first in six pairs of nine, and it showed: in one
            # A/B the first-positioned arm drew 31% of its calls from the strong models
            # against the other's 11%, in the same direction as the result. Adding the
            # scenario index staggers the pattern so the imbalance cancels across them.
            order = (variant_a, variant_b) if (rep + index) % 2 == 0 else (variant_b, variant_a)
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
    report = _report(arms[variant_a], arms[variant_b], scenarios)
    _save(report, variant_a, variant_b)
    return report


def _save(report: dict, variant_a: str, variant_b: str) -> Path:
    """Persist the report next to the runs it came from.

    Added after an hour of live runs was reduced to unusable by a ``tail`` on the console
    output: the report existed only on stdout, so truncating stdout destroyed it. Results
    that took real time and real money to produce should not live only in a terminal
    buffer.
    """
    AB_DIR.mkdir(parents=True, exist_ok=True)
    # No timestamp from inside the run — keep it deterministic and let the caller's
    # filesystem ordering do the rest; a re-run of the same pair overwrites deliberately.
    path = AB_DIR / f"{variant_a}--vs--{variant_b}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("report saved to %s", path)
    return path


def _spread(arm: Arm) -> dict[str, float]:
    """Per-dimension standard deviation within one arm — the noise floor.

    Without this a report is just two means and a subtraction, and a delta smaller than
    the run-to-run spread reads as a result. In the first null-result A/B the same variant
    scored 3.33 and 1.67 on the same scenario; against that, a +0.38 aggregate delta is not
    a finding, and saying so requires having measured it.
    """
    totals: dict[str, list[float]] = {}
    for score in arm.graded():
        for key, value in score.dimensions.items():
            totals.setdefault(key, []).append(value)
    out = {}
    for key, values in totals.items():
        if len(values) < 2:
            continue
        mean = sum(values) / len(values)
        out[key] = (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5
    return out


def _verdict(deltas: dict[str, float], a_sd: dict[str, float], b_sd: dict[str, float],
             n: int) -> dict[str, str]:
    """Read each delta against the noise, not against zero.

    The bar is one standard error of the difference. It is a smell test, not statistics —
    at these sample sizes nothing here is significant in any formal sense, and the point is
    to stop a number that looks like an effect from being reported as one.
    """
    out = {}
    for key, delta in deltas.items():
        pooled = ((a_sd.get(key, 0.0) ** 2 + b_sd.get(key, 0.0) ** 2) / 2) ** 0.5
        se = pooled / max(n, 1) ** 0.5 if pooled else 0.0
        if not se:
            out[key] = "no spread measured"
        elif abs(delta) < se:
            out[key] = f"inside the noise (±{se:.2f}) — not a result"
        elif abs(delta) < 2 * se:
            out[key] = f"suggestive, under 2 SE (±{se:.2f}) — needs more reps"
        else:
            out[key] = f"clears 2 SE (±{se:.2f})"
    return out


def _report(a: Arm, b: Arm, scenarios: list[Scenario]) -> dict:
    a_means, b_means = a.means(), b.means()
    a_sd, b_sd = _spread(a), _spread(b)
    deltas = {
        key: round(b_means.get(key, 0.0) - value, 2)
        for key, value in sorted(a_means.items())
    }
    read = _verdict(deltas, a_sd, b_sd, min(len(a.graded()), len(b.graded())))
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
            "sd": {k: round(v, 2) for k, v in a_sd.items()},
        },
        "b": {
            "variant": b.variant, "graded": len(b.graded()),
            "inconclusive": b.inconclusive, "means": {k: round(v, 2) for k, v in b_means.items()},
            "sd": {k: round(v, 2) for k, v in b_sd.items()},
        },
        "delta_b_minus_a": deltas,
        "read": read,
        "per_scenario": per_scenario,
        "a_tells": a.tells(),
        "b_tells": b.tells(),
    }
