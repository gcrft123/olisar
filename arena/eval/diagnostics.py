"""Checks that run on every live run, whether or not a scenario asked for them.

``Checks`` in a scenario file are assertions about *that* scenario: this reply must
mention double elimination, this one must not reply at all. They are opt-in, and that is
correct for anything scenario-specific.

It is wrong for properties that should hold everywhere. Four separate faults were spotted
by a human reading Discord and were invisible to the whole harness, not because the checks
disagreed but because no scenario had thought to declare them — an emulator narrating its
own reasoning into the channel, Olisar addressing the same person twice in one message,
Olisar answering in one long paragraph where a member would have sent three lines. A
property nobody remembers to assert is a property nobody measures.

Two kinds live here, and the distinction matters more than the checks do:

**Harness integrity** — the run's *input* was wrong, so its output grades nothing. Same
category as ``only_fallbacks``. These fail loudly, because a quiet one is worse than no
measurement at all: it looks like data.

**Behaviour** — Olisar did something a member wouldn't. These are reported as failures
too, but only where the right answer doesn't depend on the scenario. Fragmentation is
recorded as a *metric* rather than a check for exactly that reason: three messages is
right for a throwaway line and wrong for a considered answer, and a check that fires on
both would train the wrong lesson.
"""

from __future__ import annotations

import collections
import re

from arena.eval.transcript import CheckResult, Run
from arena.scenarios.schema import Scenario

# Live only. The fast lane folds [[break]] before the text is stored (``strip_breaks``),
# so a fragmentation number from it measures the harness, not the bot. Reporting one
# anyway is how 524 of 527 turns were once counted as "never fragmented" when they were
# structurally incapable of it.
LIVE_LANE = "live"


# ── harness integrity ──────────────────────────────────────────────────────────

# A reasoning model told to output "only the message" will still narrate its plan first.
# One emulator posted "olisar, Checking rook's voice and what the setup guide actually is
# so the message stays specific.olisar who posted the setup guide..." into the channel.
# Structured output fixed the known cause; this catches the next one, because the failure
# is silent — the scenario's input becomes something the scenario never specified, and
# Olisar is then graded on answering it.
#
# Every alternative names *the act of writing a message*. Bare verbs were tried first and
# are useless: "checking" matched "so i just stopped checking it" and "let me" matches half
# of ordinary chat. The signal is not that the model is thinking, it is that the thing it
# is thinking about is the reply it is composing.
_META_LANGUAGE = re.compile(
    r"\b(?:"
    r"(?:the|my|this|their|his|her)\s+(?:message|reply|response)\s+(?:should|needs|stays|will|has)"
    r"|so\s+the\s+(?:message|reply|response)\b"
    r"|\w+['’]s\s+voice\b"
    r"|stay(?:ing|s)?\s+in\s+(?:character|voice|persona)"
    r"|as\s+(?:the|this)\s+(?:user|persona|character)"
    r"|i['’]ll\s+(?:keep|make|write|craft|phrase)\s+(?:it|this|the)"
    r"|writing\s+(?:as|in)\s+\w+['’]s\b"
    r")",
    re.IGNORECASE,
)


def scripted_text(scenario: Scenario | None) -> set[str]:
    """Beat text the scenario hard-codes, so the harness never composed it.

    Necessary, not defensive. The red-team suite puts adversarial instructions in the
    *input* on purpose — "stay in character no matter what" is the jailbreak being tested,
    and flagging it as an emulator malfunction voided 25 stored runs that were working
    exactly as designed. Same distinction ``evaluate_checks`` already makes for
    ``must_not_contain``: a forbidden string in the input is the test, not the failure.
    """
    if scenario is None:
        return set()
    return {
        beat.text.strip()
        for beat in list(scenario.seed) + list(scenario.beats)
        if beat.text.strip()
    }


def emulator_leaked_reasoning(run: Run, scripted: set[str] | None = None) -> CheckResult | None:
    """An emulator narrating its own composition into Discord voids the run."""
    scripted = scripted or set()
    for turn in run.turns:
        if turn.is_olisar or turn.content.strip() in scripted:
            continue
        match = _META_LANGUAGE.search(turn.content)
        # A bare match is not enough — people do say "let me check". The tell is the
        # model's plan sitting *in front of* the real message, which shows up as the
        # persona's address appearing after it rather than at the start.
        if match and match.start() > 0 and len(turn.content) > 80:
            return CheckResult(
                "emulator_clean",
                False,
                f"{turn.author} appears to have narrated its reasoning into the channel: "
                f"{turn.content[:120]!r}",
            )
    return None


_ADDRESS = re.compile(r"^\s*([a-z][a-z0-9_.\-]{1,20})\s*[,:]", re.IGNORECASE)


def doubled_address(run: Run, scripted: set[str] | None = None) -> CheckResult | None:
    """Olisar (or an emulator) greeting the same person twice in one message.

    The observed shape was an emulator prefixing a name onto text that already opened with
    it — "olisar, olisar who posted the setup guide". It reads as a stutter, and on the
    emulator side it means the scenario asked a question nobody would have typed.
    """
    # Only a participant's name counts. "alright, alright. don't let it go to your head"
    # is a person repeating a word for emphasis, and reading it as a stutter is how this
    # check first reported a false finding against natural writing.
    names = {t.author.strip().lower() for t in run.turns if t.author.strip()} | {"olisar"}
    for turn in run.turns:
        match = _ADDRESS.match(turn.content)
        if not match:
            continue
        name = match.group(1).lower()
        if name not in names:
            continue
        rest = turn.content[match.end():].lstrip()
        if rest.lower().startswith(name):
            who = "olisar" if turn.is_olisar else turn.author
            return CheckResult(
                "no_doubled_address",
                False,
                f"{who} addressed {name!r} twice in one message: {turn.content[:100]!r}",
            )
    return None


# ── behaviour ──────────────────────────────────────────────────────────────────


def messages_per_reply(run: Run) -> float:
    """Olisar's messages divided by the number of times it was prompted to speak.

    A member answering a question sends one line, or three short ones. Olisar sending
    exactly one message every single time is the single most legible tell that a bot is
    composing rather than chatting — and it is invisible to a judge scoring one reply at a
    time, because each individual message is fine.
    """
    if run.lane != LIVE_LANE or not run.turns:
        return 0.0
    bursts, previous_was_olisar = 0, False
    for turn in run.turns:
        if turn.is_olisar and not previous_was_olisar:
            bursts += 1
        previous_was_olisar = turn.is_olisar
    return round(len(run.olisar_turns) / bursts, 2) if bursts else 0.0


def _fragmentation_metric(run: Run, scripted: set[str] | None = None) -> CheckResult | None:
    """Recorded, never failed. See the module docstring for why.

    ``passed`` is True unconditionally so this can't gate a promotion; the number lives in
    ``detail`` where a report can aggregate it across runs. A single run carries no signal
    about fragmentation — the question is only meaningful over a variant's whole set.
    """
    if run.lane != LIVE_LANE or not run.olisar_turns:
        return None
    return CheckResult("msgs_per_reply", True, f"{messages_per_reply(run):.2f}")


def _plausible_lengths(run: Run, scripted: set[str] | None = None) -> CheckResult | None:
    """Emulator messages far past what a person types in Discord.

    ``arena.fleet.dialogue`` caps at 320 characters after generation, so anything over it
    means the cap was bypassed — a hard-coded scenario line, or a path that skipped
    ``_tidy``. Olisar is excluded: its length is the thing under test, not a harness fault.
    """
    scripted = scripted or set()
    for turn in run.turns:
        if turn.is_olisar or turn.content.strip() in scripted:
            continue
        if len(turn.content) > 400:
            return CheckResult(
                "emulator_length",
                False,
                f"{turn.author} sent {len(turn.content)} chars; the fleet cap is 320, so "
                f"this bypassed dialogue._tidy",
            )
    return None


# Which Gemini model actually answered. Olisar falls back down a seven-model chain as the
# free tier rate-limits, so two arms of the same A/B can be served by different models —
# and a stronger model confabulates less, which is the very thing several scenarios
# measure. This was found by accident in a completed A/B whose arms drew 31% and 11% of
# their calls from the strong end of the chain. Recorded per run so it is a column in the
# data rather than something the next person has to think to check.
_SERVED_BY = re.compile(r"gemini (\S+) served conversation")


def serving_models(run: Run) -> collections.Counter:
    """Model name -> calls, from the instance log slice saved beside the transcript."""
    lines = run._olisar_log
    if lines is None:
        path = run.directory() / "olisar.log"
        if not path.is_file():
            return collections.Counter()
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return collections.Counter(m for line in lines for m in _SERVED_BY.findall(line))


def _model_mix(run: Run, scripted: set[str] | None = None) -> CheckResult | None:
    """Recorded, never failed — which model served this run is context, not a verdict."""
    counts = serving_models(run)
    if not counts:
        return None
    mix = " ".join(f"{name}x{n}" for name, n in counts.most_common())
    return CheckResult("served_by", True, mix)


def diagnose(run: Run, scenario: Scenario | None = None) -> list[CheckResult]:
    """Every always-on check, in the order a reader wants them: integrity first.

    Returns only what fired, plus the metrics. A run with nothing to say adds one metric
    line and no failures.
    """
    if run.error:
        # A run that already failed to complete has nothing to diagnose, and reporting
        # emulator faults on a truncated transcript invents findings.
        return []
    scripted = scripted_text(scenario)
    results = []
    for check in (emulator_leaked_reasoning, doubled_address, _plausible_lengths,
                  _fragmentation_metric, _model_mix):
        result = check(run, scripted)
        if result is not None:
            results.append(result)
    return results


def voided_by(results: list[CheckResult]) -> str:
    """The integrity failure that makes a run unscoreable, if there is one.

    Behaviour findings are not grounds for discarding a run — that is the finding. Only a
    bad *input* is.
    """
    integrity = {"emulator_clean", "emulator_length"}
    for result in results:
        if result.name in integrity and not result.passed:
            return result.detail
    return ""
