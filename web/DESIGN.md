# Olisar — Design Guide

A dark-only design system for **Olisar**, a self-hosted AI Discord bot configured from a private admin console. The aesthetic is calm, near-monochrome, hairline-bordered — in the spirit of Resend's dashboard — with one restrained accent and a soft ambient glow behind everything.

**How to use this file:** drop it in your repo (e.g. `DESIGN.md` or `.claude/DESIGN.md`). Paste the **Design tokens** block into your global CSS, wire up the two fonts and the icon set, then build UI with the **Component recipes** below. Everything is plain CSS custom properties + HTML/JSX — no framework required.

---

## Brand & voice

- **Audience:** an operator who knows what they're doing. The tool respects their time and their data.
- **Voice:** second person ("you"), plainspoken, lightly opinionated. Say *what* a setting does and *why* in one breath, no hedging.
- **Tone:** calm, competent, a little dry. Never oversell, never exclaim, never apologize performatively. Warnings are direct and specific.
- **Casing:** **sentence case everywhere** — page titles, card titles, buttons, labels. The only uppercase is the small tracked eyebrow/section-label treatment.
- **Mechanics:** settings = a terse **label** + a one-sentence **description** with a concrete consequence. Slash commands and code in mono with a leading slash (`/ask`, `/forget-me`). Numbers are concrete ("seen 7×", "12,481 messages"). Em-dash glosses in options ("both — read & talk").
- **No emoji** in the UI chrome, ever. No unicode-as-icon.

### Writing UI copy

The heading does the work. A description earns its place only when it says something the heading can't.

- **Cut a description that restates its heading.** "Desktop app — settings for the Olisar desktop application" is one fact written twice; ship the heading alone. Same for card hints that just list the fields beneath them.
- **Cut mechanism the reader can't act on.** No "checks GitHub Releases for a new version", no "applies live, no restart needed" — if a restart were needed, the UI would say so. Describe the consequence, not the implementation.
- **Delete, don't compress.** If trimming a sentence leaves nothing a user would act on, remove the sentence. Shortening slop still ships slop.
- **Em dashes only where a human would use one.** A genuine aside (`Admins who sign in — locally or remotely — write to that database live`) or an option gloss (`both — read & talk`). Never as a stand-in for a colon, period, or comma: `Saved — live now` is just **Saved**; `Careful — you have unsaved changes.` is **You have unsaved changes.**
- **Status text states, it doesn't scold or hedge.** What happened, then the next step if there is one. No "Careful —", no "Please note", no apology.
- **Plain words over house jargon** anywhere a server admin reads: "how sure it has to be" over "minimum classifier confidence", "hits its limit" over "returns a 429", "someone else's code" over "third-party code". Keep precise terminology in the SDK reference, where the reader is a developer.
- **US spelling** throughout — behavior, customize, analyze.

---

## Design tokens

Paste into your global stylesheet. Dark-only (`color-scheme: dark`).

```css
:root {
  color-scheme: dark;

  /* Surfaces (darkest → lightest). Near-black ground; cards sit a hair above it
     and are read by their BORDER, not by fill contrast — they blend into the bg. */
  --bg: #020203;            /* app background */
  --bg-sidebar: #040405;    /* sidebar / nav rail */
  --panel: #08080a;         /* card / modal surface */
  --bg-inset: #0f0f12;      /* inset wells: inputs, chips, code, nested cards */
  --input-bg: #0f0f12;

  /* Borders — the hairlines do the structural work */
  --border: #26262a;
  --border-strong: #323237; /* controls, dividers */

  /* Text ramp */
  --text: #ededee;          /* primary */
  --text-2: #9d9da7;        /* secondary */
  --text-3: #7f7f8a;        /* tertiary / muted / placeholders */
  /* Size --text-3 against the LIGHTEST ground it lands on, not the darkest. It carries
     placeholders, eyebrows, axis labels and hints — all body-size text, none of it eligible
     for the 3:1 large-text allowance. The earlier #6a6a73 measured 3.87:1 on --bg and
     3.57:1 on --bg-inset, an AA failure everywhere it was used; #7f7f8a measures 5.09 on
     --bg, 4.70 on --bg-inset, and 4.7 on the marketing site's lighter #08080a ground. */

  /* The one accent (user-switchable at runtime; re-tints --accent-soft + glow) */
  --accent: #8a8af2;
  --accent-soft: rgba(138, 138, 242, 0.16);
  --glow-a: rgba(138, 138, 242, 0.18);
  --glow-b: rgba(138, 138, 242, 0.10);

  /* Selectable accent hues */
  --accent-violet: #8a8af2;  --accent-iris: #7c6cf0;  --accent-blue: #5b9cf6;
  --accent-teal: #2dd4bf;    --accent-green: #43cf8e; --accent-amber: #e0a458;
  --accent-rose: #f2728a;    --accent-red: #ff6369;

  /* "Primary" action surface — a bright neutral, NOT the accent. One per view. */
  --primary-bg: #ededee;
  --primary-fg: #18181b;
  --primary-hover: #ffffff;

  /* Semantic states — base / -soft (fill) / -border (edge). */
  --ok: #43cf8e;     --ok-soft: rgba(67,207,142,.14);    --ok-border: rgba(67,207,142,.34);
  --danger: #ff6369; --danger-soft: rgba(255,99,105,.13); --danger-border: rgba(255,99,105,.34);
  --warn: #e3a13a;   --warn-soft: rgba(227,161,58,.14);   --warn-border: rgba(227,161,58,.34);
  --info: #5b9cf6;   --info-soft: rgba(91,156,246,.14);   --info-border: rgba(91,156,246,.34);
  --neutral: #9d9da7;--neutral-soft: rgba(157,157,167,.12);--neutral-border: rgba(157,157,167,.28);
  /* Aliases */
  --success: var(--ok); --error: var(--danger); --warning: var(--warn);

  /* Type */
  --font-sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;

  /* Radii — generously rounded */
  --radius: 16px;       /* cards, modals */
  --radius-sm: 12px;    /* buttons, inputs, nav items */
  --radius-xs: 8px;     /* tags, chips */
  --radius-pill: 999px; /* badges, toggles */

  /* Elevation — flat by default; only floating surfaces lift */
  --shadow-card: none;
  --shadow-pop: 0 8px 28px rgba(0,0,0,.5);
  --shadow-modal: 0 24px 70px rgba(0,0,0,.5);
  /* Focus. Two rings, not one: a gap in the page ground, then the accent. A single
     accent-soft ring measures 1.20-1.25:1 against every ground in the system — the floor
     is 3:1 (WCAG 1.4.11 / 2.4.11) — and it is invisible on --primary-bg (a near-white
     surface), which every primary button is: solid accent on #ededee measures 2.38:1, and
     accent-soft is 16% of it. The gap ring is what makes the accent readable on a light
     control; the accent ring is what makes it readable on a dark one. */
  --ring: 0 0 0 2px var(--bg), 0 0 0 4px var(--accent);

  /* Interface size. Everything here is px — 34px controls, a 244px rail, chart geometry —
     so there is no single font-size to turn up. `zoom` scales the whole coordinate system
     instead, which is what the browser's own Cmd +/− does. Operator-switchable at runtime. */
  --ui-scale: 1.1;
  zoom: var(--ui-scale);
  --vh: calc(100vh / var(--ui-scale));
  --vw: calc(100vw / var(--ui-scale));
  --dvh: calc(100dvh / var(--ui-scale));

  /* Motion — quiet and quick */
  --ease-out: cubic-bezier(0.2, 0.9, 0.3, 1);
  --dur-fast: .12s;  --dur-mid: .16s;  --dur-slow: .3s;
}
```

### Runtime accent switching (optional)

The accent is a per-browser preference. To re-tint live, set `--accent`, `--accent-soft`, and the two glow vars from one hex:

```js
function setAccent(hex) {
  const c = usableAccent(hex);                 // see below — never apply the raw pick
  const n = parseInt(c.slice(1), 16);
  const rgb = `${(n>>16)&255}, ${(n>>8)&255}, ${n&255}`;
  const r = document.documentElement.style;
  r.setProperty('--accent', c);
  r.setProperty('--accent-soft', `rgba(${rgb}, 0.16)`);
  r.setProperty('--glow-a', `rgba(${rgb}, 0.18)`);
  r.setProperty('--glow-b', `rgba(${rgb}, 0.10)`);
  return c;
}
```

**Clamp a free-form pick.** The accent is not decoration: it carries links, the focus ring, the
toggle "on" track, meters, and chart series. A `<input type="color">` will happily hand back
`#000000`, and on a near-black ground that makes all of them vanish with no warning and no
route back but Reset. Lift the colour toward white — hue and saturation intact — until it
clears **4.5:1 against `--bg`**, and have the picker echo the colour actually applied:

> `#101014` was too dark to read against the console background, so it was lightened to `#6b6b80`.

The eight preset hues all clear it already; the clamp only ever touches a custom pick.

### Interface size (optional)

`--ui-scale` is the second per-browser preference, offered as 100% / 110% / 125% and defaulting
to **110%**. It is applied the same way as the accent — written to the root before first paint,
persisted in `localStorage` — and needs no layout work, because `zoom` scales the coordinate
system rather than any individual value.

Three rules come with it:

**Never write a bare viewport unit.** They resolve against the *un-zoomed* viewport and are then
scaled, so `height: 100vh` renders `--ui-scale` taller than the window — at 1.1 that was 80px of
phantom scroll below the sidebar. Use `--vh` / `--vw` / `--dvh`, which divide the factor back out:

```css
.sidebar   { height: var(--vh); }                          /* not 100vh */
.some-modal{ width: min(560px, calc(var(--vw) * 0.94)); }  /* not 94vw  */
```
Decorative shapes are the exception — the ambient glow keeps raw `vw`, because a 10% size shift
on a blurred blob is invisible.

**Correct DOM geometry only when it crosses into CSS.** `offsetWidth`, `offsetLeft` and
`ResizeObserver`'s `contentRect` are already element-local and scale-free — the Usage charts
measure with one and draw 1:1 with no adjustment at all. `getBoundingClientRect()` is *viewport*
px, so feeding it into a CSS `left`/`top` displaces the result by exactly the scale (the
delegated tooltip landed 104px off its target at 1.1). Divide by `uiScale()` at that boundary,
and scale any px constant you compare against a rect.

**Media queries do not zoom.** A breakpoint fires at a physical width while the layout inside it
is scaled, so at 1.1 the content sees `width / 1.1`. The breakpoints are content-driven and sit
well clear of common desktop widths, so this shifts *where* the collapse happens, never whether
it works — but pick new breakpoints against the effective width, not the raw one.

---

## Base layer

```css
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: var(--font-sans); font-size: 13.5px; line-height: 1.55;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.014em; }
::selection { background: var(--accent-soft); }

/* Ambient glow — two soft, accent-tinted blobs drifting behind everything.
   Atmosphere, not decoration. Disable under reduced-motion. */
body::before, body::after {
  content: ""; position: fixed; z-index: -1; pointer-events: none; filter: blur(56px);
}
body::before { width: 54vw; height: 54vw; top: -16vw; left: 4vw;
  background: radial-gradient(circle, var(--glow-a), transparent 60%);
  animation: glow-a 28s ease-in-out infinite alternate; }
body::after { width: 48vw; height: 48vw; bottom: -18vw; right: -6vw;
  background: radial-gradient(circle, var(--glow-b), transparent 60%);
  animation: glow-b 36s ease-in-out infinite alternate; }
@keyframes glow-a { from { transform: translate3d(0,0,0) scale(1); opacity:.65 } to { transform: translate3d(6vw,4vw,0) scale(1.18); opacity:1 } }
@keyframes glow-b { from { transform: translate3d(0,0,0) scale(1.12); opacity:.55 } to { transform: translate3d(-5vw,-4vw,0) scale(1); opacity:.9 } }
@media (prefers-reduced-motion: reduce) { body::before, body::after { animation: none; } }

/* Slim floating scrollbars */
* { scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent; }
::-webkit-scrollbar { width: 11px; height: 11px; }
::-webkit-scrollbar-thumb { background: var(--border-strong); background-clip: content-box; border: 3px solid transparent; border-radius: 999px; }
```

---

## Typography

**IBM Plex Sans** for the whole UI; **JetBrains Mono** for IDs, URLs, tags, slash commands, code, and numeric readouts.

```html
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400..700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

Small, dense, admin proportions:

| Role | Size | Weight |
|---|---|---|
| Docs title | 26px | 600 / −0.02em |
| Page H1 | 22px | 600 |
| Modal / section title | 18px | 600 |
| Card title / brand | 15px | 600 |
| Body / inputs / buttons | 13.5px | 400 |
| Secondary / descriptions | 12.5px | 400 |
| Eyebrow / nav label | 11px | 600 uppercase, 0.04em |

Form **labels** sit at weight **550** (a hair above medium). Body line-height 1.55.

---

## Iconography

**One source of truth: the [Solar icon set](https://github.com/480-Design/Solar-Icon-Set).** No emoji, no unicode-as-icon.

- In a React app: `@solar-icons/react`.
- Anywhere (no build): Iconify — `<iconify-icon icon="solar:user-circle-linear"></iconify-icon>` via `https://code.iconify.design/iconify-icon/2.1.0/iconify-icon.min.js`.
- **Two weights by state:** `-linear` (outline) is idle; `-bold` (filled) marks the active item (e.g. the selected nav row swaps linear → bold). Render at 16–19px inline.

Common semantic names: `user-circle` (persona), `tuning-2` (behavior), `hashtag` (channels), `shield-keyhole` (access), `book-bookmark` (knowledge), `plug-circle` (extensions), `settings`, `power`, `magnifer` (search), `copy`, `trash-bin-minimalistic`, `check-circle`, `danger-triangle`, `info-circle`.

**Logo:** a rounded-square shield with a centered star (slate blue, navy star). Place it on `--bg`, `--bg-inset`, or `--accent-soft` tiles; don't recolor it.

---

## Component recipes

Self-contained CSS + markup for the core set. Class names are illustrative — adapt to your conventions. All buttons/inputs share a **34px height**, the same radius, and the same border so they line up.

**The system has 29 components. Every one is covered below:**

| Group | Components |
|---|---|
| Buttons | **Button**, **IconButton** |
| Forms | **TextField**, **TextArea**, **Select**, **Toggle**, **Field** |
| Data display | **Card**, **Badge**, **Tag**, **StatTile**, **DocTable**, **DataTable** |
| Feedback | **Callout**, **Spinner** |
| Overlays | **Dialog**, **Modal**, **SaveDock**, **ActionMenu**, **HoverCard**, **Toast** |
| Navigation | **NavItem**, **PageNav**, **Tabs**, **Avatar** |
| Content | **InlineCode**, **CodeBlock**, **CopyField**, **Link** |

### Button & IconButton

```css
.btn { height: 34px; padding: 0 14px; display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  font: inherit; font-weight: 550; font-size: 13px; white-space: nowrap; cursor: pointer;
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--panel); color: var(--text);
  transition: background .12s, border-color .12s, transform .08s ease, box-shadow .12s ease; }
.btn:hover { background: var(--bg-inset); }
.btn:active:not(:disabled) { transform: scale(.97); }      /* subtle press */
.btn:focus-visible { outline: none; box-shadow: var(--ring); }
.btn:disabled { opacity: .55; cursor: not-allowed; }
.btn.primary  { background: var(--primary-bg); border-color: var(--primary-bg); color: var(--primary-fg); }
.btn.primary:hover { background: var(--primary-hover); border-color: var(--primary-hover); }
.btn.ghost    { background: transparent; border-color: transparent; color: var(--text-2); }
.btn.ghost:hover { background: var(--bg-inset); color: var(--text); }
.btn.danger   { color: var(--danger); }
.btn.danger:hover { background: var(--danger-soft); border-color: transparent; }
.btn.caution  { color: var(--warn); }                       /* softer/secondary destructive */
.btn.caution:hover { background: var(--warn-soft); border-color: transparent; }
.btn.sm { height: 28px; padding: 0 11px; font-size: 12.5px; }
```

Variants: **primary** (one bright CTA per view), **secondary** (the base hairline button), **ghost**, **danger** (red), **caution** (amber), and an **acting** state (disabled + a spinning ring) for "Saving…". Sizes `md` (34px) / `sm` (28px); optional leading icon.

**IconButton** — a 34×34 square (default ghost) for toolbar/row actions. On hover/focus it shows an instant dark tooltip pill (with a small downward arrow). Set a **confirm** behavior so that on click of a copy/confirm action the glyph swaps to a green `check-circle` briefly (pop animation), then reverts.

**Tooltips are for icon-only controls only.** The hover tooltip (`data-tip`, or a `title` the host migrates to one) exists to name a control that has no visible text label — i.e. an **IconButton**. Do **not** put `data-tip`/`title` on text buttons, selectors, tabs, or other labelled controls: their label already says what they do, so a tooltip is redundant noise. Add one to a labelled control only when explicitly asked.

**A tooltip is not a name.** `data-tip` is a styling hook with no accessibility semantics, and the tooltip host *removes* `title` on hover **and on focus** so the OS tooltip never doubles up — so a control named only by `title` loses its name the moment a keyboard user tabs to it. Every icon-only control carries an explicit `aria-label` as well:

```jsx
<button className="ghost icon-btn" data-tip="Export .olx" aria-label="Export extension">…</button>
```

The host sets `aria-label` from a stripped `title` as a backstop, but write it yourself: the backstop can only repeat the tooltip, and the two want different words — the tooltip is a hint (`Copy`), the label names the object (`Copy the public web address`).

### TextField, TextArea & Select

`.input` = **TextField**, `.textarea` = **TextArea**, `.select` = **Select** (add a custom chevron via a background SVG; `appearance: none`).

```css
.input, .select, .textarea {
  width: 100%; box-sizing: border-box; background: var(--input-bg); color: var(--text);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  font: inherit; font-size: 13.5px; outline: none; transition: border-color .12s, box-shadow .12s; }
.input, .select { height: 34px; padding: 0 12px; }
.textarea { padding: 8px 12px; min-height: 70px; line-height: 1.55; resize: vertical; }
.input:focus, .select:focus, .textarea:focus { border-color: var(--accent); box-shadow: var(--ring); }
::placeholder { color: var(--text-3); }
```

### Toggle (pill switch)

```css
.toggle { display: inline-flex; align-items: center; gap: 11px; cursor: pointer; }
.toggle .track { width: 38px; height: 22px; border-radius: 99px; background: var(--border-strong); position: relative; transition: background .16s; }
.toggle .knob { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: left .16s; }
.toggle.on .track { background: var(--accent); }
.toggle.on .knob { left: 18px; }
```

### Field (label + description + control)

The label, description, and control are three siblings — the label can't wrap the control, so
it has to **point at** it. Mint one id per field and wire all three; without it the control is
an unnamed edit box to a screen reader and clicking the label doesn't focus it.

```html
<div class="field">
  <label id="f1-label" for="f1">Name triggers</label>
  <div class="desc" id="f1-desc">Comma-separated. Including one addresses Olisar.</div>
  <input class="input" id="f1" aria-describedby="f1-desc" placeholder="olisar, oli">
</div>
```
```css
.field { margin-bottom: 17px; }
/* `.flabel` is the same treatment for a field whose body holds no focusable control (a
   read-only key box, a copy row) — a <label for> there would point at nothing. */
.field > label, .field > .flabel { display: block; font-weight: 550; font-size: 12.5px; margin-bottom: 6px; }
.field .desc { color: var(--text-2); font-size: 12px; margin: -3px 0 8px; line-height: 1.5; }
```

A **Toggle** is a `div[role=switch]`, so `for` can't reach it: give it `aria-labelledby` pointing
at the same label id. A toggle used **outside** a Field and without a visible `.lbl` must carry
its own `aria-label` — otherwise it announces as "switch, on" with no subject.

### Choice groups (mode cards, segmented pickers)

A group of mutually exclusive cards is a **radiogroup**, not a row of clickable divs: `role="radiogroup"`
on the container with an `aria-label`, `role="radio"` + `aria-checked` on each card, roving `tabIndex`
(`0` on the selected one, `-1` on the rest), arrow keys to move, Space/Enter to pick. The same roving
pattern covers a `role="tablist"`, whose panel takes `role="tabpanel"` + `aria-labelledby`. Styling is
unchanged — `.mode-card.sel` and `.dev-tab.active` still carry the visual state.

### Skip link

Any surface with a nav rail ahead of its content opens with one. Offscreen until focused, then a
normal `--panel` chip at the top left:

```css
.skip-link { position: fixed; z-index: 300; top: 12px; left: 12px; padding: 9px 14px;
  border-radius: var(--radius-sm); background: var(--panel); border: 1px solid var(--border-strong);
  color: var(--text); font-size: 13px; font-weight: 550; text-decoration: none; box-shadow: var(--shadow-pop);
  transform: translateY(calc(-100% - 20px)); transition: transform var(--dur-mid) var(--ease-out); }
.skip-link:focus-visible { transform: none; outline: none; box-shadow: var(--ring), var(--shadow-pop); }
```

### Card (the flat panel)

```css
.card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 20px 22px; margin-bottom: 14px; box-shadow: var(--shadow-card); }
.card > h3 { font-size: 13.5px; margin: 0 0 2px; }
.card > .hint { color: var(--text-2); font-size: 12.5px; margin-bottom: 15px; line-height: 1.5; }
```

### Badge & Tag

```css
.badge { display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px; border-radius: 999px;
  font-size: 11px; font-weight: 600; text-transform: capitalize;
  background: var(--bg-inset); border: 1px solid var(--border); color: var(--text-2); }
.badge.success { color: var(--ok); background: var(--ok-soft); border-color: var(--ok-border); }
.badge.error   { color: var(--danger); background: var(--danger-soft); border-color: var(--danger-border); }
.badge.warning { color: var(--warn); background: var(--warn-soft); border-color: var(--warn-border); }
.badge.info    { color: var(--info); background: var(--info-soft); border-color: var(--info-border); }

.tag { font-family: var(--font-mono); font-size: 11.5px; padding: 1px 7px; border-radius: 8px;
  background: var(--bg-inset); border: 1px solid var(--border); color: var(--text); }
```

### StatTile (metric) & Spinner

A single metric — big number over a muted label, on an inset well; compose several in a grid for overview rows. The spinner is a minimal accent ring for quiet loading states.

```css
.stat { background: var(--bg-inset); border: 1px solid var(--border); border-radius: 14px; padding: 15px 16px; }
.stat .n { font-size: 25px; font-weight: 650; letter-spacing: -.02em; line-height: 1; }
.stat .k { color: var(--text-2); font-size: 12px; margin-top: 6px; }
/* grid: display:grid; grid-template-columns: repeat(auto-fit, minmax(150px,1fr)); gap:12px; */

.spinner { display: inline-block; width: 18px; height: 18px; border: 2px solid var(--border-strong);
  border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 1.6s; } }
```

### Callout (Resend-style, no eyebrow)

A colored border + dark tinted fill + a left icon. Tones: `tip`→ok, `note`/`info`→accent, `warning`→warn.

```css
.callout { display: flex; gap: 12px; align-items: flex-start; padding: 14px 16px; border-radius: var(--radius);
  font-size: 13.5px; line-height: 1.6;
  border: 1px solid color-mix(in srgb, var(--cc) 34%, transparent);
  background: color-mix(in srgb, var(--cc) 9%, var(--panel));
  color: color-mix(in srgb, var(--cc) 24%, var(--text)); }
.callout .ic { color: var(--cc); margin-top: 1px; }
.callout a { color: var(--cc); text-decoration: underline; text-underline-offset: 2px; }
.callout.warning { --cc: var(--warn); }
.callout.note    { --cc: var(--accent); }
.callout.tip     { --cc: var(--ok); }
```

### Toast (bottom-right status)

Same tinting as the callout, fixed bottom-right, with a filled-circle icon in the state colour; slides in from the right. States: success / warning / danger / info / neutral.

```css
.toast { position: fixed; right: 24px; bottom: 24px; display: flex; align-items: center; gap: 13px;
  min-width: 300px; max-width: 430px; padding: 13px 15px; border-radius: var(--radius);
  border: 1px solid var(--tc-border); background: color-mix(in srgb, var(--tc) 11%, var(--panel)); box-shadow: var(--shadow-pop);
  transform: translateX(24px); opacity: 0; transition: transform .3s var(--ease-out), opacity .24s ease; }
.toast.show { transform: translateX(0); opacity: 1; }
.toast .ic { color: var(--tc); font-size: 22px; }      /* solar:check-circle-bold etc. */
.toast .title { font-size: 13.5px; font-weight: 650; }
.toast.success { --tc: var(--ok); --tc-border: var(--ok-border); }
.toast.danger  { --tc: var(--danger); --tc-border: var(--danger-border); }
.toast.warning { --tc: var(--warn); --tc-border: var(--warn-border); }
.toast.info    { --tc: var(--info); --tc-border: var(--info-border); }
```

### Overlays (Dialog / Modal / SaveDock / ActionMenu / HoverCard)

- **Dialog** (centered info+action): blurred backdrop `rgba(0,0,0,.55)` + `backdrop-filter: blur(3px)` fading in; the card (`--panel`, `--border-strong`, `--shadow-modal`) scales-and-lifts from `translateY(12px) scale(.96)` → `0/1` over `.22s var(--ease-out)`. Optional tinted icon tile (46px, `--radius` 15px) + footer actions. Close on backdrop click / Escape.
- **Modal** (full-UI sheet): same backdrop; a `min(900px,94vw) × min(620px,90vh)` sheet with a header (title + close ×), a scrollable body (put a two-pane nav+content inside), and an optional footer.
- **SaveDock** (unsaved-changes bar): `position: fixed; bottom: 22px; left: 50%`; slides up from `translate(-50%,170%)` → `translate(-50%,0)` over `.3s var(--ease-out)`. A `--panel` pill, message + Reset/Save.
- **ActionMenu** (click-to-open dropdown anchored to a trigger): a `--panel` menu (`--border-strong`, `--shadow-pop`, `--radius-sm`) that pops in with a `.14s` fade + scale from the top (`translateY(-6px) scale(.97)` → `0/1`). Items are `7px 9px` rows with a leading icon, optional right-aligned mono shortcut, hover → `--bg-inset`; a `danger` item is `--danger` (hover `--danger-soft`); thin `--border` dividers and uppercase section labels. Closes on outside-click / Escape / select.
- **HoverCard** (expand-on-hover detail, e.g. a roles/members row): a `--panel` card (`--border-strong`, `--shadow-pop`, `--radius`) absolutely positioned above the trigger; fades + lifts in (`translateY(6px) scale(.98)` → `0/1`, `.15s`) **after a ~.18s delay**, closes immediately on leave. Make the trigger `tabindex=0` so `:focus-within` opens it too.

**Every overlay goes through one shell.** Hand-rolling the backdrop per call site is how a
system ends up with some dialogs that close on Escape and some that don't. The shell owns:

| | |
|---|---|
| Semantics | `role="dialog"`, `aria-modal="true"`, `aria-labelledby` on the card's own `<h2>`/`.confirm-title` (or `aria-label` when there's no visible title) |
| Focus | move to the first focusable on open — unless an `autoFocus` input already claimed it — trap Tab/Shift-Tab inside, and **return focus to the trigger** on close |
| Escape | always closes, except while an irreversible action is in flight (`dismissable={false}` during a publish, a move, an install) |
| Backdrop | closes on **`mousedown` on the backdrop itself** — an `onClick` handler fires when a text selection starts inside the card and releases outside it, closing the dialog mid-drag |

The visual recipe above is unchanged; the shell only adds behaviour. A drawer that stays
mounted while closed (so it slides rather than pops) sets `inert` while hidden — `aria-hidden`
alone leaves its controls in the tab order, hidden from the screen reader that would name them.

### StepsDialog (the post-download popup)

Fires on the marketing site after a download click: a Dialog whose body is a short **numbered handoff** telling someone what to do with the file they just got. Wider radius (18px) than a console dialog, and a display-serif title — the marketing site adds **IBM Plex Serif** at 400 for headings; in-console, use `--font-sans` at 600 instead.

Plex Serif is the superfamily sibling of `--font-sans`, so the site reads as one type system rather than a pairing. It is wide and low-contrast with a large x-height (51.6 against a Garamond's 38.6 at the same size), which means display sizes run *smaller* in px than a high-contrast serif would while reading the same size or larger, and it holds at weight 400 on the near-black ground with no extra weight step. Track it tighter than you would a Garamond — the face is open to begin with. Pair it with a width-matched fallback (`local('Georgia')` at `size-adjust: 106.2%`; Georgia is a closer base than Times at 0.94x its width) so the headline does not reflow during swap.

```css
.dlg-back { position: fixed; inset: 0; z-index: 200; display: flex; align-items: center; justify-content: center;
  padding: 24px; background: rgba(6,6,9,.62); backdrop-filter: blur(4px);
  opacity: 0; transition: opacity .22s ease; }
.dlg-back.open { opacity: 1; }
.dlg-back[hidden] { display: none; }

.dlg { position: relative; width: min(520px, 100%); padding: 28px 28px 24px; border-radius: 18px;
  background: var(--panel); border: 1px solid var(--border-strong); box-shadow: var(--shadow-modal);
  opacity: 0; transform: translateY(14px) scale(.97);
  transition: transform .26s var(--ease-out), opacity .26s ease; }
.dlg-back.open .dlg { opacity: 1; transform: none; }
.dlg h3 { font-family: "IBM Plex Serif", Georgia, serif;   /* site-only; --font-sans/600 in-console */
  font-weight: 400; font-size: 31px; line-height: 1.12; margin: 2px 0 8px; }
.dlg .lede { color: var(--text-2); font-size: 14.5px; line-height: 1.6; }

/* Numbered steps — CSS counter in an accent-tinted disc, no list markers. */
.dlg-steps { list-style: none; margin: 20px 0 24px; padding: 0; counter-reset: dstep;
  display: flex; flex-direction: column; gap: 14px; }
.dlg-steps li { display: flex; gap: 13px; align-items: flex-start; }
.dlg-steps li::before { counter-increment: dstep; content: counter(dstep);
  flex: none; width: 24px; height: 24px; margin-top: 2px; border-radius: 50%;
  display: grid; place-items: center; font-family: var(--font-mono); font-size: 12px; font-weight: 600;
  color: var(--accent); background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent); }
.dlg-steps b { color: var(--text); font-weight: 600; }
.dlg-steps p { color: var(--text-2); font-size: 13.5px; line-height: 1.55; margin: 2px 0 0; }
.dlg-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
```

**Behaviour.** Opens ~120ms after the click so it doesn't race the browser's own download chrome. Closes on backdrop click, the × , Escape, or the dismiss button; focus moves to the primary action on open and returns to the trigger on close. Steps are **tailored to the detected OS** by swapping each step's text.

**Copy rule.** A step exists only where that platform actually needs one. Windows carries an unsigned-installer warning; macOS has nothing to say at that point, so the step is *absent* there rather than padded with "open it normally" — per **[Writing UI copy](#writing-ui-copy)**, a step with no action in it is deleted, not shortened.

### Tabs

Three idioms: **underline** (hairline `border-bottom`, active item bold `--text` with a 2px foreground indicator that slides between tabs), **pill** (bordered `--panel` pills, active fills `--bg-inset`), **segmented** (enclosed control on `--bg-inset`, active raises a `--panel` chip with a faint shadow). Tabs take an optional leading icon and a trailing count chip.

### Navigation (NavItem / PageNav / Avatar)

- **NavItem** (sidebar row): `padding: 7px 10px; border-radius: var(--radius-sm); color: var(--text-2)`. Hover → `background: var(--bg-inset); color: var(--text)`. Active → same bg, `font-weight: 600`, icon swaps to `-bold`.
- **PageNav** ("On this page"): a header (list icon + label), a vertical rail (`border-left: 1px solid var(--border)`), items muted (`--text-3`) that brighten on hover; the active item is bold `--text` with a 2px foreground bar on the rail. One level of nesting via extra left padding.
- **Avatar**: rounded square (`object-fit: cover`), or a tinted initial — `background: var(--accent-soft); color: var(--accent); font-weight: 700`.

### DocTable & DataTable

- **DocTable** (minimal): `border-collapse`, hairline `border-bottom` row rules, header in `--text` with a `--border-strong` underline, first column emphasized (`--text`), body `--text-2`. Generous 12px cell padding.
- **DataTable** (functional): a rounded `--panel` container; a toolbar with a search input (`magnifer` icon) and a row count; uppercase header cells on `--bg-sidebar`, click-to-sort with an accent arrow; rows hover to `--bg-inset`; right-aligned tabular-nums numerics; status pills via the Badge; per-row icon actions. Add a checkbox column (`accent-color: var(--accent)`) with select-all and a bulk-action toolbar (tinted `--accent-soft`) that replaces the search row while rows are selected.

### LineChart (usage/metrics)

Data-driven inline `<svg>` (no chart lib), used on the **Usage** page. Recipe:
- **Series colours** come from the selectable accent hues, applied as a `color:` class (`.us0`=blue, `.us1`=teal, `.us2`=violet, `.us3`=amber, `.us4`=green, `.us5`=rose) so SVG shapes pick them up via `stroke="currentColor"` / `fill="currentColor"` — never hardcode chart hex (the design linter forbids it). One **primary** series draws heavier (`stroke-width 2.6`) with a flat translucent area fill (`fill-opacity .12`, no gradient — the system is flat).
- **Smooth lines** via a horizontal-midpoint cubic path (control x at the midpoint of each pair, y at the endpoints).
- **Grid + axes**: hairline baseline (`--border`), 1–2 dashed gridlines (`stroke-dasharray 2 6`), mono `--text-3` tick + day labels (strided when dense).
- **Limit line**: a dashed `--danger` rule (`stroke-opacity .55`, `stroke-dasharray 5 4`) with a small mono `--danger` caption — the rate-limit ceiling. Include one on any chart with a cap.
- **Endpoint tags**: a filled series-colour dot (`stroke: --panel`) at the last point with the value in mono beside it.
- **Meters/bars** (RPM, quota): an inset track (`--bg-inset`) with a `currentColor` fill; the fill turns `--warn` past ~75% of cap.
- **DonutChart** (composition, e.g. by-process share): a `--bg-inset` track ring with per-segment arcs drawn as `<circle>` strokes (`stroke-linecap: round`, a small angular gap between segments), coloured **distinctly** by rank via the `.us*` hue classes (`currentColor`). A mono total sits in the centre; a legend below pairs a rounded-square colour chip with the label, value, and percent. Segment order matches the legend order.

### Content — InlineCode, CodeBlock, CopyField, Link

**InlineCode** — a monospaced chip for tokens/paths in running text; tone it to a semantic state when used inside a matching callout. **CodeBlock** — a titled preview with a filename header, a copy button, and light JS/TS syntax highlighting. **CopyField** — a value in a `--bg-inset` box with a trailing copy button (divider `border-left`) that flips to a green `check-circle` on click (`boxed` for domains/keys, `bare`+`lg` for an editable-title look). **Link** — `default` (accent), `prose` (muted underline → white on hover), `subtle` (quiet foreground), `inherit` (takes the surrounding text colour — use inside callouts/toasts); `external` opens a new tab + appends a ↗ arrow.

```css
/* InlineCode — inline code chip */
.icode { font-family: var(--font-mono); font-size: .86em; padding: 1px 6px; border-radius: 6px;
  background: var(--bg-inset); border: 1px solid var(--border); color: var(--text); }
.icode.warn { color: var(--warn); background: var(--warn-soft); border-color: var(--warn-border); }  /* tone inside a callout */

/* CodeBlock — code preview block */
.codeblock { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); overflow: hidden; }
.codeblock .head { display: flex; align-items: center; gap: 10px; padding: 10px 12px 10px 15px; border-bottom: 1px solid var(--border); }
.codeblock .file { font-family: var(--font-mono); font-size: 12.5px; color: var(--text-2); }
.codeblock pre { margin: 0; padding: 14px 16px; overflow-x: auto; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.7; }
/* syntax: comment var(--text-3) · string #7fd1a0 · keyword #b69cff · fn/number #e0a458 */

/* CopyField — copyable value box */
.copy { display: inline-flex; align-items: stretch; height: 34px; overflow: hidden;
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--bg-inset); }
.copy .val { display: inline-flex; align-items: center; padding: 0 12px; font-family: var(--font-mono); font-size: 12.5px; }
.copy .btn { width: 36px; display: grid; place-items: center; border: none; border-left: 1px solid var(--border);
  background: transparent; color: var(--text-3); cursor: pointer; }
.copy .btn.done { color: var(--ok); }   /* swaps to check-circle on click */

/* Link */
.link { color: var(--accent); }
.link:hover { text-decoration: underline; text-underline-offset: 2px; }
.link.prose { color: var(--text-2); text-decoration: underline; text-decoration-color: var(--text-3); text-underline-offset: 2px; }
.link.prose:hover { color: #fff; text-decoration-color: #fff; }
.link.subtle { color: var(--text); text-decoration: underline; text-decoration-color: var(--border-strong); }
.link.inherit { color: inherit; text-decoration: underline; text-decoration-color: color-mix(in srgb, currentColor 45%, transparent); }
```

**Confirm pattern** (shared by IconButton, CopyField, and CodeBlock's copy button): on click, briefly swap the glyph to a green `check-circle` with a small pop — `@keyframes pop { 0% { transform: scale(.4); opacity: 0 } 55% { transform: scale(1.12) } 100% { transform: scale(1); opacity: 1 } }`.

---

## Marketing site (site-only)

`docs/index.html` and `docs/docs.html` are standalone files with no build step, so they
inline the tokens rather than importing them. They use the vocabulary above — `--text`,
`--border`, `--bg-inset`, `--font-sans` — with a lighter ground (`--bg: #08080a`,
`--panel: #0f0f12`) and the lifted `--text-3: #7f7f8a` the ramp note requires. Keep the
two files' token blocks identical to each other; they are siblings linked from each
other's nav, and a divergent ground shifts the page on navigation.

Four additions live only on the site. They are documented here so the next surface can
reuse them rather than reinvent them.

```css
/* Display face. Site-only, as the StepsDialog note says. Pair every webfont with a
   width-matched local fallback so the headline does not reflow during swap: measure the
   real face against the stand-in and set size-adjust to the ratio. IBM Plex Serif against
   Georgia is 106.2%; an unmatched Georgia fallback reflowed the h1 from two lines to
   three at 320px, a 47px shift. */
@font-face { font-family: 'Plex Serif Fallback'; src: local('Georgia'), local('Times New Roman');
  size-adjust: 106.2%; ascent-override: 90%; descent-override: 22%; }
--font-serif: 'IBM Plex Serif', 'Plex Serif Fallback', Georgia, serif;

/* Prose measure. In em, never ch: IBM Plex Sans's zero is 1.33x its average advance
   (0.60em vs 0.4499em), so a ch value overstates the real line length by a third.
   29em lands at ~65 characters at every size in the ramp. */
--measure: 29em;

/* Section seams. Width-based, because the type scale is width-based — a vh seam against
   a vw type scale makes the spacing-to-type ratio a function of aspect ratio, which swung
   63% between a 1440x900 laptop and a 768x1024 tablet. Two values, so a surface can say
   "this is a turn in the argument" or "these two sections are one act". */
--seam-act: clamp(88px, 9vw, 148px);
--seam-flow: clamp(56px, 5.5vw, 92px);
```

**Decoration never shares space with text.** The footer ornament used to free-float over
the link columns and erase them between 721px and 1200px. Give an ornament its own grid
track, and withhold it at widths where the content needs the room:

```css
.foot-inner { display: grid; grid-template-columns: minmax(0, 1fr) min(30%, 340px);
  grid-template-areas: "content art" "bottom bottom"; align-items: start; column-gap: 40px; }
@media (max-width: 1100px) {
  .foot-inner { grid-template-columns: 1fr; grid-template-areas: "content" "bottom"; }
  .foot-stage { display: none; }
}
```

---

## Do / Don't

- **Do** lean on hairline borders + inset wells for structure; keep cards flat and shadowless.
- **Do** reserve the accent for selection, links, focus, and active state — never as a fill for big surfaces.
- **Do** use one **primary** (bright-neutral) button per view; everything else is secondary/ghost.
- **Do** keep motion quiet (.12–.3s, ease-out), and always honour `prefers-reduced-motion` — by **slowing** motion, not deleting it. A spinner with `animation: none` is a static ring that tells the operator nothing; `animation-duration: 1.6s` still says "working".
- **Do** give every `div` you attached an `onClick` to a `role`, a `tabIndex`, and a key handler in the same breath — or make it a `<button>`. This is the failure that recurs.
- **Don't** use emoji, bluish-purple gradients, drop shadows on cards, or Title Case headings.
- **Don't** introduce new hues — use the accent or a semantic state.
- **Don't** let a control be named by `title` alone — the tooltip host strips it on focus. See **Button & IconButton**.

### Verify before shipping

The build gates on `npm run design-lint` — token use, the spacing scale, radius tokens, button
variants, no native `alert/confirm/prompt`. It cannot see any of the following, so check them
by hand on the surface you touched:

- Body-size text ≥ 4.5:1 **against the lightest ground it lands on**. `--text-2` and `--text-3` both clear it; anything you tint yourself may not.
- Focus visible on every interactive element, including the ones on `--primary-bg`.
- Tab through the whole surface: no control skipped, none trapped, nothing focusable inside a hidden container.
- Every icon-only control has an `aria-label`; every input has a label pointing at it; every switch has a subject.
- The shell at 375px, and the widest table or chart on the surface.

---

*Provenance: distilled from the Olisar console front-end (`gcrft123/olisar`, `web/src`). For the full component library, specimen cards, and an interactive console recreation, see the Olisar Design System project this was exported from.*
