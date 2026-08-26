"""Aggregating a variant's results, and comparing two variants honestly.

The comparison rules encoded here are the ones that keep an autonomous loop from
convincing itself it is making progress:

- **The red-team gate is not a score.** It is a precondition. A variant that fails it does
  not enter the comparison at any weighting.
- **Pairwise naturalness only counts scenario-by-scenario.** Comparing variant A's run of
  one scenario against variant B's run of a different one measures the scenarios.
- **Ties count as ties.** A judge that flipped its answer when the order flipped produced
  no information, and rounding those toward the challenger is how a loop drifts.
- **A challenger has to clear a margin, not merely lead.** With a handful of scenarios per
  round, a one-win lead is inside the noise, so ``compare`` requires a net margin of at
  least two before it reports a winner.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from arena.config import RUNS_DIR
from arena.eval.judge import Scores, Verdict

SCORECARD_DIR = RUNS_DIR / "_scorecards"

# How many net pairwise wins a challenger must hold to be called better.
#
# This has to scale with the number of comparisons. Under the null — two variants that are
# actually identical — the net margin is a random walk, so its typical size grows like the
# square root of the comparison count. A fixed 2 is a sane bar over five scenarios and a
# rubber stamp over twelve, which is exactly how an overnight run promoted two variants
# whose only change could not affect the lane they were measured in.
WIN_MARGIN = 2


def win_margin(comparisons: int) -> int:
    """The net pairwise margin a challenger must clear, given how many were made."""
    if comparisons <= 0:
        return WIN_MARGIN
    return max(WIN_MARGIN, math.ceil(1.5 * math.sqrt(comparisons)))


@dataclass
class Scorecard:
    """Everything known about one variant after a round."""

    variant: str
    created_at: str = ""
    gate_passed: bool = False
    gate_summary: str = ""
    scores: list[Scores] = field(default_factory=list)
    judge_trustworthy: bool = True

    @property
    def dimension_means(self) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for score in self.scores:
            for key, value in score.dimensions.items():
                totals.setdefault(key, []).append(value)
        return {k: sum(v) / len(v) for k, v in totals.items() if v}

    @property
    def mean(self) -> float:
        means = self.dimension_means
        return sum(means.values()) / len(means) if means else 0.0

    @property
    def checks_pass_rate(self) -> float:
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.checks_passed) / len(self.scores)

    def tells(self) -> list[str]:
        """The bot-like habits the judge kept naming — the shortlist a prompt revision
        should actually target, rather than a number to move."""
        seen: dict[str, int] = {}
        for score in self.scores:
            if score.worst_tell:
                seen[score.worst_tell] = seen.get(score.worst_tell, 0) + 1
        return [tell for tell, _ in sorted(seen.items(), key=lambda kv: -kv[1])]

    def save(self) -> Path:
        SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
        path = SCORECARD_DIR / f"{self.variant}.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path


def load_scorecard(variant: str) -> Scorecard | None:
    path = SCORECARD_DIR / f"{variant}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["scores"] = [Scores(**s) for s in raw.get("scores", [])]
    return Scorecard(**raw)


def new_scorecard(variant: str) -> Scorecard:
    return Scorecard(variant=variant, created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


@dataclass
class Comparison:
    """The outcome of putting a challenger against the incumbent."""

    champion: str
    challenger: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    flips: int = 0
    verdict: str = "inconclusive"  # "challenger" | "champion" | "inconclusive"
    reason: str = ""
    absolute_delta: dict[str, float] = field(default_factory=dict)

    @property
    def margin(self) -> int:
        return self.wins - self.losses


def compare(
    champion: Scorecard,
    challenger: Scorecard,
    verdicts: dict[str, Verdict],
) -> Comparison:
    """Decide whether the challenger replaces the champion.

    ``verdicts`` maps scenario id to a pairwise verdict where ``"a"`` is the champion and
    ``"b"`` the challenger — the caller must keep that orientation, because a silently
    inverted mapping produces a loop that walks steadily backwards while reporting wins.
    """
    result = Comparison(champion=champion.variant, challenger=challenger.variant)

    if not challenger.gate_passed:
        result.verdict = "champion"
        result.reason = f"challenger failed the red-team gate: {challenger.gate_summary}"
        return result

    for verdict in verdicts.values():
        result.flips += verdict.flipped
        if verdict.winner == "b":
            result.wins += 1
        elif verdict.winner == "a":
            result.losses += 1
        else:
            result.ties += 1

    champion_means = champion.dimension_means
    challenger_means = challenger.dimension_means
    result.absolute_delta = {
        key: round(challenger_means.get(key, 0.0) - value, 2)
        for key, value in champion_means.items()
    }

    if not challenger.judge_trustworthy:
        result.verdict = "inconclusive"
        result.reason = (
            "the judge failed calibration — it could not reliably pick a human reply out "
            "of a lineup, so its naturalness verdicts carry no signal this round"
        )
        return result

    # A challenger that got worse at the measurable things doesn't get in on style. Half a
    # point on a 0-4 scale is a real drop, not rounding.
    regressions = {k: v for k, v in result.absolute_delta.items() if v <= -0.5}
    if regressions:
        result.verdict = "champion"
        result.reason = (
            "challenger regressed on "
            + ", ".join(f"{k} ({v:+.2f})" for k, v in sorted(regressions.items()))
        )
        return result

    needed = win_margin(len(verdicts))
    if result.margin >= needed:
        result.verdict = "challenger"
        result.reason = (
            f"net +{result.margin} on pairwise naturalness over {len(verdicts)} "
            f"comparisons (needed ±{needed}) with no regressions"
        )
    elif result.margin <= -needed:
        result.verdict = "champion"
        result.reason = f"challenger lost by {abs(result.margin)} (needed ±{needed})"
    else:
        result.verdict = "inconclusive"
        result.reason = (
            f"margin of {result.margin:+d} over {len(verdicts)} comparisons is inside the "
            f"noise floor (need ±{needed}); {result.ties} tie(s), {result.flips} order-flip(s)"
        )
    return result
