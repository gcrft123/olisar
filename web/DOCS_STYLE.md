# Docs style guide

How Olisar's documentation is written and formatted. It applies to every page in `web/src/docs.tsx` (the console's Docs tab) and the Setup pages in `DOCUMENTATION.md`; `node web/scripts/build-docs-site.mjs` builds the public site and the Markdown mirror from those two sources.

The rules come from a read of how established product docs are written (Stripe, Linear, Tailscale, Vercel, GitHub, Cloudflare, PostHog, Resend, Plausible, 1Password, Notion, Slack and the Raycast manual), narrowed to what suits Olisar's readers, and from the voice rules in `web/DESIGN.md`.

## Who reads these docs

Most readers run a Discord server and installed Olisar for it. They know Discord well (roles, channels, Manage Server) and don't know Python, SQLite or how the bot is built. Write for them unless the page says otherwise.

Three pages have a different reader:

| Page | Reader |
| --- | --- |
| Talking to Olisar | A server member who has never seen the console. |
| Build & run from source | A developer working on Olisar itself. |
| The Extend pages that cover the SDK, flows and the marketplace | A developer writing an extension. Precise terms (function names, types, HTTP status codes) are fine here. |

## What the recon showed

The docs that read best do the same handful of things.

1. The first sentence says what the thing is or what it lets you do. Linear's Triage page opens "Triage is a special inbox for your team." Tailscale opens "Funnel lets you route traffic from the broader internet to a local service." Raycast opens "Snippets let you store frequently used text and insert it anywhere." None of them opens with history, a pitch or a summary of the page.
2. They talk to the reader as "you", in the present tense, in the active voice. "We" shows up rarely and only for a recommendation ("We recommend sending from a subdomain").
3. Paragraphs run one to four sentences. Sentences mostly run 10 to 20 words, with the occasional long one when a rule has a condition.
4. Headings are short and in sentence case. Reference sections are nouns ("Subscription statuses", "Requirements and limitations"). Task sections are verbs ("Create the subscription", "Exclude visits by IP address").
5. A procedure is a numbered list with one action per step. Each step starts with a verb, names the UI label in bold exactly as it appears, and writes menu paths with `>` ("Team Settings > Triage").
6. Reference data goes in tables: statuses, options and what they do, plan differences, comparisons (Stripe's status table, Vercel's "which staging workflow" table, Slack's roles grid).
7. Limits are stated up front and plainly. Tailscale gives Funnel its own "Requirements and limitations" list instead of scattering caveats through the page.
8. Jargon is defined on first use, in a parenthesis or a clause ("your Tailscale network (known as a tailnet)"), then used without ceremony.
9. Each fact has one home. Other pages link to it ("To learn more, see Subscription invoices") instead of restating it.
10. Callouts are rare and earn their box: a condition that changes what you do, something you can't undo, or a security consequence. The main point of a page is never inside a callout.
11. Internals stay out unless the reader can act on them. Resend's domains page skips DNS mechanics and links to a guide. Nobody explains which process or table does the work.
12. Pages end when the content ends. No summary, no "Conclusion", no "Happy building!".

## Voice

- Second person, present tense, active voice. "Olisar replies in the thread", not "a reply will be posted by Olisar".
- Plainspoken and a little dry. State what happens and why in one breath, and don't hedge.
- Contractions are fine and preferred (it's, don't, you'll).
- US spelling: behavior, customize, color, canceled.
- Call the bot "Olisar" even though owners can rename it. Say "it", never "he" or "she".
- No exclamation marks, no emoji, no marketing words (powerful, simply, just, easily, seamless, robust, effortless, magic).
- Don't apologize, scold or reassure. "If the key is wrong, Olisar says so" beats "Don't worry, Olisar will let you know".
- No em dashes as a stand-in for a comma, colon or period. Most pages need none.

## Words to use

Use one name for each thing and keep it.

| Use | For | Not |
| --- | --- | --- |
| server | A Discord server | guild, community (as a noun for the server) |
| console | The admin web UI | dashboard, admin panel, web UI |
| desktop app | The Olisar app on a Mac or PC | client, launcher |
| operator | The person who installed Olisar and runs it | host, owner (ambiguous with server owner) |
| admin | Anyone with **Manage Server** on a server Olisar is in | moderator, mod |
| member | A person in a Discord server | user (except for Discord's own "user" concepts) |
| bot | One Discord application that Olisar runs; one install can run several | instance |
| VM or cloud server | The Linux machine Olisar runs on when hosted on a server | host server, box |
| knowledge base | Pages and files Olisar can look things up in | KB, docs store |
| extension | An optional package of features | plugin, add-on, mod |

Discord's own names keep Discord's capitalization: **Manage Server**, **Message Content**, **Server Members**, Developer Portal.

## Page structure

Every page follows the same shape. Leave out any part the topic doesn't need.

1. Open with one or two sentences, with no heading above them, saying what the feature is or what the page lets you do. Add one sentence on when you'd use it if that helps.
2. State requirements or limits, if there are any, right after the opening, as a sentence or a short list.
3. Put the body under `##` headings in the order the reader needs it: the common task first, the rare one last, reference tables at the end.
4. Add troubleshooting only for failure modes the reader will actually hit, as `###` headings named after the symptom ("The bot shows as offline").

Aim for a page someone can read in two to five minutes. Reference pages, such as the SDK, can run longer, because readers search them rather than read them top to bottom.

## Formatting

### Headings

- The page title is set in `docs.tsx` and shows as the page's h1. Don't repeat it as a heading in the body.
- Use `##` for sections and `###` for subsections. The console doesn't render `####` or deeper.
- Sentence case. No trailing punctuation. No numbering in the heading text.
- Name a task with an imperative verb ("Add a knowledge source"), a concept or reference with a noun ("Channel modes"). Don't use gerunds ("Adding a source") or questions, except in troubleshooting.
- A slash command can be a heading on its own (`` ### `/catchup [hours]` ``).

### Paragraphs and lists

- Keep paragraphs to four sentences or fewer.
- Use a bulleted list for three or more parallel items that don't need an order. Two items read better as a sentence.
- Use a numbered list for steps. One action per step. Put the result of a step in the same item or right after the list ("The bot joins and shows up under **Servers**").
- Lists can't nest. If an item needs sub-items, split it into its own section or use a table.
- Never use the "**Bold term**: explanation" list. Put term and meaning in a two-column table, or write the item as a plain sentence.
- Don't end list items with periods unless they're full sentences. Be consistent within a list.

### Tables

Use a table for anything with two or more attributes per item: settings and what they do, modes, commands and who can run them, limits, defaults, comparisons. Keep cells short. A cell can hold inline formatting and links but no lists or line breaks.

### Inline formatting

| Thing | Format | Example |
| --- | --- | --- |
| A label in the console or Discord (button, tab, field, toggle, menu) | Bold, exactly as it appears | Press **Save**. |
| A menu path | Bold labels joined by `>` | **Settings > Remote access** |
| A slash command, option, file, folder, command line, value, placeholder | Code | `/forget-me`, `stop_remembering: true`, `{user}` |
| A key | `<kbd>` | <kbd>Esc</kbd> |
| A term on first definition | Italic, once | Olisar keeps a *profile* of each member. |
| Text the reader types or the bot replies with | Code if it's exact, quotes if it's an example | Ask "catch me up". |

Don't use bold for emphasis in running text. If a sentence needs emphasis to be understood, rewrite the sentence.

### Links

- Link to another docs page with `[text](#page-id)`. The text says where it goes ("see [Remote access](#remote)"), never "click here".
- Link to a console tab with `[text](tab:id)`. Valid ids: `persona`, `behavior`, `messages`, `channels`, `access`, `knowledge`, `members`, `extensions`, `keys`, `usage`. On the public site these render as plain text, so the sentence must still make sense without the link.
- Link out with a full `https://` URL.
- Link a page at most once per section, on first mention.

### Callouts

The syntax is `:::tip`, `:::note`, `:::warning` or `:::info`, an optional title on the same line, the body, then `:::` on its own line.

| Kind | Use it for |
| --- | --- |
| `:::note` | A condition or exception that changes what the reader does: only on Windows, only for the operator, only in DMs. |
| `:::warning` | Something that can't be undone, loses data, exposes data, or breaks the bot. |
| `:::tip` | A faster or better way to do what the section describes. |

At most two callouts a page, never two in a row, and never one that only repeats the paragraph above it. A title is optional and should be a full statement ("Deleting a bot deletes its data"), not a label ("Important").

### Code blocks

Fence code with three backticks and a language (`bash`, `ts`, `js`, `json`, `py`). Put only the command or code in the block; explain it in the sentence before. Comments inside a block are fine when a line needs one.

### Examples

Use concrete examples: a real-looking channel name, a real command with real options, a plausible member question. Label made-up conversations as examples. Prefer one good example to three thin ones.

## What to leave out

These docs get rewritten, not appended to. When a fact doesn't help the reader use Olisar, cut it.

- History and change notes: "used to", "now", "since 2.0", "no longer", "new in". Describe how it works today.
- Internals the reader can't act on: which process, thread, table, queue, file or library does the work. Keep internals only when they explain a behavior the reader will notice, or when the reader is a developer who needs them.
- Facts that belong to another page. Link instead.
- Restating the heading, the page title or the previous paragraph.
- Defensive caveats about rare edge cases, unless the edge case loses data or locks someone out.
- Exhaustive lists of every option when the console already shows them. Document the ones that need explaining.
- Marketing: why Olisar is better, how it "feels", what it "aims" to do.

## Checklist before you finish a page

1. Does the first sentence say what this is or what it lets you do?
2. Is every fact on the page true of the current code? Check the source, not the old page.
3. Is every UI label written exactly as the console shows it?
4. Could a table replace a list of term-and-explanation pairs?
5. Is anything here also explained on another page? Keep one and link to it.
6. Read it aloud. Rewrite anything that sounds like a press release.
