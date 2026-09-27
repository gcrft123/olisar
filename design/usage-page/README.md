# Usage page

A standalone redesign of the console's Usage page, laid out in the order someone opening it needs answers: how much is left today, which model is replying, where the requests go, then everything else. It's built into the console as `web/src/usage.tsx`; this folder keeps the prototype it was built from.

Open `index.html` in a browser, from disk or from any server. It's one file, drawn in the console's tokens at its 110% interface size, with the rail around it so the page can be judged in place. It only reaches the network for IBM Plex Sans and JetBrains Mono; the glyphs are Solar's, rendered from `@solar-icons/react`'s own path data. A request arrives every few seconds, so the numbers move the way they would on a live bot.

The Preview strip in the corner holds the page in one of six states. `?state=low` opens on one, and `?clean` hides the strip.

| File | What it is |
| --- | --- |
| `index.html` | The page. |
| `explorations/page.jpg` | The whole page in the afternoon state, at 1440 wide. |
| `explorations/states.jpg` | The top block in each of the six states. |
| `explorations/phone.jpg` | The page at 390 wide. |
| `explorations/interactions.mp4` | The first pass, with the chain bar the review took out: a segment lighting its row, a model resting and coming back, the chain running low and running out. |
| `explorations/before.jpg` | The page it replaced, on the dev fixture, for comparison. |

## What the page before it got wrong

- **"+10% vs yesterday"** compares today so far with the whole of yesterday. At 9 AM an ordinary day reads about −60%.
- **Tokens / min** draws the busiest minute's tokens across every model against a hard-coded 1M cap (`TPM_LIMIT` in `api/routers/usage.py`). Google's token limit is per model, so the chart compares a sum with one model's limit. When the cap is off the chart, its label sits at the top edge anyway, where it reads as a ceiling the line is about to hit.
- **Requests over time** fills the area under the first model's line, and the fill runs under every other line, so it reads as a stacked total that isn't one.
- **No daily limit anywhere**, and the daily limit is what stops the bot. The per-minute meters turn amber past 75%, which is normal fallback rather than trouble.
- **The reset time is wrong.** The callout says limits reset at 00:00 UTC. Google resets requests-per-day at midnight Pacific ([rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)), and the usage tables bucket by UTC day, so for 7 or 8 hours every evening the page starts a fresh day while Google is still counting the old one.
- **The docs** (`usage` in `web/src/docs.tsx`) still describe a dashed daily-limit line the page stopped drawing.

## The page

### What's left, and what's replying

Every chat model has its own daily limit and the bot falls back through them in order, so what's left today is what's left on every model that can still answer. The big number is that sum. A model Google has stopped counts as zero, even when Olisar counted fewer requests than its limit.

Beside it, "Replying with" names the first model that can take a request right now. Where it sits in the chain, and what's left on it, is in the table below.

Under both, past a rule the divider between them ends on, the pace line projects today's average rate since Google's day began. It says the allowance lasts until the reset, or, in amber, when it runs out and how long before the reset that is. With nothing left it says Olisar can't reply until the reset, in red, and the rail's badge reads Rate-limited, as `BOT_BADGE` has it.

Memory search (`gemini-embedding-001`, which has no fallback) and web search (Google Search grounding) have daily limits of their own, so they sit beside the pace line as small meters rather than in the total.

The first pass drew the chain as a bar under the two figures, each model a segment as wide as its limit, with a playhead where the bot was. Review took it out: it restated the table. `interactions.mp4` still shows it.

### Fallback chain

One row per chat model, in order: its status, and what's left of its daily limit.

| Status | Badge | When |
| --- | --- | --- |
| Replying | `success`, `play-circle` | The first model that can take a request |
| Standby | `neutral`, `menu-dots-circle` | Further down, waiting its turn |
| Used up | `danger`, `minus-circle`, and the time | Out for the day |
| Back in 0:48 | `info`, the spinner | Resting after a per-minute limit; it comes back on its own |

When Google stops a model before Olisar's count reaches its limit, the row's meter shows the missing part hatched. That usually means something else on the same Google Cloud project is using the limit.

### By feature

The donut stays, with four changes:

- It counts only requests against the chain's daily limits. Memory search has its own limit, so its requests are out; on the dev fixture they were a quarter of the old donut.
- It opens on Today. 7 days and 30 days are one click away.
- Each feature keeps its color whatever the range: Replies, Summaries, Impressions, Glossary, Image descriptions, then Everything else, always in that order round the ring. That order passes the colorblind check for neighbors on this ground; the old rank order put green beside rose, 4.5 apart under deuteranopia where the check wants 8.
- Everything else lists what's in it on one line under its row, instead of in a tooltip. Bars beside the legend carry the comparison the ring is bad at.

### Stats

Requests and tokens today against the same time yesterday, the busiest minute against that model's per-minute limit, and when the chain last ran out. Under them, requests per day for 14 days against the chain's daily total, today's bar in the accent and any day that ran out in amber.

## Where it departs from `DESIGN.md`

- **Meters fill with what's left, not what's used** ("Meters/bars" under LineChart). Every number on the page is a "left" number, and in the first pass a fill that meant the opposite of the figure beside it read wrong.
- **The big figures are Plex Sans, not mono.** The hero is 600 at 60px, the stat tiles 600 at 26px. Mono's dotted zeros at that size read as a code sample. Figures in the table, the legend and the axes stay mono.

## What the backend would need

Most of this page was data the API didn't return. All six of these landed with the page (`olisar/gemini/quota.py`, `olisar/gemini/rate_limiter.py`, `api/routers/usage.py`). In the order the page uses it:

1. **A daily limit for each model.** `ModelInfo` in `olisar/gemini/models.py` carries `rpm` only. Google's rate-limits page doesn't list free-tier numbers any more; AI Studio shows a project's own. Two sources would cover it: an `rpd` beside `rpm`, and Google's own figure from a daily 429, whose `QuotaFailure` names the quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) and its value. The limits in this fixture (250 for the Flash models, 1,000 for Flash-Lite and for embeddings, 500 for grounding) are placeholders.
2. **Count by Google's day.** `record_usage` keys rows on `datetime.now(timezone.utc).date()`. It should use the date in `America/Los_Angeles`.
3. **Park a model until the reset when its day is spent.** Any 429 parks a model for 120 seconds today (`COOLDOWN_SECONDS`), so a model that's out for the day is tried again every two minutes all evening, each try a wasted round trip. The client already tells a per-minute throttle from a daily quota for grounding (`_retry_after_seconds`); the same check on generation could park it until midnight Pacific and record when it ran out. The table's "Used up at 11:52 AM" is that time.
4. **Every chain model's state.** `/api/usage/live` returns only models that are busy or cooling. The page needs all of them (available, resting with the seconds left, out, retired) in `RANKED` order. The page shows the install's chain from the top; a server with its own `default_model`, and the vision chain, start lower down.
5. **Counts by hour.** "On this time yesterday" and the pace line both need them. An hour column on `gemini_usage`, or a small rollup beside it, covers both.
6. **When the chain ran out.** Record the first time `chat_exhausted()` turns true each day.

Two things to check against a real 429 before trusting the total. `gemini-flash-latest` probably points at the same model as one of the pinned entries, and if Google counts an alias against the model behind it, the two share one limit and adding both counts it twice. And since the limit is per project, Olisar's count is a floor: anything else on the same project uses the same allowance.

## The states

| State | Pacific time | What it shows |
| --- | --- | --- |
| Just reset | 12:42 AM | Everything full, replying from the top of the chain |
| Afternoon | 3:48 PM | Two models used up, one of them stopped early by Google; replying from the third |
| Busy minute | 12:20 PM | The top model resting after a per-minute limit, with quota left; it comes back when the countdown ends |
| Running low | 7:05 PM | Replying from the sixth model, projected to run out before the reset |
| All used up | 9:50 PM | Nothing left; Olisar can't reply until the reset |
| Not responding | 3:48 PM | The live poll has stopped landing: the warning, and every figure dimmed |

Each state's clock starts at its Pacific time and runs from there, shown in the viewer's own time zone. A request arrives every few seconds: most are replies on the first available model, the rest background work from Gemini 3.1 Flash-Lite down, as `DEFAULT_LITE_MODEL` does. A model that reaches its limit is marked used up and the next one takes over.
