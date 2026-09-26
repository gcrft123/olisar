# Split onboarding

A standalone prototype of first-run setup in two halves: the wizard on the left, and on the right a faded 3D form, set partly off the window's edge, that changes with each step. Once a server is running healthy, the form moves to the middle of the window and becomes the final screen. Nothing here is wired into `web/` or `DESIGN.md`.

Open `index.html` in a browser, from disk or from any server. It's one file, and it only reaches the network for the IBM Plex Sans and JetBrains Mono webfonts. It's built from `src/` by `python3 design/onboarding-split/src/assemble.py`, which needs nothing but Python 3.

| File | What it is |
| --- | --- |
| `index.html` | The screen. |
| `src/` | What it's built from: `engine.js` (the form and the memories' dust), `brain.js` (the final screen's change, motion and mock feed), `app.js` (the wizard, the panels and the corner), `style.css`, the vendored Preact + htm bundle with its licences, and two images. |
| `explorations/screen.jpg` | The screen on the Bot token step, at 1440×900. |
| `explorations/brain.jpg` | The final screen, at 1440×900. The bot's avatar in the middle is the mock's. |
| `explorations/brain-open.jpg` | A reply opened on the final screen. |
| `explorations/brain-transition.jpg` | The change from the stats screen to the final screen, at six points along it. |
| `explorations/drift-steps.jpg` | The form through every step. |
| `explorations/looks.jpg` | The four ways of drawing the form that were tried. Drift is the one kept. |

## The wizard

The left half runs `web/src/setup.tsx` ported to Preact and htm: the same steps, fields, live checks, polls, errors and copy, in the same order. The bot token and client secret carry their placeholders from before the setup rebuild (`your bot token`, `client secret`). The frame around them is laid out like a conventional SaaS onboarding: a thin icon rail on the far left, then the wizard filling the rest of its half with the progress bar, the heading, the step, and the step's buttons straight under it. Past 760px of content the column stops growing, so a very wide window doesn't stretch the fields and cards into hundred-character lines. The three hosting choices sit side by side, stacking once the column is too narrow for three.

The rail holds the logo, then settings, then the docs. The docs slide out from behind the rail over the wizard as the whole docs site (`docs/docs.html`, embedded at build time without its marketing bar), with its own section nav and search; the button turns into a left chevron that closes them, and Escape does too. The Deploy step keeps only a note that opens them at Host on a server. The click-by-click Oracle Cloud steps the wizard used to carry aren't in the docs source (`web/src/docs.tsx`) yet. On a phone the rail becomes a bar across the top.

The buttons put the primary first (Continue, Deploy to server, Finish & start Olisar) with the one other way out beside it: Back, or on the first step, Connect to existing server, which the app keeps behind a hover and this layout shows outright. On a phone the primary takes the full width and the other button sits under it. A long step scrolls the half as one page, and whatever arrives at the bottom of it (a refused Continue's reason, the deploy notice, a failure) is scrolled into view.

A wrong value shakes its field and marks it in the danger colour until it's edited. That covers a token, secret or key that Discord or Google turns down, the moment the answer arrives, and whichever field a refused Continue is about when the value is at fault: empty, or turned down. A check that's still running, or couldn't reach Discord, isn't the value's fault and doesn't shake. Under reduced motion the field stays still and its fill flashes instead. A refused Continue's reason also clears once it stops being the reason, so the next press says what's in the way now; the app keeps the old reason on screen until then.

After a deploy or a connect, the server panel (the stats screen) shows the status rows (console address, server, version, uptime), the redirect URL to register, and Open console, Stop or Start, and Reconnect. The redirect field is only there until Discord lists the address: it turns to Added, holds long enough to be seen, and goes, and the buttons under it close the gap. The panel goes through every state the real one reports: Checking, Starting, Running, Updating (the Version row carries the update), Unhealthy, Unreachable, Stopping and Stopped, each with the app's own wording. `?end=server` opens straight on it. A Preview strip in the corner holds it in any one state, takes the redirect away and gives it back, and adds a memory on demand.

The backend is `web/mock/fixture.ts`, with the same delays:

- A value that starts with `bad` takes that step's failure path: a bot token, a client secret, a Gemini key, a Tailscale key, or a VM address (the deploy fails with its install log; the connect can't reach it).
- A bot token containing `intents` holds the Bot step until the intents come on, 8 seconds in.
- The redirect URLs register themselves 5 seconds after the sign-in step starts watching, and the bot joins a server 4 seconds after that.
- Connecting to an address ending in `.9` finds two installs and asks which bot this is.
- `?second` sets up a second bot, so Deploy offers the server another bot already runs on.

## The final screen

When the server is running and healthy and the redirect is listed, the stats screen gives way, 1.2 seconds after the last of those comes true, so the redirect's Added is seen first. The form moves to the middle of the window. The panel's title, status, Open console, Stop server and settings shrink into the top-left corner, with the uptime beside the status; the gear carries a dot while an update is waiting and opens Settings at Updates, since the update button otherwise lives only on the stats screen. The rail and the docs go. Any other state (stopping, updating, unhealthy, unreachable, the redirect taken away) brings the stats screen back at once. `?end=brain` opens straight on the final screen, as a server that has been up a few hours.

The form carries the bot's own face: its avatar, faint, filling most of the form under the dust, with its name across the middle. Around it float memories of what Olisar has been doing, each a small sphere of the same dust with its words set inside it:

| Memory | What it shows |
| --- | --- |
| A reply | The pfp and name of whoever it answered, a preview of what it said, and how it was called: /ask, Name trigger, Mention, Reply, Chimed in (a proactive reply), or /catchup. Never a DM. |
| New member | Their pfp and name. |
| Member sync | How many members the roster sync knows. |
| Impression | Whose, and its first sentence. |
| Memory | A fact saved about someone. |
| Glossary | A fact about the server. |
| Status | A custom status the bot set itself. |
| Knowledge | A source it read, and how many passages. |
| Reminder, Image | Who it was for, and the reminder or the prompt. |
| Health check | Closer in than the rest, updated in place every 30 seconds. |

The memories sit on one ring round the form, a wide ellipse, centred on it whatever their sizes, with the same straight-line gap between neighbours' edges all the way round. Where a memory would overlap the corner cluster (on a smaller window) that stretch of the ring is left out; otherwise the ring closes on itself. A new memory starts as a thought: the form livens for a moment, then dust streams out of the side of it that faces the widest gap and forms the sphere, and the others even out round it. Older memories dim, and past eight (fewer on a smaller window) the oldest lets go of its words and its dust flows back into the form. A memory's words are out of focus, soft and faded, until it's looked at, and they fade out well inside the sphere's rim; an address too long for the width starts at the left and trails off. Hovering or focusing one brings it into focus: it stops, grows, brightens, adds a line (the channel, how many messages an impression came from), and a faint thread of dust runs from it back to the form. The heartbeat ripples each time a check passes.

Clicking a memory opens it. Its sphere comes to the middle and grows, the form draws back behind it, the bot's face steps away, and the other memories shrink and hush at the edges. Then the memory shows its context:

| Memory | Opened |
| --- | --- |
| A reply | The message it answered, then the whole reply under the bot's avatar. |
| Impression | The whole impression, and how many messages it came from. |
| Memory, Glossary | The fact, and the message it came from. |
| Reminder | The request, then the reminder the bot sent. |
| Image | The request, and the picture. |
| New member | A larger pfp, and their roles. |
| Member sync | More of the people it found. |
| Status | How it was set. |
| Knowledge | The full address, the passages, and who added it and how. |
| Health check | Each thing the check covers. |

A back button (a left chevron) at the sphere's upper left, or Escape, sends it back to its own place on the ring. By keyboard the corner comes first, then the memories newest first; Enter opens the focused one and moves focus to Back, and closing returns focus to the memory. The memories and Back show no focus ring: a focused memory peeks, and Back takes its hover look.

The change between the two screens runs on one clock that can turn round partway. Going to the final screen, the rows fade, the rail slides away while its logo stays, the title, badge and two buttons travel into the corner (the corner's own copies take over from the panel's in a few frames and carry on), the form glides to the middle as it shrinks, and the memories bud out one after another. Coming back, the memories return into the form first, then the rest runs backwards. Focus follows: to the same control on the other side, or to the heading when that control is disabled. Under reduced motion nothing travels: the two sides cross-fade, the form fades out and back in where it's going, and memories fade in where they sit. Below 1000px, where the stats screen has no form, the form gathers in the middle instead. Without WebGL2 the memories get hairline rings.

The bot keeps no activity feed today; the one structured feed is the admin audit log, and replies and their triggers exist only as log lines. The memories here are a mock with the fields a real feed would need, drawn from where each lives now: replies from the bot's own message rows and the trigger in `bot/cogs/conversation.py`, joins and the roster sync from `bot/cogs/members.py`, impressions from `persona_summary`, memories from the `remember` tool, glossary facts from `GuildFact`, the custom status from `presence.py` and `set_status`, knowledge from the ingest log, reminders and images from their tools, and health from the Docker healthcheck against `/api/health`. Pfps are drawn in the page: Discord's default avatars for some names, seeded pictures of planets for the rest.

The gear and every "Report a problem" open a stand-in for Settings with the console's sections. Feedback arrives filled in, as it does in the app. After Finish the left half shows the screen the app goes to next (sign-in, or the server panel), whose buttons do nothing here.

## The form

Each step has a form, and the symbol is carried by a quality of the form (its openness, its count, its motion) rather than by a picture of anything:

| Step | Form |
| --- | --- |
| Where it runs | A closed pebble for this machine alone. The pebble with a way through it for shared. For a server, a body drifting up and away with a few smaller ones trailing back toward here. It changes as the choice does. |
| Bot token | A shell opened in six places, with a nucleus inside that swells when Discord accepts the token. |
| Remote access | The way through opens all the way into a ring. |
| Sign-in | A band that arrives back where it left, turned over once on the way round. It broadens when Discord lists the redirects. |
| Your server | One body among several on a shared orbit. They draw in when the bot joins. |
| Gemini key | The surface carries waves out from one point, stronger once the key works. |
| Deploy | One loop with no end to it, turning on its own. |
| Connect to an existing server | A small body folding into a larger one that was already there. |
| Done | One whole body. |

On the server panel the form is the server's state: dim while it's being checked, gathering as it starts, whole and calm while it runs, a gap sweeping up through it while it updates, a steady tremor while it's unhealthy or can't be reached, and drawn in, still and dim once it's stopped. On the final screen it's the whole body, livening before each reply and pulsing as each memory buds.

It also follows what the wizard is waiting on. The surface gets livelier while a check runs or Discord is being watched, and each confirmation (the token accepted, the redirects listed, the bot joining, the key working, remote access going live) passes through it once. A wrong value makes the dust shiver off the surface for a moment before it's pulled back.

The form is drawn as dust: 60,000 particles held to the surface by transform feedback and carried along it, faint where the surface faces you and brightest at its edges. On first load they gather out of a loose cloud. Three other looks were tried on the same forms (`explorations/looks.jpg`): horizontal hairline contours, a satin graphite solid shaded by a matcap rendered in Blender, and trails circling the form. The contours read too literally, the solid too heavily, and the trails too busily, so only Drift is left in the page.

### How it's built

One WebGL2 canvas. Every form is a signed distance field, and a change of step moves weight between three slots on critically damped springs, so pressing Back halfway through a change turns it around without a jump. The form sways instead of spinning, so each keeps the angle it was drawn for. The memories are a second pass that isn't simulated: each grain has a fixed place on its sphere, turning on an axis of its own, so a sphere sits exactly where the page puts its words and a bud reverses by construction. The page runs the change and the memories' motion itself and hands the engine a framing and a list of spheres each frame. `?debug=frame` rings where it thinks the form and each memory are, and `?quiet` holds the feed to its first memories.

On the wizard and the stats screen the form keeps to a grid track of its own and never goes behind text, the rule `DESIGN.md` sets for the marketing site's ornament. Its canvas is a layer the size of the window, so it can cross to the middle for the final screen, and a mask keeps the dust off the wizard's half. The final screen breaks that rule on purpose: each memory's words sit inside its sphere, which is drawn rim-heavy with a little dark under the words. Below 1000px the layer is withheld (except on the final screen) and stops rendering, and a hidden tab renders nothing. A slow GPU gets a smaller canvas and fewer particles instead of a stutter. Under reduced motion everything runs at about a third of the speed instead of stopping. Without WebGL2 the wizard takes the whole window.

The page keeps the console's tokens and its 110% interface size, so it measures like the app it would replace.
