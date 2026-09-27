# Bot status in the closed foot sheet

Seven ways to show the bot's state on the closed sheet at the foot of the console's rail (`FootSheet` in `web/src/App.tsx`, "Foot sheet" in `web/DESIGN.md`). The first was built first. What's built now is 4's row with 6's glow: see "What was built" below. This page isn't wired into `web/`.

Open `index.html` in a browser, from disk or from any server. It's one file with the Solar glyphs inlined, and it only reaches the network for IBM Plex Sans. The "Starting" column animates.

| File | What it is |
| --- | --- |
| `index.html` | The grid: one row per way of drawing it, one column per state `BotPower` reports. Each cell is the bottom of the 244px rail at 100%, with the last nav row above the sheet. |
| `explorations/variants.jpg` | The grid at 2x, with the spinners stopped. |

The six states are the ones `BotPower` already tells apart: online, starting, rate-limited (connected, with every chat model parked), switched off, refused or crashed (Discord refused it, or it stopped on its own), and unknown (no reading from the backend yet).

## The seven

1. **Dot.** What's built: a 7px dot with a soft halo in the nav's icon column, and `BotPower`'s own label in the nav's label column. Switched off and unknown are the same gray dot, and only the label tells them apart.
2. **Power button.** The power button the sheet opens, at 22px, in the colors the full one takes for each state, with its spinning arc while starting. The closed sheet previews the control inside it, and the bolt stays even when the color goes gray. It looks like a button, so someone will tap it expecting power: either make it work the way the full one does (tap to power on, hold to power down) or accept that tapping it opens the sheet.
3. **Lit grabber.** The grabber carries the color, and a short word centered under it says what the color means. The closed sheet drops to 40px. It's the most iOS of the seven, and it gives the handle a second job.
4. **Status badge.** The console's `Badge`, with the tones and glyphs `DESIGN.md` gives them: play for online, the spinner for starting, stop for switched off, close for refused. This is the one that disagrees with what's built about switched off: the Badge table makes Offline `warning`, so a bot the operator switched off on purpose turns amber. The "Bot" label on the left is only there to give the row a subject.
5. **Presence.** Discord's presence marks on the bot's avatar: a filled dot for online, idle's crescent for rate-limited (up, not answering), do-not-disturb's bar for refused, a hollow ring for switched off, a turning arc while starting. Shape as well as color sets the states apart, and Discord users already read them. The second line is who's signed in, which the closed sheet otherwise hides. It needs 9px more height, and the marks are 13px.
6. **Edge light.** The sheet's border takes the state's color and glows onto the rail above it, and a highlight runs along the edge while starting. The row stays neutral. It's the loudest of the seven for a state that rarely changes, and a glow under a sheet that isn't floating goes against `DESIGN.md`'s rule on shadows.
7. **Uptime strip.** The last hour in 3-minute ticks, newest on the right and taller. It's the only one that shows a bot crashing and coming back. The status poll only runs while the console is open and nothing stores it, so drawing this needs the backend to keep a status history.

## What was built

The row from 4 (the bolt, "Bot", and the badge) with the glow from 6, made subtler: a 42% border instead of 55%, a 5% tint instead of 9%, and the halo at 45% alpha. The glow follows the badge's color and stays off for switched off and unknown, as it did in 6. Two states move: while the bot starts or stops, a light slides back and forth along the top edge, and while it's rate-limited, the glow breathes.

## Which one, before the choice

Power button, if the closed sheet should stay one quiet row: it's the only one whose mark is the thing you'll find when you open the sheet. Presence, if the closed sheet should also say who's signed in. The uptime strip is worth building once the backend keeps a history, and it sits alongside either of those rather than replacing them.
