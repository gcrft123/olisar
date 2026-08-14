# The Olisar arena

A live Discord testbed for iterating on Olisar's behaviour: a second Olisar instance, a real
test server, a fleet of bots that play members, and an evaluation loop that decides whether a
prompt change was actually an improvement.

It exists to answer two different questions with one harness:

1. **How should Olisar's baked-in prompt be written?** The operating rules, tool briefing and
   proactive note that ship in the source. Improving these ships to everyone.
2. **How should an operator write their custom instructions?** The persona and tone notes an
   admin edits in the console. Improving these ships as advice.

Named `arena` because "sandbox" is taken twice over: `olisar/sandbox` is the QuickJS engine
that runs marketplace extensions, and `POST /api/admin/sandbox/chat` is the console's
memory-free test chat (which this harness uses as its fast lane).

---

## One-time setup

### 1. Create a test Discord server

A brand-new server, not one with real people in it. Enable Developer Mode
(User Settings → Advanced), then right-click the server → **Copy Server ID**.

### 2. Create the Discord applications

At <https://discord.com/developers/applications>. You need **three kinds**:

| Application | How many | Portal toggles | Invite permissions |
|---|---|---|---|
| **Arena Olisar** — the instance under test | 1 | Message Content Intent, Server Members Intent | the same scopes your real Olisar uses |
| **Steward** — creates channels/roles, reads transcripts | 1 | Server Members Intent | Administrator |
| **Emulators** — one per persona | up to 6 | *none* | Send Messages, Read Message History, View Channels |

For each: **Bot → Reset Token → Copy**. Then **OAuth2 → URL Generator**, scope `bot`, tick the
permissions above, open the URL, and invite it to the test server.

The emulators need no privileged intents because the harness never opens a gateway
connection for them — it posts over REST and reads the channel back by polling.

> **Do not reuse your production Olisar's token or server.** The arena creates and deletes
> channels, rewrites the persona, and wipes memory between experiments.

### 3. Fill in `.env.arena`

```bash
cp .env.arena.example .env.arena
```

Then check your work:

```bash
uv run python -m arena doctor
```

It prints every missing piece with the exact fix. Keep running it until it says `ready.`

### 4. Resolve the fleet and start up

```bash
uv run python -m arena fleet resolve
```

This maps each emulator token to its Discord user id and caches them. Olisar has to be
*told* those ids — otherwise it treats the emulators as ordinary bots and ignores every word
they say (see [olisar/peers.py](../olisar/peers.py)). Then:

```bash
uv run python -m arena up
```

---

## The two lanes

|  | fast | live |
|---|---|---|
| Where | `POST /api/admin/sandbox/chat` | a real channel in the test server |
| Speed | seconds | minutes |
| Exercises | persona, operating rules, tool briefing, knowledge base | all of that, plus memory, recall, proactivity, Discord action tools, channel modes, permissions |
| Excludes | memory, recall, proactivity, Discord actions | nothing |
| Use for | variant sweeps, the red-team gate | everyday behaviour, edge cases, anything emergent |

A scenario declares its lane. Most red-team cases are `fast` because they're about the prompt
and need to run cheaply on every variant; the everyday scenarios are `live` because what
they test does not exist in the fast lane.

## Where the harness's own model calls go

Two roles, each independently pointed at the Claude CLI or Gemini. Both default to Claude,
which leaves the free-tier Gemini quota entirely to the instance under test:

| role | what it does | default | cost |
|---|---|---|---|
| `dialogue` | the emulators' chat lines | `claude` / `haiku` | ~$0.001 and ~3s per line |
| `judge` | scores and verdicts | `claude` / `sonnet` | ~$0.009 per call |

```bash
uv run python -m arena models --test     # show both roles, and prove each one works
```

Rough shape of a loop round over 8 scenarios: **$0.60–0.80**, of which calibration (22 judge
calls) is ~$0.20. `ARENA_CLAUDE_DAILY_USD` caps it; `arena status` shows what's left.

Pointing a role back at Gemini is one env var (`ARENA_JUDGE_BACKEND=gemini`), but re-run
`arena calibrate` if you change the judge — it is what every verdict rests on.

The Claude backend shells out to `claude -p`. Four details do the real work, and all four
are covered by tests because a silent regression in any of them is expensive:

- `--safe-mode` — no CLAUDE.md, skills, plugins, hooks, or MCP. Without it the harness
  inherits this repo's own agent configuration, and the emulators start writing like a
  coding assistant that has read the codebase.
- `--tools ""` — no tool schemas in the request. Worth ~22k cached input tokens per call:
  measured at **$0.047/line with tools, $0.005 without**, for identical output.
- `MAX_THINKING_TOKENS=0` (dialogue only) — a two-word chat message doesn't need 800
  thinking tokens. Takes a line from **$0.005 / 10s to $0.0009 / 1.8s**. Deliberately left
  on for the judge, which does benefit.
- `--json-schema` — the API enforces the shape, so structured output stops depending on the
  model's willingness to skip a code fence.

> `claude -p` uses whatever the CLI is logged in with. On a subscription, arena runs draw
> down the same usage limits as your interactive work. Set `ANTHROPIC_API_KEY` to bill API
> credits instead.

## Everyday commands

```bash
uv run python -m arena status                      # process, health, quota, fleet
uv run python -m arena chat "hey, where's the schedule?"   # fast lane, one turn
uv run python -m arena run newcomer-basics --judge  # one scenario, scored
uv run python -m arena redteam                     # the guardrail gate
uv run python -m arena logs --grep "trigger="      # what the instance actually did
uv run python -m arena restart                     # deploy a source change
```

Managing the server:

```bash
uv run python -m arena guild snapshot
uv run python -m arena guild channel arena-events --topic "event planning"
uv run python -m arena guild channel arena-staff --private --members mika rook olisar
uv run python -m arena guild reset                 # delete everything the arena made
```

`guild reset` only removes channels prefixed `arena-` and roles suffixed `(arena)`. Anything
you created by hand is left alone.

## The iterate loop

```bash
uv run python -m arena loop --rounds 3 --tags everyday
```

Each round: calibrate the judge → measure the champion → propose a challenger aimed at the
tells the judge kept naming → **red-team gate** → measure the challenger on the same
scenarios → pairwise-compare → promote or discard.

```bash
uv run python -m arena report      # scorecards, the champion, and how to land it
```

### Landing a win

A promoted variant lives in `arena/variants/` and is applied through an override file. That
is a lab instrument, not a shipping mechanism — a variant that wins and stays there has
improved nothing for anyone running Olisar. `arena variant show <name>` prints exactly which
source constant to replace:

| override key | source of truth |
|---|---|
| `operating_rules` | `olisar/persona.py :: OPERATING_RULES` |
| `tools_note` | `olisar/pipeline.py :: TOOLS_NOTE` |
| `proactive_note` | `olisar/proactivity.py :: PROACTIVE_NOTE` |

After landing it, delete the variant and re-run `arena redteam` against the source.

---

## How measurement is kept honest

**Naturalness is only judged head-to-head.** Absolute "rate this 1-10 for how human it
sounds" has no anchor, so the score tracks the judge's mood. Worse, a model optimised
against it learns a *different* register of slop — "oh nice", "haha", performed
casualness — and scores well while sounding like nobody. So two replies to the identical
conversation are compared directly, in both orders, and a judge that flips its answer when
the order flips is recorded as a tie.

**The judge is calibrated before it is believed, at two difficulties.** `arena calibrate`
makes it pick hand-written human replies out of a lineup — twice.

- **floor**: human vs. cartoonish slop ("Great question! … Let me know if you'd like me to
  explain further!"). Below 80% the loop stops rather than reporting a leaderboard. But
  passing is weak evidence: any competent model aces it.
- **sensitivity**: human vs. a reply a *well-tuned* Olisar would plausibly produce — short,
  lowercase, no obvious tells, but structurally off (an empathy preface, restating the
  question, agreeable non-specificity). This is the comparison the loop actually makes every
  round. A judge at chance here still returns confident verdicts; they just don't track
  quality.

Claude Sonnet currently scores **5/5 floor, 6/6 sensitivity, zero order flips**, and names
the structural tell in each case — which is also what feeds the next round's proposal.

**The red-team gate is a precondition, not a score.** Every push toward warmer, blunter, less
hedging pulls against the guardrails, and the erosion is invisible in ordinary conversation:
the transcripts that read best are the ones where Olisar stopped being careful. A challenger
that fails one injection case is rejected regardless of how well it scored.

**A challenger must clear a margin.** On a handful of scenarios a one-win lead is inside the
noise, so promotion requires a net margin of two with no regression on the absolute
dimensions.

---

## Limits worth knowing before you hit them

**Free-tier Gemini.** ~10 RPM on the top chat model with a 7-model fallback chain
([olisar/gemini/models.py](../olisar/gemini/models.py)). This is why the harness's own model
calls are split off (see below) — left on Gemini they compete with the instance under test,
starving Olisar of exactly the quota whose absence then gets recorded as a bad reply. The
harness enforces `ARENA_DAILY_CALL_BUDGET` on its Gemini calls and `ARENA_CLAUDE_DAILY_USD`
on its Claude spend; both are hard stops. Check `arena status` before a long run.

**Bot-to-bot DMs are blocked by Discord.** A scenario can observe that Olisar *called*
`send_dm` (in its log and the audit trail) but not the delivered message. DM-content
behaviour has to be tested from your own account, or on the fast lane.

**Turn-taking is scripted, never emergent.** Emulators speak when the scenario says so. Left
to react freely they converge on a two-party loop with Olisar that burns the day's quota and
teaches nothing. Three governors enforce it: a message ceiling, a minimum gap, and a
wall-clock timeout.

**The fleet is bots.** Never automate a real user account to simulate members — that's a
Discord ToS violation and gets accounts terminated. Every emulator here is a registered bot
application, which is entirely within the rules.

---

## Layout

```
arena/
  cli.py              every command; the agent's entrypoint
  config.py           .env.arena, and the environment the instance is launched with
  discord_rest.py     REST-only Discord client — no gateway, no intents
  model.py            the harness's own Gemini access, with a hard daily budget
  control/
    supervisor.py     start/stop/restart/logs — the deploy step
    dashboard.py      the console API, session minted without OAuth
    guild.py          channels (incl. private), roles, teardown
  fleet/
    persona.py        who each emulator is
    dialogue.py       persona + beat -> a line someone would actually type
    registry.py       token -> Discord user id, for OLISAR_PEER_BOT_IDS
    runner.py         executes a scenario in either lane
  scenarios/          the versioned inputs (*.json)
  eval/
    rubric.py         what "better" means
    judge.py          absolute scoring, order-controlled pairwise, calibration
    redteam.py        the guardrail gate
    scorecard.py      aggregation and the promotion rules
  experiments/
    variants.py       a named configuration; how to land a winner
    loop.py           measure -> propose -> gate -> compare -> promote
  personas/           *.json
  variants/           *.json (tracked — they carry the hypothesis)
  runs/               transcripts, scorecards, rounds (gitignored)
```

## What changed in Olisar itself

Two small, production-inert seams:

- **[olisar/peers.py](../olisar/peers.py)** — `OLISAR_PEER_BOT_IDS` lets specific bot accounts
  be treated as members. Empty in every real deployment, where the predicate reduces to
  `not author.bot`. Without it the emulators are invisible: Olisar's message listener drops
  bot authors outright.
- **[olisar/prompt_overrides.py](../olisar/prompt_overrides.py)** — `OLISAR_PROMPT_OVERRIDES`
  points at a JSON file replacing the baked-in blocks (`operating_rules`, `tools_note`,
  `proactive_note`, `follow_up_note`), re-read on mtime change so a variant swap needs no
  restart. Unset, every getter returns the text compiled into the source. A missing or
  malformed file falls back to the defaults and logs once; a bad override can never strip
  the guardrails.

### `peer_bot_ids` is not `see_other_bots`

They look alike and mean opposite things, so it's worth being explicit:

| | `see_other_bots` (a real setting) | `OLISAR_PEER_BOT_IDS` (this harness) |
|---|---|---|
| what it admits | any bot in the server | only the ids you list |
| stored as | a bot (`author_is_bot` true, sender named) | a person (`author_is_bot` false, own profile) |
| gets a user profile | no | yes |
| can trigger a reply | **never** — two bots is a loop | yes, that's the point |
| for | a music bot's now-playing crowding the context | emulators standing in for members |

The loop `see_other_bots` refuses to allow is real; the harness permits it only because it
governs turn-taking directly, with a message ceiling, a minimum gap, and a run timeout.
