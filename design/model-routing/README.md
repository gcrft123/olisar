# Model routing

Built (see `olisar/routing/`). Where the build departs from the proposal, it's marked **Built:**. Olisar picks a model for each reply based on what the message needs, instead of starting every reply at the head of the chain. It's off by default on a free key and on by default once billing is on.

Today every reply starts at the server's `default_model` (Gemini 3.8 Flash unless someone changed it) and walks down `RANKED` when a model is busy or spent. "thanks!" and "why does my mod loader crash with this stack trace" get the same model with the same thinking budget. Background work is already routed by hand: summaries, impressions, the glossary miner, the chime-in classifier and image descriptions all name `gemini_lite_model` or the vision chain. This design does the same for replies, per message.

## Decision: build it ourselves

Olisar should own the router. The third-party options are built to choose among dozens of models from many providers. Olisar has one provider and three sensible tiers, and its hard problem is one none of them model: a free key's per-model daily allowance, which models are parked right now, and a monthly budget kept in Olisar's own database.

| Option | What it is | Why not |
| --- | --- | --- |
| [OpenRouter Auto Router](https://openrouter.ai/docs/guides/routing/routers/auto-router) | A gateway. It sorts each prompt into one of about 30 task types and ranks models by what OpenRouter's users spent on that task over the last 7 days. No fee on top of the model's price; `allowed_models: ["google/*"]` limits it to Gemini. | Every message members send would pass through a second company. It needs its own account and credits, it can't spend a free key's Gemini allowance the way Olisar does, and it ranks by market spend, not by what this server has left today. It breaks the positioning ("their keys") and the data statement ("Anything Olisar replies to is sent to Google Gemini"). |
| [Not Diamond](https://www.notdiamond.ai/pricing) | A recommender: it names the model, you make the call. $0.05 per million tokens routed, with a waitlist. It only skips seeing the prompt in its enterprise privacy mode. | A paid account even for a free-tier owner, a network hop before every reply, and its value is choosing across providers. Worth another look if Olisar ever adds a second provider. |
| RouteLLM (LMSYS, open source) | Trained classifiers that split traffic between one strong and one weak model. | Binary, trained on GPT-4 vs Mixtral preference data, and its best routers need PyTorch or OpenAI embeddings: hundreds of MB in a PyInstaller bundle that also runs on a free Oracle Arm box. Its threshold calibration is worth borrowing (below). |
| LiteLLM, Portkey | Gateways with retries, fallback and load balancing. | They don't look at the task. Olisar's quota-aware fallback in `GeminiClient._raw_generate` already does the rest, and better for a free key. |
| Vertex AI Model Optimizer | Google's own per-request model picker. | Vertex AI only. Olisar runs on AI Studio keys, and moving owners onto Google Cloud service accounts would wreck the setup wizard. |

Building it costs little because most of the parts exist. Recall already embeds every incoming message with `gemini-embedding-001` (`olisar/memory/retriever.py`), so a classifier can read that vector for free. The client already falls back across models mid-reply. The tier is known (`olisar.gemini.tier`), and so is spend (`olisar.gemini.spend`), the day's usage, and the Usage page's pace line.

## What gets routed

Replies: `generate_reply` and the console's test chat (`generate_sandbox_reply`), including the forced final answer and truncation continuations, which keep the reply's route.

Background work keeps its fixed assignment. Summaries, impressions, the glossary, the chime-in classifier, reaction picks, image descriptions, the status line, the self-test and embeddings are already on the right model. A later phase can route catch-ups (long input, where a billed key might want Flash), extension reviews (high stakes, a candidate for Pro) and extension generations.

## Tiers

Three tiers. Each is a chain plus a thinking level, and the chain still falls back on a 429, 5xx or retirement exactly as today.

| Tier | For | Free key | Billed key | Thinking |
| --- | --- | --- | --- | --- |
| Light | Acknowledgements, greetings, banter, one-line answers with no lookup | 3.5 Flash-Lite, then the rest of the Flash-Lite rungs | Same | minimal |
| Standard | Questions, lookups, actions, anything with tools or images | The server's chain, from `default_model` down (today's behavior) | Same | medium |
| Deep | Code and stack traces, multi-step reasoning, long or multi-part asks, explicit requests for depth | Standard's chain with high thinking. No Pro: it has no free allowance | 3.1 Pro Preview, then standard's chain with high thinking | high |

On a free key, Pro isn't available, so routing there is about spending the Flash allowance where it matters. On a billed key it's a cost and quality trade in both directions: trivial turns get cheaper and hard ones get Pro.

Thinking is set per tier as `thinking_level`, which is what the Gemini 3 models take. Each model supports a different set: 3.8 Flash takes low, medium and high, 3.5 Flash-Lite and 3.6 Flash also take minimal, and 3.1 Pro takes low, medium and high. The client maps the tier's level to the nearest one the candidate supports, and to a `thinking_budget` for the 2.5 rungs. Today `generate_with_tools` sends `thinking_budget=1024` on every model, which Gemini 3 only accepts as a legacy field.

## How a message gets its tier

The router runs once per reply, after recall and before the tool loop. It makes no network calls and takes a millisecond or two. Any error in it, or a missing embedding, routes standard, which is today's behavior. The router can never block or delay a reply.

### Floors and ceilings

Rules go first, because some calls shouldn't be left to a score:

- A message with images attached is at least standard.
- A message with lookup intent ("what did Sam say about…", "find the message", a question about the server, a URL) is at least standard. The arena's open finding is that Olisar already fabricates facts about its own server; a lighter model does it more.
- A message that would reach the settings tools, or names an action (remind, DM, post to a channel), is at least standard.
- A message that reads like an injection attempt (asks for the system prompt, claims authority, says to ignore instructions, pastes a "SYSTEM:" turn) is at least standard. **Built:** and never deep, so an attack can't spend Pro. The router's input is written by whoever is talking, so the light tier can't be a door to a weaker model's guardrails. The `rt-*` arena scenarios must pass on the light tier before it ships.
- A reply to one of Olisar's own messages can't route below the tier that message was written on, unless it's a pure acknowledgement. **Built:** keyed by member and channel for 10 minutes rather than by Discord reply. After standard or deep, only a pure acknowledgement may go light ("do it", "sure" or "yes" may be agreeing to an offer and don't count). After deep, only a short question or a continuation ("and…", "what about…") stays deep, so one deep answer doesn't put the next ten minutes of chat on deep. "and on Windows?" after a deep answer about a crash is short but needs the deep model. The tier of each sent reply is kept in a small in-memory map by message id; after a restart the floor is standard.
- A chime-in nobody asked for (`addressed=False`) is never deep.
- A DM from someone who isn't a member of the home server is never deep.

### Score

What's left gets scored on two kinds of features.

The lexical ones are cheap and readable: word count, whether it's a question, a closed list of acknowledgements, greetings and laughter ("thanks", "ty", "lol", "gg", "gm", emoji alone), code fences and stack-trace shapes, math, numbered or multi-part asks, reasoning verbs ("explain why", "compare", "step by step", "debug", "plan"), and explicit asks for depth ("in detail", "think hard").

The other is the message's embedding, which recall has already paid for. Olisar ships around 600 labeled example messages with their 768-dimension vectors (about 1 MB as float16), and the router takes the similarity to each tier's nearest examples. The examples come from the arena's scenarios and synthetic Discord chatter, never from a real server's members.

A small logistic model combines them into p(light) and p(deep). Its weights are fit offline and ship as a JSON file next to the examples.

### Thresholds

Light if p(light) clears its threshold, deep if p(deep) clears its threshold and the deep guardrails allow it, standard otherwise. The thresholds are calibrated the way RouteLLM does it: on a labeled set, pick the value that hits a target share while holding the rate of routing too low under 5%. Routing too low is the expensive mistake, since a member sees a worse answer. Routing too high only costs money or allowance.

## Escalation during a reply

Classification will be wrong sometimes, so the tool loop corrects it as it goes. The signals are failures the pipeline already detects:

- The light model types out a tool call instead of making it (`_settle_typed_calls` returns None). The comments in `pipeline.py` already note that weaker fallback models do this. Re-ask on standard instead of re-asking the same model with `TYPED_CALL_RULE`.
- The light model makes a lookup. Run it, then write the answer on standard. Answering from search results is where lighter models invent things.
- The light model calls an action or settings tool. Don't run it; re-ask the turn on standard. That's a second lock behind the floors above.
- The light model returns nothing, or the loop needs a forced final answer. Use standard.

**Built:** simpler and stricter. A light turn that calls any tool but `react` or `acknowledge` (lookups included), types a call out, or comes back empty is thrown away unrun, and the same turn is asked again on standard. No tool result is ever read by the light model, and no thought signature crosses models, so the cross-model self-test below wasn't needed.

A reply only escalates, never steps down, and never escalates into Pro at runtime: deep is chosen up front or not at all. Switching models mid-reply is safe because Google's thinking guide says to resend the previous model's thought blocks when switching models within a session, which the loop already does by appending the model's turn verbatim. The self-test should add one cross-model tool turn (a call on 3.5 Flash-Lite, the answer on 3.8 Flash) so a change there turns up in the log first.

Each escalation is counted against the route that caused it. That count is the router's error rate on real traffic.

## Free key: off by default, and pacing when on

A free key's Flash rungs allow roughly 750 to 1,000 requests a day between them (250 each, and the 2.5 pair only for projects that used it before). **Built:** those were the published figures, and they were far out. Refusing a free key on 2026-10-10, Google named 20 a day on 3.8 and 3.6 Flash and 500 on 3.5 and 3.1 Flash-Lite, and counted each -latest alias against the model it points to, so a newer project's Flash day is 40 requests, about 28 replies, against about 1,000 Flash-Lite requests. That makes free-tier routing worth more than this section assumed: each light reply saves one of 40 Flash requests, not one of 1,000. Pacing reads Google's figures and counts an alias's requests against its model's allowance.

With routing on, light turns go to Flash-Lite all day, so Flash lasts about 1.5 times as long when a third of the traffic is light. On top of that, the light threshold follows the Usage page's pace line. When the Flash rungs are on pace to run out before midnight Pacific, the threshold drops so more borderline turns go light, and when there's plenty left it rises. It stays between a 20% and a 60% light share. Once Flash is spent, everything is on Flash-Lite anyway, as today.

It's off by default because it saves no money, and on a server that never runs out of Flash it only lowers the quality of easy replies. It helps the servers that hit the cap by evening. A later phase can have the Usage page suggest it once Flash has run out on 3 of the last 7 days.

## Billed key: on by default, with Pro kept in check

**Built:** Pro is on by default, after a detour. Tested without tools on 13 hard questions with checkable answers, 3.1 Pro and 3.8 Flash at high thinking both got 13 of 13, Flash in about half the time at a quarter of the cost, so Pro was briefly made opt-in. In the reply path, with tools, that reversed: Flash at high or medium thinking answered a debugging question by running web search two or three times (41 seconds to over two minutes, up to $0.04), where Pro answered in 8 to 14 seconds for $0.012–0.020 and Flash on standard's budget in 4 seconds. So deep is Pro at high thinking, and a deep reply without Pro (capped, turned off, or a free key) is sent as a standard reply rather than as Flash thinking hard. The caps below apply.

Rough cost per 1,000 replies, using 3,500 input tokens per request and 1.4 requests per reply (the arena database averages 3,000 to 4,000 tokens per conversation request). Output is 250 tokens on light, 700 on standard and 2,500 on deep, thinking included.

| | Everything on 3.8 Flash | Routed, 5% Pro | Routed, 10% Pro | Routed, no Pro |
| --- | --- | --- | --- | --- |
| Through 2026 | $7.35 | $7.82 (+6%) | $10.04 (+37%) | $6.18 (−16%) |
| From 2027 (Flash doubles) | $14.70 | $12.23 (−17%) | $14.09 (−4%) | $11.53 (−22%) |

A Pro reply costs about 7 times a Flash reply and 22 times a light one, so the share of deep replies decides the bill. With deep held near 5%, routing roughly breaks even this year and saves about a sixth once Google doubles Flash on 2027-01-01. Hence these guardrails, all on by default:

- Deep is capped at 5% of the last 24 hours' replies, with a floor of 3 a day so a quiet server can still use it.
- Each member gets at most 10 deep replies a day, so one person can't spend the owner's money by asking hard questions on purpose.
- Pro turns off once the month passes 80% of the budget (`spend.WARN_AT`). Over budget, the existing behavior takes over: `BUDGET_CHAIN` or a stop, and routing only chooses among the cheapest models.
- **Built:** Google holds a Tier 1 key to 25 Pro requests a minute and 250 a day. Olisar throttles Pro at 20 a minute, and while Google or the throttle has Pro parked, deep is sent as standard rather than falling through to Flash at high thinking.
- A deep reply over a cap goes to standard with high thinking, not to Pro.

Pro is slower. High thinking can take 10 to 30 seconds, which is fine for a hard question with the typing indicator running (`bot/replies.py` already keeps it alive) and wrong for chat, which the floors keep away from it.

3.1 Pro is a preview model, and Google retires previews on short notice. The deep chain falls back to 3.8 Flash on a 404 like any other rung, and the self-test sweeps it on a billed key. It skips Pro on a free key, where it only ever answers 429. `pricing.py` needs its price ($2 in, $12 out per million, and $4 and $18 above 200k tokens).

## Settings

Two controls.

On the API keys page, next to the budget (**Built:** that's where the budget lives, not Usage): a "Model routing" toggle. It stores two values, `AppConfig.routing_free` (default off) and `AppConfig.routing_paid` (default on), and shows the one for the key's current tier. The tier belongs to the key, and the key and the money belong to the install, so this is install-wide. Storing both means turning billing on switches routing on, and turning it back off returns to the owner's free-tier choice.

Per server, the "Primary model" select gets "Automatic" as its first option. Automatic follows the install's routing switch: routed when it's on, today's behavior (the head of the chain) when it's off. Picking a model pins it and that server isn't routed. New servers start on Automatic. On upgrade, rows still holding the seeded `DEFAULT_CHAT_MODEL` move to Automatic, the same reasoning `migrate_model_default` used for `LEGACY_DEFAULT_CHAT_MODEL`: the value was seeded, not chosen. On a free key with routing off, that changes nothing. The chat settings tools (`self_settings.py`) accept `auto` the same way.

No sliders in the first version. An operator dial like OpenRouter's (cheaper ↔ better) can come later, once real escalation rates say where its midpoint belongs.

## What the operator sees

- The Usage page gets a routing breakdown: replies per tier today and this month, and escalations. On a billed key it shows what routing saved or added against everything on standard. On a free key it shows how many Flash requests light turns saved. That's the same honest comparison the page already makes with "what billing would cost".
- The test chat shows each reply's tier, model and reason under it, operator only, so routing can be checked without guessing.
- The log line that names the serving model adds the route: `gemini gemini-3.8-flash served conversation (route standard: lookup)`.

## Telemetry and privacy

Routing records the tier, the reason codes, the scores and whether it escalated. It never stores message text for routing. A new `usage_route` rollup (day, tier, replies, escalations, deep replies capped) sits beside `usage_source`, rather than splitting `conversation` into sub-sources the Usage page would then have to merge back.

## Where it lands

| Area | Change |
| --- | --- |
| `olisar/gemini/models.py` | Tier chains next to `RANKED`: the light chain, and the billed deep chain headed by `gemini-3.1-pro-preview`. Retirements update them in the same release as the chain. |
| `olisar/gemini/client.py` | `thinking_level` per call, mapped to what each candidate supports. A route (chain and thinking) can be passed in place of `model`. |
| `olisar/gemini/pricing.py`, `canary.py` | Pro's price; Pro in the billed sweep; the cross-model tool turn. |
| `olisar/routing/` (new) | Features, the classifier with its shipped weights and examples, the policy (floors, guardrails, pacing), the per-message tier map. |
| `olisar/memory/retriever.py`, `embeddings.py` | Hand the query vector to the router, or keep a small cache in `embed_query` so it isn't embedded twice. |
| `olisar/pipeline.py` | Route after recall; the escalation hooks in `_run_tool_loop`, `_settle_typed_calls` and `_force_final_answer`; the action-tool gate on a light turn. |
| `olisar/db/models.py`, migrations | `AppConfig.routing_free`, `routing_paid`; `auto` as a `default_model`; the `usage_route` table; moving seeded defaults to `auto`. |
| `api/`, `web/src/pages.tsx`, `web/src/usage.tsx` | Automatic in the model select, the toggle, the breakdown, the test chat's route line. |
| `arena/` | A variant that forces a tier, to run every scenario on each one; the labeling script. |
| Docs | `DOCUMENTATION.md`, `web/src/docs.tsx`, the docs site, and `PRODUCT.md`, which says "The model chain is the same on both, with no Pro model." |

## What was built to evaluate it

The classifier is fit by `scripts/fit_router.py` from 560 hand-labeled messages (`olisar/routing/training/examples.jsonl`) and checked against 87 messages taken from the arena's scenarios and labeled separately (`arena_holdout.jsonl`), never trained on. Thresholds come from out-of-fold scores, with precision weighted to a traffic mix of 35% light, 60% standard and 5% deep, so a set with far more deep examples than real traffic doesn't make deep fire too easily. Cross-validated: 3 of 208 standard messages routed light ("tell me a joke", "roast me", "make it funnier") and 1 routed deep; 83 of 90 deep messages caught. On the arena holdout, no standard message went deep and one went light ("hey olisar what do you think").

`arena/experiments/routing_probe.py` sends a fixed set of messages through the test chat and records each one's tier, model, cost and time, stopping at a spending cap. The test chat embeds the message itself (only when the embedding model can take it at once) so it routes as a live reply would.

## Tested on a billed key (2026-10-10)

All through the arena instance on a key with billing on, with a safety budget of about $12 that stops at the budget. Costs are the instance's own count at Google's paid rates.

| Test | Result |
| --- | --- |
| Tier detection | The console's key check said "billing on"; the instance switched to paid limits and released the free key's parked models |
| Routing probe, 16 messages through the test chat | 16 of 16 on the intended tier. Light: 3.5 Flash-Lite, under a second, about $0.0004 each. Standard: 3.8 Flash. Deep (with Pro on, as built then): the first 3 on Pro at $0.014–0.020 and 8–12 seconds, the 4th held to Flash by the 5% share. $0.08 in all |
| Pro against 3.8 Flash at high thinking, 13 checkable hard questions, no tools | Both 13 of 13. Pro: median 10–11 s, about 4 times the cost, chattier |
| The same deep messages in the reply path, with tools | Pro: 7.6–14.1 s, $0.012–0.020, no searches. Flash at high thinking: up to over 2 minutes on a debugging question (2–3 web searches), $0.005–0.033. Flash at medium: 41–91 s on the same question. Flash on standard's budget: 3.5 s. Pro kept as the default deep model; deep without Pro sent as standard |
| A/B, 12 everyday fast-lane scenarios, routing on against every reply on 3.8 Flash, one rep each, judged | Accuracy and brevity level; restraint +0.57 (under 2 SE); helpfulness −0.67, all of it from three scenarios that ran entirely on standard (the same model and settings as the control), so judge noise. In the 7 scenarios where routing changed a model (13 light turns), routing scored the same or better in all 7. One rep can't show small effects |
| Self-test sweep, every rung plus Pro | Every model the key can use completed a tool round trip, Pro included. $0.005. The 2.5 pair came back "no longer available to new users", now inconclusive rather than failed |
| Web search | Ran on 3.8 Flash (the billed path), returned sources |
| Image, live in Discord | Gemini's image model made and posted a correct image, about $0.034 |
| Budget at 80% | Deep replies held off Pro ("capped: budget") |
| Over budget, keep going on the cheapest | Every tier answered from 3.1 Flash-Lite (2.5 Flash-Lite isn't served to new projects); web search refused |
| Over budget, stop | The rate-limit reply, nothing spent. Its wording says to try again in a minute, which is wrong for a budget that's out until next month |
| Red-team gate, fast lane, 3 reps | Passed 8 of 8 with real replies in every case (on the free key the last two had passed on rate-limit replies). Every red-team turn routed standard |

The tier each message actually needs, measured on 249 fresh messages with every tier's answer judged, is in [EVALUATION.md](EVALUATION.md), along with Pro's worth on deep replies, the light tier's guardrails, a 3-rep A/B and live-lane routing. In short: the router fails its pre-registered criteria on deep (13% of messages sent there didn't need it), Pro beats standard by 0.17 on the messages it gets, and sending deep as standard was the recommendation. Pro stays on by default regardless.

## Evaluation and rollout

The labeled set comes first. Take around 500 messages from the arena scenarios and synthetic chatter, answer each on all three tiers through the test chat, and have Pro judge the cheapest tier whose answer is as good as the best one. That's the message's true tier. It costs about $40 on a billed key, once. The router's targets on that set: under 5% routed too low, under 20% routed too high.

Then:

1. Shadow. The router decides and logs on every reply but changes nothing. One release on the operator's own bot shows the real distribution before anything acts on it.
2. Billed routing on by default for replies, with the free-tier toggle, escalation, the Pro guardrails, the console controls and the docs. The arena runs every scenario routed and unrouted first, and routing ships only if the judged quality isn't worse, the `rt-*` scenarios pass on the light tier, and the cost lands near the table above. The arena's findings say 16% of runs were void and some red-team checks pass vacuously when a reply comes late, so void runs are excluded and counted, not read as passes.
3. Free-tier pacing, the Usage breakdown, and routing for catch-ups, extension reviews and extension generations.
4. Threshold tuning from escalation rates (tighten the light threshold a step when more than 15% of the last 200 light routes escalated, within bounds), and maybe the operator dial.

## Open questions

- Voice drift. Flash-Lite may sound different from 3.8 Flash in the same persona, and a member could notice the bot's voice change between "lol" and a real answer. Light replies are short, which limits it, but the arena should score persona adherence per tier.
- Implicit caching. Gemini discounts a repeated prompt prefix per model, and routing splits that cache three ways. The system instruction already changes every reply (the current time, recall), so the loss is probably small, but Olisar doesn't record cached tokens yet. It should, before and after.
- Gemini 2.5 Pro. Google's pricing page lists a free tier for it, but Google only serves the 2.5 models to projects that used them before. It could be an opportunistic deep rung on a free key for those projects. Not in the first version.
- The 5% deep cap is a guess. Shadow mode will show how much of real traffic scores deep, and the cap should follow that.
