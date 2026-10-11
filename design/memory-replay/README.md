# Memory replay

A standalone prototype of the memory replay: a fullscreen walk through a server's history, narrated by Olisar and played out in the form's dust. It covers the whole path for an example server called Hollow Pines: the start sheet you'd open from the console, the remembering screen while Olisar reads the history, the Play screen it ends on, and a six-chapter replay.

Open `index.html` in a browser, from disk or from any server. It's one file, and it only reaches the network for the IBM Plex Sans and JetBrains Mono webfonts. It's built from `src/` by `python3 design/memory-replay/src/assemble.py`, which needs nothing but Python 3. `explorations/` holds stills of each stage at 1440×900, one at phone size, and `states.jpg`, a run that stopped partway.

| File | What it is |
| --- | --- |
| `index.html` | The prototype. |
| `src/engine.js` | `web/src/form.ts` in plain JS, with what the replay adds (below). |
| `src/replay.js` | Everything else: the camera, the memories, the narration, the beats, the start sheet, remembering and Play. |
| `src/style.css` | The console's tokens and the prototype's styles. |
| `src/assets/` | The console logo and Olisar's default avatar. |

`?at=start`, `?at=remember`, `?at=play` and `?at=replay` open straight on a stage, and `&beat=` with a beat's id (`cold`, `first`, `five`, `arrivals`, `night`, `goose`, `spread`, `arrive`, `pinecone`, `now`) opens the replay on that beat. `&pick=1` or `&pick=2` plays a runner-up pick, and `&auto` turns autoplay on.

Three more show what a real server can throw at it:

| Parameter | What it shows |
| --- | --- |
| `?state=indexing` | The search index is still reading history: Start becomes Start anyway, and says what a replay now would miss. |
| `?state=quota` | Not enough of today's Gemini allowance left: Start is off, and the sheet says when it resets. |
| `?state=short` | Too little history for a replay: Start is off. |
| `?state=busy` | Another admin started one: Start becomes Watch it and joins the run partway. |
| `?state=paused` | The allowance runs out partway through remembering. The run waits for midnight Pacific by itself. |
| `?state=failed` | The bot goes offline partway through. Try again carries on from where it stopped. |
| `?server=` | Any server name, up to Discord's 100 characters and in any script. |
| `?stress` | The worst names and messages: 32-character names, right-to-left and CJK text, emoji, a long server name, and a message long enough to be cut. The Demo strip at the top restarts the whole thing or skips to the replay.

## The path

The start sheet has no settings. It sits under the form, where remembering and Play sit too, so the form doesn't move until the replay starts. Before anything runs, it says what the replay reads and what that costs: the messages in the indexed channels (which ones is set on the Channels tab, and people who've opted out of search aren't in the index), how long it takes, and how much of the day's Gemini allowance.

Remembering is fourteen seconds here, standing in for minutes on a real server. Four steps run under the form: gathering (the form draws streams of dust in from far off while the count of messages read climbs), counting (people, channels, years), reading (each moment it keeps buds off as a sphere, kept out of focus so nothing is spoiled), and writing.

Play is where remembering ends. The form stays where it was, and under it are the replay's title, how many chapters and minutes it runs, and Play. There's nothing to change: the replay plays as Olisar wrote it.

## The replay

Click anywhere, or press → or Space, for the next beat; ← goes back, Escape returns to Play. The chapters run along the bottom and jump on click. The play button top right autoplays.

| Chapter | Beat | What happens |
| --- | --- | --- |
| Before I was here | `cold` | The form collapses to a single grain of light. The server's creation date, worked out from its ID. |
| | `first` | The first three messages type in; the grain grows as each lands. |
| | `five` | The first five members bud off as spheres. Hover for their first words, open one for more. One has left, so shows as Someone. |
| Everyone else | `arrivals` | The count runs from 5 to 1,000 over four years with dust falling into the form for everyone who joined, and the form grows with it. A scrubber takes it anywhere; three milestones appear as it passes them, the shoutout day as one burst. |
| The longest night | `night` | The form moves to the corner and one sphere comes out: the busiest day in #general. Opened, it's the thread. |
| (named after the pick) | `goose` | Olisar's choice of the one thing that most belongs to this server, with the message that started it. The prototype has three: "goose" (a word), Friday movie night (a ritual), the lighthouse (a Minecraft build). How the pick is made is in `PICK.md`. |
| | `spread` | The pick's dust flies out to where it came up again, leaving labels and a little dust at each. Clicking the sphere runs it again. |
| Then I showed up | `arrive` | The form takes the bot's shape from setup, and Olisar's first reply types in. From here the narration is lived, not read. |
| | `pinecone` | A persona change from the activity log. The form shivers at the new name. |
| Now | `now` | Every memory the replay showed settles on one ring round the form, the final server screen's layout, with Olisar's face faint inside it. Olisar asks what it should remember from this year; the answer is saved and buds as a new sphere. |

Opening any memory brings it to the middle, large, with the form drawn back behind it and the rest stepped out to the edges, like the final server screen. Back, Escape, or a click outside closes it.

Thrown dust (arrivals, the pick) never crosses the narration, the corner or the chapters. Under reduced motion the form runs at a third of the speed, thrown dust at about 40%, and the words only fade. Without WebGL2 the memories get hairline rings.

Whatever a member or Discord wrote is set as text, never as markup. Names sit in `<bdi>` so a right-to-left name can't reorder the English around it, and messages take their own direction. A quoted message longer than 220 characters is cut at a word, and a screen reader hears each one whole, once, instead of letter by letter as it types. The arrivals count doesn't announce every frame. The last answer is one line of up to 120 characters.

Below 900px the narration moves under the form. The first five sit round it, the pick's spots stack in two columns between its sphere and the words, and the last screen's memories sit above and below the question, never beside it. On a touch screen the first hint says to tap, and the last question doesn't open the keyboard by itself.

## What the engine adds

`src/engine.js` is `form.ts` with four additions, none of them in the app yet:

| Addition | Why |
| --- | --- |
| Shape 10, remembering | One body with six spiral streams of beads walking inward and folding into it. |
| `feed(rate)` | How fast dust respawns in the loose cloud round the form, so the streams stay fed. |
| `density(x)` | Draws only part of the dust, so the form can shrink to a single grain without summing to a white blot. |
| A floor in the mask (`Framing.yb`) | Fades dust out above words set under the form (the remembering steps). |

It also leaves out the interface-size zoom, so its layers measure in CSS px, and has room for 14 memories instead of 12. The page calms the surface warp as the whole body shrinks, since at a tenth of its size the same warp tears it apart.

## What's mocked

The server, its members, messages and numbers are all written for the prototype. Avatars are drawn in the page: Discord's default avatars for some names and seeded pictures of planets for the rest. "Open in Discord" shows a toast. Nothing reads from or writes to a backend, and the answer to the last question lasts until the page reloads.
