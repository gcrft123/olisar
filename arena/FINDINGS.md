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

### Olisar answers questions from a corpus of the same question

**Root cause found, and it is not the prompt.** Queried the arena's own index directly
for a fact the server has never discussed:

```
search_messages("who posted the setup guide")
  0.595  "olisar, who posted the `setup-guide` again? need to check something with them"
  0.565  "olisar, who posted that `setup-guide` again? need to ping them about..."
  0.554  "olisar, who posted the `setup` guide a while back? need the original..."
```

Every top hit is *the question being asked*, from earlier runs. Olisar indexes messages
addressed to it, so a question about X is the best keyword match for a query about X.
The tool then hands back ten restatements of the question under the instruction "skim
these and answer", and Olisar obliges. That is the fabrication: it is not inventing from
nothing, it is summarising the only thing it was given.

Specific to a bot that indexes what is said *to* it, and self-reinforcing — every asking
makes the next search worse.

**A relevance floor cannot fix it.** `kw` is normalised across the returned candidate
set (`(hi - bm25) / (hi - lo)`), so the best candidate always scores 1.0 on keyword
however poor it is in absolute terms. Every query tops out at 0.595 — absent facts and
present ones alike. `MIN_RELEVANCE = 0.30` as shipped moves an absent-fact query from 10
results to 10, and another from 10 to 8. It is honest about intent and useless in effect;
the fused score carries no absolute signal to threshold.

**The obvious fix was tried, measured, and reverted. It made things worse.** Dropping
questions addressed to the bot before ranking (both conditions required: names the bot
*and* reads as a question, so "olisar said the schedule moved to friday" survives) was
A/B'd against keeping them — same scenarios, same judge, interleaved arms, the flag set
as instance env so each arm is a real restart of the same code.

16 runs completed before the process died. Unbalanced, so this is counted, not scored:

| scenario | arm | what Olisar did |
|---|---|---|
| server-fact-social *(absent)* | kept, n=2 | "don't think we have any" / "nothing here… discord-only crew" — **correct, 2/2** |
| server-fact-social *(absent)* | dropped, n=3 | "pretty sure there was a twitter at one point, dead for at least a year" — **invented, 3/3** |
| server-fact-history *(absent)* | kept, n=3 | invented a date in 1 of 3 |
| server-fact-history *(absent)* | dropped, n=4 | invented a date in 3 of 4 — "late 2023", "august 2025", "early last year" |
| server-fact-present *(control)* | both | retrieved the seeded answer, 2/2 each |

**Checked for the confound that would have killed it, and it survives.** Olisar falls down
a seven-model chain as the free tier rate-limits, and the two arms were *not* served
equally: `kept` drew 31% of its calls from the strong end (3.5-flash, flash-latest),
`dropped` only 11%. A stronger model confabulates less, so the confound ran in the same
direction as the result — which is exactly the shape of a finding that is really about
something else.

It isn't. Stratifying by model rather than comparing arms in aggregate, on the lite-only
tier where 14 of the 17 runs sit:

| stratum | kept | dropped |
|---|---|---|
| **lite only** (n=14) | **0/6 invented** | **6/8 invented** |
| touched a strong model (n=3) | 1/2 | 0/1 |

Holding the model constant makes the separation cleaner, not weaker. The strong-model
stratum is too small to carry any weight either way.

Two instrument changes came out of this rather than a retraction. Which model served a run
is now recorded on every run (`served_by`), so this is a column in the data instead of
something the next person has to think to check. And the arm order now alternates on rep
*and* scenario: alternating on rep alone gave every scenario the same A,B,A pattern, which
at three reps put arm A first in six pairs of nine — and going first is worth something
real when the resource depletes monotonically through a session.

**Why the "bug" was load-bearing.** Ten hits that are all people *asking* X is the only
absence signal this retrieval layer emits, and Olisar reads it correctly — it concludes
nobody ever answered, and says so. Filtering the questions out does not leave silence. It
promotes the next tier, which is members speculating, and **speculation reads as evidence
in a way a question never does**. The filter swapped a legible absence signal for a
plausible-looking false one.

Present facts retrieve correctly under either arm, so there was nothing on the other side
of the trade. Shipped default is now off (`OLISAR_SEARCH_DROP_BOT_QUESTIONS=0`); the flag
and the matcher stay, because the balance between question-noise and speculation-noise
could differ on a corpus larger than this one.

**What this leaves.** The ugliness that started the investigation is real but was
misdiagnosed as a retrieval fault. Olisar narrating "i keep hitting your own questions
about it" is the *mechanism* leaking into the reply, and that belongs in the tool briefing
— see `empty-search-offer-action`, which gives the live path the same rule the sandbox
path already has, plus the missing half: offer an action it can actually take.

Making an empty result reachable at the tool layer is still open, and still needs an
absolute signal — the fused score has none (a floor was tried and dropped the correct
answer), so it would need raw bm25 or a real semantic distance. Note `search_message` has
no embedding column and no vector table: this index is keyword-only, and the semantic half
of the hybrid does not apply to it. But it is now a lower priority than it looked, because
the absence signal the corpus already carries turns out to work.

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

## Data integrity: 16% of runs were void

A quota-exhausted Olisar does not go silent. It replies with a canned fallback —
"…my mind just went blank there. mind rephrasing?" — which arrives as an ordinary
Discord message, so nothing downstream could tell it from an answer. A `must_reply`
check counted it as a reply, and the judge graded its prose: one placement-A/B tell
reads *"'my mind just went blank there' reads like an AI performing a human
memory-lapse"*, which is a critique of Olisar's error message.

Audited every run: **133 of 793 with a reply were entirely fallback (16%)**, from
05:50 UTC onward, plus 12 partly. Runs are now marked inconclusive when every Olisar
turn is a fallback, the same treatment as a rate-limited silence.

What survives, checked rather than assumed:

- **The character-not-rules A/B is clean.** All 86 runs in its window (00:29–04:49
  UTC, before the first loop began) contained zero fallbacks in either arm. The one
  established finding stands.
- **The second night's "0 promotions" is weaker than reported.** Rounds r016–r026
  include 17 void runs, concentrated in the last three rounds. Nothing was promoted,
  so no bad variant got through — but "nothing cleared the bar" partly reflects
  rounds comparing error messages, not only a correctly-set bar.

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
