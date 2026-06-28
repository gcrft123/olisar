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
  --text-3: #6a6a73;        /* tertiary / muted / placeholders */

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
  --font-sans: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
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
  --ring: 0 0 0 3px var(--accent-soft);  /* focus */

  /* Motion — quiet and quick */
  --ease-out: cubic-bezier(0.2, 0.9, 0.3, 1);
  --dur-fast: .12s;  --dur-mid: .16s;  --dur-slow: .3s;
}
```

### Runtime accent switching (optional)

The accent is a per-browser preference. To re-tint live, set `--accent`, `--accent-soft`, and the two glow vars from one hex:

```js
function setAccent(hex) {
  const n = parseInt(hex.slice(1), 16);
  const rgb = `${(n>>16)&255}, ${(n>>8)&255}, ${n&255}`;
  const r = document.documentElement.style;
  r.setProperty('--accent', hex);
  r.setProperty('--accent-soft', `rgba(${rgb}, 0.16)`);
  r.setProperty('--glow-a', `rgba(${rgb}, 0.18)`);
  r.setProperty('--glow-b', `rgba(${rgb}, 0.10)`);
}
```

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

**Inter** for the whole UI; **JetBrains Mono** for IDs, URLs, tags, slash commands, code, and numeric readouts.

```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
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

### Buttons

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

Variants: **primary** (one bright CTA per view), **secondary** (the base hairline button), **ghost**, **danger** (red), **caution** (amber), and an **acting** state (disabled + a spinning ring) for "Saving…". Icon-only buttons are a 34×34 square (default ghost); on click of a copy/confirm action, swap the glyph to a green `check-circle` briefly.

### Inputs, select, textarea

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

```html
<div class="field">
  <label>Name triggers</label>
  <div class="desc">Comma-separated. Including one addresses Olisar.</div>
  <input class="input" placeholder="olisar, oli">
</div>
```
```css
.field { margin-bottom: 17px; }
.field > label { display: block; font-weight: 550; font-size: 12.5px; margin-bottom: 6px; }
.field .desc { color: var(--text-2); font-size: 12px; margin: -3px 0 8px; line-height: 1.5; }
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

### Callout (Resend-style, no eyebrow)

A colored border + dark tinted fill + a left icon. Tones: `tip`→ok, `note`/`info`→accent, `warning`→warn, `danger`→danger. Structure: a `.ic` span (Solar icon) + a `.callout-body` that may hold a `.callout-title` plus paragraphs/lists. Used for the security-review (publish) verdict, where each flagged reason renders as its own callout, stacked with a gap.

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
.callout.danger  { --cc: var(--danger); }
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

### Tooltip

A small dark chip on a hairline border with a **stem** (a bordered diamond) pointing at its target, shown on hover/focus of any control whose meaning isn't a visible label. Driven by one portal-mounted, delegated host that reads `data-tip="…"` (or a native `title`, which it migrates to `data-tip` so the OS tooltip is suppressed) — so it's never clipped by an `overflow:hidden` modal. Sits above the target; flips below (stem on top) when there's no room. **Pops up instantly on hover**; instant hide.

```css
.tooltip {
  position: fixed; z-index: 400; transform: translate(-50%, -100%);
  padding: 5px 9px; border-radius: var(--radius-xs); max-width: 280px;
  background: var(--panel); border: 1px solid var(--border-strong); color: var(--text);
  font-size: 11.5px; font-weight: 500; line-height: 1.3;
  box-shadow: var(--shadow-pop); pointer-events: none; animation: tip-in .1s ease;
}
.tooltip.below { transform: translate(-50%, 0); }      /* flipped below the target */
/* stem: a small bordered diamond pointing at the target */
.tooltip::after { content: ""; position: absolute; left: 50%; margin-left: -4px; width: 8px; height: 8px; background: var(--panel); transform: rotate(45deg); }
.tooltip:not(.below)::after { bottom: -4px; border-right: 1px solid var(--border-strong); border-bottom: 1px solid var(--border-strong); }
.tooltip.below::after { top: -4px; border-left: 1px solid var(--border-strong); border-top: 1px solid var(--border-strong); }
@keyframes tip-in { from { opacity: 0 } to { opacity: 1 } }
```

Every **icon-only button** (close ×, copy, report flag, row actions, the server picker…) carries a `data-tip`/`title`, paired with an `aria-label` for assistive tech. The press-and-hold **bot-power control** is the deliberate exception — it surfaces its state as a visible hint beneath the button, so it carries no hover tooltip.

### Overlays (Dialog / Modal / SaveDock)

- **Dialog** (centered info+action): blurred backdrop `rgba(0,0,0,.55)` + `backdrop-filter: blur(3px)` fading in; the card (`--panel`, `--border-strong`, `--shadow-modal`) scales-and-lifts from `translateY(12px) scale(.96)` → `0/1` over `.22s var(--ease-out)`. Optional tinted icon tile (46px, `--radius` 15px) + footer actions. Close on backdrop click / Escape.
- **Modal** (full-UI sheet): same backdrop; a `min(900px,94vw) × min(620px,90vh)` sheet with a header (title + close ×), a scrollable body (put a two-pane nav+content inside), and an optional footer.
- **SaveDock** (unsaved-changes bar): `position: fixed; bottom: 22px; left: 50%`; slides up from `translate(-50%,170%)` → `translate(-50%,0)` over `.3s var(--ease-out)`. A `--panel` pill, message + Reset/Save.

### Tabs

Three idioms: **underline** (hairline `border-bottom`, active item bold `--text` with a 2px foreground indicator that slides between tabs), **pill** (bordered `--panel` pills, active fills `--bg-inset`), **segmented** (enclosed control on `--bg-inset`, active raises a `--panel` chip with a faint shadow). Tabs take an optional leading icon and a trailing count chip.

### Navigation (NavItem / PageNav / Avatar)

- **NavItem** (sidebar row): `padding: 7px 10px; border-radius: var(--radius-sm); color: var(--text-2)`. Hover → `background: var(--bg-inset); color: var(--text)`. Active → same bg, `font-weight: 600`, icon swaps to `-bold`.
- **PageNav** ("On this page"): a header (list icon + label), a vertical rail (`border-left: 1px solid var(--border)`), items muted (`--text-3`) that brighten on hover; the active item is bold `--text` with a 2px foreground bar on the rail. One level of nesting via extra left padding.
- **Avatar**: rounded square (`object-fit: cover`), or a tinted initial — `background: var(--accent-soft); color: var(--accent); font-weight: 700`.

### Tables

- **Doc table** (minimal): `border-collapse`, hairline `border-bottom` row rules, header in `--text` with a `--border-strong` underline, first column emphasized (`--text`), body `--text-2`. Generous 12px cell padding.
- **Data table** (functional): a rounded `--panel` container; a toolbar with a search input (`magnifer` icon) and a row count; uppercase header cells on `--bg-sidebar`, click-to-sort with an accent arrow; rows hover to `--bg-inset`; right-aligned tabular-nums numerics; status pills via the Badge; per-row icon actions. Add a checkbox column (`accent-color: var(--accent)`) with select-all and a bulk-action toolbar (tinted `--accent-soft`) that replaces the search row while rows are selected.

### Code & links

```css
/* Inline code chip */
.icode { font-family: var(--font-mono); font-size: .86em; padding: 1px 6px; border-radius: 6px;
  background: var(--bg-inset); border: 1px solid var(--border); color: var(--text); }
/* tone it inside a callout, e.g. */
.icode.warn { color: var(--warn); background: var(--warn-soft); border-color: var(--warn-border); }

/* Code preview block */
.codeblock { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); overflow: hidden; }
.codeblock .head { display: flex; align-items: center; gap: 10px; padding: 10px 12px 10px 15px; border-bottom: 1px solid var(--border); }
.codeblock .file { font-family: var(--font-mono); font-size: 12.5px; color: var(--text-2); }
.codeblock pre { margin: 0; padding: 14px 16px; overflow-x: auto; font-family: var(--font-mono); font-size: 12.5px; line-height: 1.7; }
/* syntax: comment var(--text-3) · string #7fd1a0 · keyword #b69cff · fn/number #e0a458 */

/* Links */
.link { color: var(--accent); }                                   /* default */
.link:hover { text-decoration: underline; text-underline-offset: 2px; }
.link.prose { color: var(--text-2); text-decoration: underline; text-decoration-color: var(--text-3); text-underline-offset: 2px; }
.link.prose:hover { color: #fff; text-decoration-color: #fff; }
.link.inherit { color: inherit; text-decoration: underline; text-decoration-color: color-mix(in srgb, currentColor 45%, transparent); }
```

**CopyField:** a value in a `--bg-inset` box with a trailing copy button (divider `border-left`) that flips to a green `check-circle` on click. **Confirm pattern:** any copy/confirm icon button briefly swaps to a green check with a small pop (`@keyframes` scale .4→1.12→1).

---

## Do / Don't

- **Do** lean on hairline borders + inset wells for structure; keep cards flat and shadowless.
- **Do** reserve the accent for selection, links, focus, and active state — never as a fill for big surfaces.
- **Do** use one **primary** (bright-neutral) button per view; everything else is secondary/ghost.
- **Do** keep motion quiet (.12–.3s, ease-out), and always honour `prefers-reduced-motion`.
- **Don't** use emoji, bluish-purple gradients, drop shadows on cards, or Title Case headings.
- **Don't** introduce new hues — use the accent or a semantic state.

---

*Provenance: distilled from the Olisar console front-end (`gcrft123/olisar`, `web/src`). For the full component library, specimen cards, and an interactive console recreation, see the Olisar Design System project this was exported from.*
