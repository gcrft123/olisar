# Findings

What the arena has actually established, at the strength the evidence supports. Run
records are gitignored, so this is the durable version.

Every number here is a judge score on 0–4, averaged over interleaved runs. Where a
delta is smaller than one standard error it is recorded as *not a result*, because
reporting it otherwise is how a loop convinces itself it is making progress.

---

## Established

### Shorter beats the checklist

Replacing Olisar's 29-bullet tone notes (2,198 chars) with a four-line character
sketch (460 chars) improved every measured dimension and regressed none.

| | baseline | character-not-rules | delta |
|---|---|---|---|
| helpfulness | 2.60 ± 0.89 | 3.00 ± 0.00 | +0.40 |
| restraint | 2.80 ± 0.77 | 3.07 ± 0.59 | +0.27 |
| brevity | 2.25 ± 0.91 | 2.45 ± 0.51 | +0.20 |

n=20 per arm, four fast-lane scenarios, five reps, arms interleaved. Red-team gate
7/7 — the variant touches only the operator-editable tone notes and leaves
`OPERATING_RULES` alone. The spread narrowed in every dimension, so the shorter
prompt is also the more consistent one.

**Two caveats.** `helpfulness` has a standard deviation of exactly zero — all
twenty runs scored 3.0, which is the judge anchoring rather than a distribution,
and a zero-variance arm understates the pooled standard error that let it "clear
2 SE". And *naturalness*, which is what the hypothesis was about, was **not
measured**: it is scored only pairwise and `arena ab` does absolute scoring only.
So this establishes the cut cost nothing and helped three dimensions; it does not
yet establish that it sounds more human.

**Operator-facing form:** write a character, not a style guide. Who they are and
how they type, in about four lines. This is the operator-editable layer, so it is
advice an admin can apply today.

### The test chat advertised tools it did not have

`_SANDBOX_CORE` declares two tools (`query_knowledge`, `web_search`) while the full
briefing for twelve was injected, including "any question about THIS server … uses
`search_messages`" — a tool the lane does not provide. An instruction that cannot be
obeyed produces a confabulation, not a refusal: the model invented the lookup it was
told to perform ("not finding anything in the logs"). Fixed by rendering the
briefing per tool set. A defect in what operators are shown, not only in the
harness.

---

## Not supported

### Placement — where an instruction sits

Moving "any question about THIS server … uses `search_messages`" verbatim from
`TOOLS_NOTE` into `OPERATING_RULES` (the block headed "these always take priority")
changed nothing: accuracy +0.17 at n=6, well inside the noise. Net prompt delta was
−9 characters, so only placement varied.

Corroborating evidence against placement: the same class of instruction fails in the
tone notes too. "💀 is funny-dying" is present and violated, in a different block and
the other half of the prompt entirely.

### The null-result line

`OPERATING_RULES` said "If you're rate-limited or a tool is unavailable, say so
briefly and answer from what you know" — written for an unavailable tool, and
seemingly generalising to a search that returned nothing. Rewriting it to make a null
result an answer in its own right moved accuracy +0.38 at n=8, against a pooled
standard error of ±0.45. **Inside the noise.**

The control held (`server-fact-present`, 3.67 vs 3.83), so the change did not trade
fabrication for uselessness — it just did not reliably buy anything either.

**Adverse finding:** told "never describe a search you didn't run", the model complied
by citing the search it *did* run as evidence for an invented date — *"according to
the search … May 2024"*. The instruction made the fabrication more authoritative
rather than absent.

---

## Open

### Olisar fabricates facts about its own server

The largest unresolved defect. Asked about the server's history, social accounts, or
who posted what, Olisar averages **~1.2–2.5 / 4 on accuracy** and invents handles,
dates, attendance and server norms.

The logs settle one thing: it **is** calling `search_messages`, repeatedly and with
sensible queries, and getting hits back. Tool routing works. The failure is one step
later — it cannot report that a search found nothing, so it fills the gap.

Two prompt-level interventions have now failed to move this. That is worth treating
as evidence about the shape of the problem rather than as two unlucky variants.

### Instructions that exist and are not followed

Three, in three different blocks:

| instruction | block | layer |
|---|---|---|
| don't announce you're using a tool | `TOOLS_NOTE` | baked-in |
| server questions use `search_messages` | `TOOLS_NOTE` | baked-in |
| 💀 is funny-dying | tone notes | operator-editable |

Failing across blocks and across layers points at dilution — a long prompt, not a
misplaced line — which is consistent with the character-not-rules result.

---

## Two unattended nights: 26 rounds, 0 real promotions

Round 1–14 promoted twice; both were noise and were reverted (see *Method*). Rounds
16–27, under a corrected promotion bar, promoted **nothing**. Every challenger the
loop proposed against `operating_rules` or `tools_note` either failed the gate or
landed inside the noise floor.

That is the clearest evidence yet for the dilution reading. Three targeted
hypotheses (placement, the null-result line, and ~20 model-authored rewrites) have
now failed to move the numbers, while the one change that *did* move them was a
deletion. Prompt rewording appears to be near its ceiling here.

**The proposer can't reliably rewrite a long block.** `tools_note` (2,020 chars) came
back unusable in 4 of 6 attempts; `operating_rules` (1,419) never failed. And the
successful ones were **122%, 124%, 122%** of the original despite the proposal prompt
saying "Do not make it longer than the original. Shorter is better" — a fourth
instance of an explicit instruction not landing, this time in the harness's own
prompt. Mitigated with a retry; the real fix is to propose an edit rather than a
full rewrite.

**The red-team gate was flaky.** `rt-fake-authority` held eight times and broke twice
across one night, and each case ran exactly once. A marginal case sampled once
falsely rejects good variants — tolerable — but by the same token passes a genuinely
broken one most of the time, which is not. The gate now runs each case three times
and fails if any rep fails, biased toward rejection on purpose.

## Notes on method

Findings that cost real runs to learn:

- **Run-to-run variance is the dominant term.** The same variant, same scenario, same
  input scored 3.33 and then 1.67. At n=2 a per-scenario delta means nothing. Prefer
  more reps on fewer scenarios.
- **Interleave the arms.** Free-tier quota degrades over a session; running all of A
  then all of B hands the second arm worse conditions and calls the difference an
  effect.
- **A rate-limited silence is not a decision.** It passes a `must_not_reply` check for
  entirely the wrong reason. Runs where Olisar said nothing while its model chain was
  exhausted are marked inconclusive, not scored.
- **The judge misses things a person catches.** Three operator notes — "wdym" over
  "think about what? i'm lost lol", emoji used as punctuation, forum register in chat
  — were all scored clean by the judge beforehand. Human anchors are the one input the
  harness cannot generate for itself.
- **Five of the bugs found so far were in the instrument**, not in Olisar. Phantom
  tools, an over-tightened accuracy dimension, a swallowed channel-setup error, a
  permissive schema letting the judge volunteer dimensions, and a report that lived
  only in stdout.
