# My pick

Chapter 4 of the replay is Olisar's choice: the one thing that most belongs to this server. It can be a word, a running joke, a weekly ritual, something members built, a place, a game, a custom emoji. The test is that someone who's been here a year sees it and grins, and someone who joined last week understands it from one line.

The prototype has three picks to show the range (`&pick=1` and `&pick=2` play the runners-up): "goose", a word; Friday movie night, a ritual; the lighthouse, a Minecraft build.

## Why it isn't one prompt

Asking a model "what's this server's thing?" over its summaries gets one of three failures. It picks something generic ("the community's love of gaming"), it writes a quote that was never sent, or it picks something that's funny to most people and humiliating to one. A model also can't read 400,000 messages.

So the work is split. Code finds candidates and pulls the evidence. The model judges, but only from that evidence, and only by citing message ids. Code checks every id, picks the order from the model's scores, and fills in every number. A second model call checks each quote's meaning and whether anything could hurt someone. A third writes the two narration lines.

| Step | Who | What |
| --- | --- | --- |
| 1. Candidates | Code (SQL and full-text search) | 30 to 60 things that recur, with counts. Shortlist 12. |
| 2. Evidence | Code | For each of the 12: stats, the earliest uses with context, and later uses sampled across time. |
| 3. Judge | Strongest model in the chain, thinking on, temperature 0.2 | Scores, disqualifications, and up to 3 finalists with origin, trail and meaning. |
| 4. Check | Flash-Lite, temperature 0 | Per quote: does it use the thing in this sense? For the whole: could it hurt anyone? |
| 5. Write | The chat model, in persona, temperature 0.8 | Two narration lines for the chosen finalist, with placeholders for numbers. |
| 6. Play | Code | The top finalist that passed the check is chapter 4. Nobody reviews it first. |

About 3 to 5 requests and 25,000 tokens per replay.

## 1. Candidates

Each detector proposes things that recur. Every candidate is then counted the same way, with a phrase match in `search_message_fts` over the replay's range and channels.

| Detector | Finds | How |
| --- | --- | --- |
| Glossary | In-jokes, local terms, codenames | Every `guild_fact` subject. These are the strongest candidates, since Olisar already judged them server-specific. |
| Server words | Words and phrases this server says far more than anyone else | 1- to 3-word phrases ranked by distinct people, then by how far their rate here exceeds a bundled general-English frequency list. |
| Emoji and stickers | Custom emoji, stickers | `<:name:id>`, `<a:name:id>` and `[sticker: name]` in message content. |
| Rituals | Weekly or monthly events | A phrase that recurs on the same weekday for 8 or more weeks, or a channel whose activity pulses every 7 days (named by its most common phrase in the pulse windows). |
| Named things | Builds, places, objects, nicknames for things | "the X" and capitalized phrases repeated by many people that aren't a member, role or channel name. |
| Motifs | Things the summaries keep mentioning | One Flash-Lite pass over `channel_summary` text: "list things that keep coming back". Only proposes; the counts still come from search. |

Hard filters, applied in code before anything reaches a model:

- At least 6 distinct people, or 3% of everyone who posted in the range, whichever is more.
- No one person accounts for more than 35% of uses, or 60% for a ritual or an event with its own threads, which someone usually runs.
- Used in at least 3 calendar months, and in at least 2 channels (a ritual in its own channel passes with 20 distinct days instead).
- The earliest uses are in channels everyone can see, from people who can be named. If the first use is by someone opted out or gone, the next use by someone else is the origin, or the candidate goes.
- Not a member's name or nickname, a channel or role name, Olisar's name or aliases, a stopword, or a word in the top 30,000 of general English.
- Fewer than 40% of uses in staff, mod or NSFW channels, and nothing on the blocklist (slurs, sexual terms, self-harm).
- Not the subject of another chapter (the longest night's day is excluded from its counts, so "candle" can't win on one night).

The shortlist score is `log(people) × √months × channels^0.3 × (1 − top person's share) × unusualness`, and the top 12 go forward, with at most 4 from any one detector so the judge sees a mix of kinds.

## 2. Evidence

One block per candidate, in plain text. Ids are short handles mapped back to message ids by code. Context lines (marked `·`) are there so the model can read the moment, and can't be cited.

```
c07 · from glossary · "goose"
  glossary: goose: a win nobody saw coming
  uses 1,204 · people 212 · channels 17 · months 41 · first 2022-05-08 · last 2026-09-30 · top person 6%
  earliest:
    · #games 2022-05-08 22:14 bram: did you SEE that
    m201 #games 2022-05-08 22:15 okapi: that was the most goose thing that's ever happened
    · #games 2022-05-08 22:15 tess: wait what does that mean
    m202 #games 2022-05-08 22:16 okapi: you know. goose. unexpected win
    m203 #general 2022-05-11 19:02 juniper: we need goose energy tonight
  later:
    m204 #general 2022-06-02 20:40 tess: absolute goose
    m205 #memes 2022-08-19 13:11 nightjar: [image: goose.png] goose
    m206 #late-night 2023-01-14 01:22 pocket: goose energy tonight
    m207 #general 2023-07-02 11:05 vel: saw a goose at the lake today, it was huge
    ...
```

Earliest: the first 4 uses, each with 2 messages either side. Later: 8 uses sampled evenly across quarters and channels, each with the message before it. Text is cut at 200 characters. People who can't be named appear as "Someone" and their messages aren't given ids.

The judge also gets the other chapters, one line each, so it doesn't repeat them:

```
Other chapters: 1. The server's first day and first five members. 2. People arriving, 2021 to 2025. 3. February 3, 2023, when bram's power went out and #general stayed up. 5. Olisar joining, and being renamed Pinecone. 6. Now.
```

## 3. The judge

The prompts' `{placeholders}` are filled by plain string replacement before sending, so the JSON braces in them stay as written. The one exception is `{people}` and `{count}` in the writer, which the model writes back literally for code to fill.

```
PICK_JUDGE_SYSTEM = """\
You're choosing one chapter of a replay of a Discord server's history. The replay is a short \
fullscreen story that the server's admins watch and often show to everyone. Most chapters are \
fixed: when the server started, who arrived, its busiest day. This one is a free pick: the single \
thing that most belongs to this server. It can be anything: a word or phrase, a running joke, a \
ritual or regular event, something members made, a place, a game, a custom emoji.

The test: someone who has been here a year sees it and grins, and someone who joined last week \
understands it from one line.

You'll get candidates the server's data turned up, each with counts and messages. Judge only from \
that evidence. Score every candidate from 0 (not at all) to 3 (clearly) on each of these:

- ours: its meaning or importance is specific to this server. A generic activity ("gaming", \
"movie night") scores 0 unless the evidence shows what's particular about it here: who runs it, \
how it goes, what it's called.
- shared: many different members take part, in more than one channel. Mostly one person's habit \
scores 0 or 1 however often they do it.
- origin: one of the earliest messages clearly shows it beginning: coined, proposed, invented, \
first held, first built.
- lasting: it comes back over months or years. Still going scores highest.
- warm: it's fond, funny, or charmingly absurd, and showing it to the whole server would \
embarrass or hurt no one.
- plain: it can be explained to a stranger in ten plain words or fewer.

Disqualify, with a short reason and no scores, any candidate that:
- is about a conflict, a ban or kick, a breakup, members arguing, moderation, money, health, \
injury, grief, politics, religion, or anything sexual
- is a joke at someone's expense, or about a member's body, looks, age, family, or private life
- is mainly about one member, even fondly: their catchphrase, their pet, their cooking
- is internet slang or a meme that means the same thing everywhere
- is about Olisar, or is something another chapter already covers
- has evidence that doesn't hold together: the messages don't show the same thing, or the word \
is mostly used in its ordinary meaning

Be strict about warm. If you're unsure whether something could hurt someone, disqualify it.

Then name up to 3 finalists, best first. A finalist needs at least 2 on ours, shared, origin and \
warm. If no candidate qualifies, return no finalists: no chapter is better than a weak one.

For each finalist give:
- kind: one of word, joke, ritual, made, place, game, emoji, other.
- title: the thing's own name, written the way the server writes it.
- meaning: what it is here, in ten words or fewer, for someone who has never been on the server. \
No names, no numbers, no quotation marks.
- origin: the id of the one message that best shows it starting. It must be among the \
candidate's earliest messages and use the thing in the sense you mean.
- trail: 6 to 10 ids of later messages that show it carrying on, spread across time and, where \
possible, channels. Leave out any message that uses the word in its ordinary meaning.
- why: one sentence for the admin on why this one, in plain words.

Cite only ids that appear in the evidence. Don't quote or paraphrase messages and don't state \
counts; the replay shows the real messages and numbers.

Return only JSON in this shape:
{schema}
"""
```

`{schema}`:

```json
{
  "candidates": [
    {"id": "c03", "disqualified": "a joke about one member's cooking"},
    {"id": "c07", "scores": {"ours": 3, "shared": 3, "origin": 3, "lasting": 3, "warm": 3, "plain": 3}}
  ],
  "finalists": [
    {
      "id": "c07", "kind": "word", "title": "goose", "meaning": "a win nobody saw coming",
      "origin": "m201", "trail": ["m203", "m204", "m205", "m206"],
      "why": "Most of the server uses it, and it started with one message in #games."
    }
  ]
}
```

What code does with the answer:

- Drops any finalist with an id, origin or trail id that isn't in that candidate's evidence, or whose origin isn't one of its earliest messages.
- Re-orders finalists by its own total, `3·ours + 3·warm + 2·shared + 2·origin + lasting + plain`, and re-applies the floor. The model's order is logged when it disagrees; that's the most useful signal for tuning the prompt.
- Rejects a meaning over ten words, with a digit, or with a member's name, and a word or emoji whose title doesn't appear in its origin message.

## 4. The check

One call per finalist, until one passes.

```
PICK_CHECK_SYSTEM = """\
You check one chapter of a Discord server's replay before an admin sees it. The chapter is about \
"{title}", which on this server means: {meaning}.

For each message below, answer "yes" if it uses "{title}" in that sense, "no" if it uses it some \
other way, and "unclear" if you can't tell.

Then imagine the chapter on a big screen in front of the whole server, and answer:
- hurts: could anyone named or quoted reasonably be embarrassed, hurt, or singled out?
- sensitive: does it touch conflict, moderation, health, grief, money, politics, religion, or sex?

Return only JSON: {"uses": {"m201": "yes"}, "hurts": false, "sensitive": false, "reason": ""}
"""
```

Code drops trail messages that aren't "yes". The finalist fails, and the next one is checked, if its origin isn't "yes", fewer than 4 trail messages survive, or `hurts` or `sensitive` is true.

## 5. The writer

```
PICK_WRITER_SYSTEM = """\
You're {name}, a member of the Discord server {server}, narrating one chapter of a replay of its \
history for the people in it. {persona}

This chapter is about {title}: {meaning}. It started with this message, which the replay shows \
under your words:
{origin_author} in {origin_channel}, {origin_date}: {origin_text}

Write two lines.
- intro, shown above that message: what {title} is here, and who started it. Two short sentences \
at most.
- spread, shown while it spreads across the screen: how it carried on. One or two short \
sentences. Where a number belongs, write {people} for how many members took part and \
{count} for how many times; the replay fills them in.

Voice: first person, plain, a little dry, fond without gushing, like a friend who was paying \
attention. Use people's names exactly as given, lowercase if they're lowercase. "Someone" stays \
"Someone".

Don't use exclamation marks, rhetorical questions, or emoji. Don't call anything legendary, \
iconic, a core memory, an era, or a vibe. Don't explain the joke, quote the messages, mention \
being a bot, or say anything the evidence below doesn't support.

Evidence, for understanding only:
{trail}

Return only JSON: {"intro": "...", "spread": "..."}
"""
```

`{persona}` is the persona's tone notes, so the narration sounds like the bot the server knows. Code fills `{people}` and `{count}` from the stats, and rejects a line with an exclamation mark, a banned word, a name that isn't in the evidence, more than two sentences, or more than 32 words. One retry with the reason appended; after that it falls back to a template: "Here, {title} means {meaning}. {origin_author} started it." and "{people} of you have kept it going. {count} times so far."

The demo's three picks use these lines. They were written by hand as the target the writer should hit, not produced by the prompt:

| Pick | intro | spread |
| --- | --- | --- |
| goose | Here, goose means a win nobody saw coming. okapi said it first. | 212 of you have said it since. 1,204 times, in 17 channels. |
| Friday movie night | Every Friday at nine since June 2021. tess started it, and it has started late ever since. | 61 of you have come at least once. 198 Fridays so far. |
| the lighthouse | A Minecraft build on the north cliff. bram laid the first block and said to add whatever. You did. | 14 of you built on it for two years. 640 messages about it. |

## 6. What plays

There's no draft, so no admin sees the pick before the replay plays it. The top finalist that passed the check is chapter 4, and the check in step 4 is the last gate: when it's unsure, the finalist is dropped. With no finalists left, the replay has no chapter 4. The judge's `why` shows when someone opens the pick's memory.

## Getting the prompts right

The prompts above are a first draft. They get good by running against servers where the right answer is known, not by rewording them in the abstract.

Build six seeded servers (fake members and messages written into a copy of the schema), each with one clear best answer and traps:

| Trap | Expect |
| --- | --- |
| A frequent joke about one member's cooking | Disqualified as about one member |
| A month-long argument with the highest activity of the year | Disqualified as conflict |
| "W" and "ratio" with huge counts | Low on ours, never a finalist |
| "bridge" on a bridge-building game server, half the uses about real bridges | The check drops the ordinary uses; the trail stays clean |
| A phrase that lives mostly in #mods | Filtered before the judge |
| A small, quiet server with nothing that qualifies | No finalists, no chapter |

Then run the IOTE test server and the arena server for a human read of taste, since seeded data can't tell you whether a pick is actually charming.

Pass when, over five runs of each seeded server: no trap is ever a finalist, the planted answer is first in at least 5 of 6 servers and the same pick comes back in at least 4 of 5 runs, no invented id survives to the check, and the writer passes code's checks first time in at least 95% of calls. On the free tier that's a few days of requests, so a paid key during tuning is worth it.
