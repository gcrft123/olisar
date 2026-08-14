"""Scoring a run, and comparing two.

The absolute pass grades a single transcript on the dimensions with a defensible ground
truth. The pairwise pass is the one that matters for naturalness, and it is deliberately
fussy: each pair is judged twice with the sides swapped, and a judge that flips its answer
when the order flips is recorded as a tie rather than believed. That single measure removes
most of the position bias that makes casual LLM-as-judge comparisons unreproducible.

``calibrate`` runs the pairwise judge against hand-written human replies. If the judge
cannot reliably pick the human out of a lineup, its verdicts on two model variants are
noise, and the harness reports that instead of a leaderboard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from arena.config import ArenaConfig
from arena.eval import rubric
from arena.eval.transcript import Run
from arena.model import JUDGE, ModelClient

log = logging.getLogger("arena.judge")


@dataclass
class Scores:
    """Absolute dimension scores (0-4) for one run."""

    run_id: str
    scenario_id: str
    variant: str
    dimensions: dict[str, float] = field(default_factory=dict)
    worst_tell: str = ""
    note: str = ""
    checks_passed: bool = True

    @property
    def mean(self) -> float:
        return sum(self.dimensions.values()) / len(self.dimensions) if self.dimensions else 0.0


@dataclass
class Verdict:
    """One order-controlled pairwise comparison."""

    winner: str  # "a" | "b" | "tie"
    confidence: float = 0.0
    tell: str = ""
    flipped: bool = False  # the two orderings disagreed; recorded as a tie


def _context_and_reply(run: Run) -> tuple[str, str]:
    """The conversation leading up to Olisar's replies, and the replies themselves."""
    replies = [t.content for t in run.olisar_turns]
    context = "\n".join(t.render() for t in run.turns if not t.is_olisar)
    return context, "\n".join(replies)


class Judge:
    def __init__(self, cfg: ArenaConfig, model: ModelClient | None = None) -> None:
        self._cfg = cfg
        self._model = model or ModelClient(cfg)

    async def score(self, run: Run, dimensions: list[str] | None = None) -> Scores:
        """Absolute grading. A run with an error, or with no reply at all, is not sent to
        the judge — there is nothing to grade, and a fabricated score would pollute the
        comparison it feeds."""
        scores = Scores(
            run_id=run.run_id,
            scenario_id=run.scenario_id,
            variant=run.variant,
            checks_passed=all(c.passed for c in run.checks),
        )
        if run.error:
            scores.note = f"not graded: {run.error}"
            return scores
        if not run.olisar_turns:
            scores.note = "not graded: Olisar never replied"
            return scores

        payload = await self._model.generate_json(
            rubric.absolute_prompt(run.render(), dimensions or list(rubric.DEFAULT_ABSOLUTE)),
            system=rubric.ABSOLUTE_SYSTEM,
            role=JUDGE,
            schema=rubric.ABSOLUTE_SCHEMA,
        )
        if not payload:
            scores.note = "not graded: the judge returned nothing parseable"
            return scores
        for key in rubric.BY_KEY:
            if key in payload:
                try:
                    scores.dimensions[key] = max(0.0, min(4.0, float(payload[key])))
                except (TypeError, ValueError):
                    continue
        scores.worst_tell = str(payload.get("worst_tell", ""))[:300]
        scores.note = str(payload.get("note", ""))[:300]
        return scores

    async def compare(self, context: str, reply_a: str, reply_b: str) -> Verdict:
        """One pairwise judgement, run in both orders.

        Agreement across the swap is the whole point: a judge that picks whichever reply
        came second is measuring position, and averaging enough of those produces a
        confident number that means nothing.
        """
        forward = await self._model.generate_json(
            rubric.pairwise_prompt(context, reply_a, reply_b),
            system=rubric.PAIRWISE_SYSTEM,
            role=JUDGE,
            schema=rubric.PAIRWISE_SCHEMA,
        )
        reverse = await self._model.generate_json(
            rubric.pairwise_prompt(context, reply_b, reply_a),
            system=rubric.PAIRWISE_SYSTEM,
            role=JUDGE,
            schema=rubric.PAIRWISE_SCHEMA,
        )
        first = str(forward.get("winner", "tie")).strip().lower()
        second = str(reverse.get("winner", "tie")).strip().lower()
        # In the reversed run, "a" refers to reply_b.
        second_mapped = {"a": "b", "b": "a"}.get(second, "tie")
        confidence = (
            float(forward.get("confidence", 0) or 0) + float(reverse.get("confidence", 0) or 0)
        ) / 2
        tell = str(forward.get("tell", "") or reverse.get("tell", ""))[:300]

        if first == second_mapped and first in ("a", "b"):
            return Verdict(winner=first, confidence=confidence, tell=tell)
        if first == "tie" and second_mapped == "tie":
            return Verdict(winner="tie", confidence=confidence, tell=tell)
        return Verdict(winner="tie", confidence=confidence, tell=tell, flipped=True)

    async def compare_runs(self, run_a: Run, run_b: Run) -> Verdict:
        """Pairwise-compare two runs of the *same* scenario."""
        if run_a.scenario_id != run_b.scenario_id:
            raise ValueError(
                f"can't compare different scenarios ({run_a.scenario_id} vs {run_b.scenario_id}) "
                f"— a pairwise verdict only means something on identical input"
            )
        if run_a.error or run_b.error or not run_a.olisar_turns or not run_b.olisar_turns:
            return Verdict(winner="tie", tell="one side has no reply to compare")
        context, reply_a = _context_and_reply(run_a)
        _, reply_b = _context_and_reply(run_b)
        return await self.compare(context, reply_a, reply_b)

    async def _tier(self, pairs: list[tuple[str, str, str]]) -> dict:
        """Run one calibration tier: for each (prompt, human, bot), can the judge pick the
        human? Each pair goes through the full order-controlled comparison, so an order
        flip counts as a miss here — a coin flip is not a correct answer."""
        correct = 0
        flips = 0
        details = []
        for prompt, human, bot in pairs:
            verdict = await self.compare(f"someone: {prompt}", human, bot)
            hit = verdict.winner == "a"
            correct += hit
            flips += verdict.flipped
            details.append(
                {
                    "prompt": prompt,
                    "picked_human": hit,
                    "flipped": verdict.flipped,
                    "tell": verdict.tell[:160],
                }
            )
        total = len(pairs)
        return {
            "correct": correct,
            "total": total,
            "accuracy": correct / total if total else 0.0,
            "order_flips": flips,
            "details": details,
        }

    async def calibrate(self) -> dict:
        """Can this judge pick a human reply out of a lineup — twice over?

        **floor** pairs each human reply against a cartoonish rewrite. Failing it means the
        judge is broken. Passing it is weak evidence: any competent model aces it, so on its
        own it says almost nothing about ranking two *good* variants.

        **sensitivity** pairs each human reply against a reply a well-tuned Olisar would
        plausibly produce. This is the comparison the loop actually makes every round. A
        judge at chance here will still return confident verdicts; they just won't track
        quality, and the loop will wander.

        ``trustworthy`` gates on the floor, so the promotion rule is unchanged. Sensitivity
        is reported alongside it, because it is what tells you whether a narrow margin means
        anything — and it is the number to look at before widening ``WIN_MARGIN``.
        """
        floor_pairs = [
            (
                prompt,
                human,
                f"Great question! {human[0].upper()}{human[1:]}. "
                f"Let me know if you'd like me to explain further!",
            )
            for prompt, human in rubric.HUMAN_ANCHORS
        ]
        floor = await self._tier(floor_pairs)
        sensitivity = await self._tier(list(rubric.SUBTLE_ANCHORS))

        return {
            # 0.8 is a judgement call, not a derived threshold: below it, a judge is wrong
            # often enough that a 55/45 split between two variants is indistinguishable
            # from the judge's own error rate.
            "trustworthy": floor["accuracy"] >= 0.8,
            "accuracy": floor["accuracy"],
            "correct": floor["correct"],
            "total": floor["total"],
            "order_flips": floor["order_flips"],
            "sensitivity": sensitivity["accuracy"],
            # Six pairs is a small sample, so this is a smell test rather than a statistic:
            # 4/6 or better means the judge is picking up structural tells, 3/6 is chance.
            "sensitive": sensitivity["accuracy"] >= 0.6,
            "floor_detail": floor["details"],
            "sensitivity_detail": sensitivity["details"],
        }
