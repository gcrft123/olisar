"""The regression gate that a naturalness win is not allowed to buy.

Olisar's guardrails live in one block of text — ``OPERATING_RULES`` in ``olisar/persona.py``
— covering prompt injection from untrusted content, privacy across the DM/public boundary,
not bluffing, and the 2000-character ceiling. Every edit that makes Olisar warmer, blunter,
more opinionated, or less hedging pulls against that block, and the erosion is invisible in
ordinary conversation: the scenarios that look best are the ones where it has stopped being
careful.

So this suite is a *gate*, not a score. Every case is tagged ``redteam``, asserts with
deterministic substring checks rather than a judge, and must pass in full. A variant that
scores beautifully on naturalness and fails one injection case is rejected — there is no
weighted trade-off, because the trade-off is the failure mode.

Cases live alongside every other scenario in ``arena/scenarios/``. Adding one is adding a
file; nothing here needs to change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from arena.config import ArenaConfig
from arena.eval.transcript import Run
from arena.fleet.runner import execute
from arena.scenarios.schema import Scenario, select

log = logging.getLogger("arena.redteam")

REDTEAM_TAG = "redteam"


@dataclass
class GateResult:
    passed: bool = True
    total: int = 0
    failures: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)

    def summary(self) -> str:
        if self.errors:
            return (
                f"INCONCLUSIVE — {len(self.errors)}/{self.total} case(s) could not run. "
                f"A gate that didn't execute is not a pass."
            )
        if self.passed:
            return f"PASS — {self.total}/{self.total} red-team cases held"
        names = ", ".join(f["scenario"] for f in self.failures)
        return f"FAIL — {len(self.failures)}/{self.total} case(s) broke: {names}"


def cases(lane: str = "") -> list[Scenario]:
    return select(tags=[REDTEAM_TAG], lane=lane)


async def run_gate(cfg: ArenaConfig, *, variant: str = "baseline", lane: str = "") -> GateResult:
    """Run every red-team case and report whether the variant may proceed.

    An execution failure is kept separate from a check failure on purpose. A case that
    couldn't run tells you nothing about the guardrails, and folding it into "passed"
    would let a broken harness wave a bad variant through — the exact direction an
    error should never fail in.
    """
    scenarios = cases(lane)
    result = GateResult(total=len(scenarios))
    if not scenarios:
        result.passed = False
        result.errors.append(
            {"scenario": "(none)", "error": "no scenarios tagged 'redteam' — the gate is empty"}
        )
        return result

    for scenario in scenarios:
        run = await execute(cfg, scenario, variant=variant)
        run.save()
        result.runs.append(run)
        if run.error:
            result.errors.append({"scenario": scenario.id, "error": run.error})
            result.passed = False
            log.error("red-team case %s could not run: %s", scenario.id, run.error)
            continue
        broken = [c for c in run.checks if not c.passed]
        if broken:
            result.passed = False
            result.failures.append(
                {
                    "scenario": scenario.id,
                    "title": scenario.title,
                    "run_id": run.run_id,
                    "checks": [{"name": c.name, "detail": c.detail} for c in broken],
                    "replies": [t.content for t in run.olisar_turns],
                }
            )
            log.error(
                "red-team case %s FAILED: %s",
                scenario.id,
                "; ".join(f"{c.name} ({c.detail})" for c in broken),
            )
        else:
            log.info("red-team case %s held", scenario.id)
    return result
