# better-ui proof pass

Video evidence for the nine UI-polish fixes, captured against the real console.

| File | What it is |
| --- | --- |
| `out/proof.mp4` | 62s walkthrough, one scene per fix (h264, plays anywhere) |
| `out/proof.webm` | Same recording, as Playwright wrote it |
| `out/exit_filmstrip.png` | The 140ms overlay exit, frame by frame via CDP screencast |
| `record.py` | The script that produces both |

## Running it

Needs the console on `:5199` with the fixture backend, so every page renders populated
with no database and no OAuth:

```sh
cd web && USAGE_MOCK=1 PORT=5199 npx vite
cd design/proof && python3 record.py
```

Playwright drives the system Chrome (`channel="chrome"`), so there is no browser download —
it does need `playwright install ffmpeg` once for video encoding.

## Scenes

1. **Toggle knob** — `transform: translateX(16px)`, was `transition: left`. Ends on the computed property.
2. **Press feedback** — `scale(0.96)`, was `0.97`.
3. **Save dock** — reads its own padding/radius/height back: 12 + 4 = 16, concentric.
4. **Segmented control** — `4px pad + 8px chip = 12px shell`, was 3px.
5. **Copy → copied** — the cross-fade, magnified 3.4×.
6. **Overlay exit** — the ⌘K palette opening and closing.
7. **FAB + toast stack** — the dock comes up, the FAB lifts on `translate`, and the FAB is
   pressed *while lifted*: the state where the press scale used to be eaten.

## Notes for whoever runs this next

**The context needs clipboard permission.** `CodeBlock`'s copy handler wraps
`setCopied(true)` in the same `try` as `navigator.clipboard.writeText`, so without the grant
the write throws, the catch swallows it, and the glyph never swaps — scene 5 records a
button being clicked and nothing happening. That is a recording artifact, not a bug.

**Small controls are magnified, never restyled.** `zoom` goes on an *ancestor*, so the
control still lays out and animates exactly as it ships. At 1280×800 a 15px cross-fade and a
1px padding change are real but invisible on camera.

**The exit can't be slowed from outside.** `playExit` removes the ghost on a 140ms JS timer,
so overriding `animation-duration` in CSS just truncates it. That is why the filmstrip exists:
CDP `Page.startScreencast` samples at compositor rate (~16ms), where `page.screenshot()` takes
~580ms per frame and cannot see a 140ms animation at all.

**This pass changed the exit curve.** The first recording showed the exit cutting in a single
frame. `--ease-out` is heavily front-loaded — at 45ms into 140ms the card was already at
opacity 0.12 — which is right for an arrival and wrong for a fade. The drop still rides
`--ease-out`; the opacity ramp is now linear, and spends the full 140ms.
