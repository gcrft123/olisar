---
# Machine-readable head of this guide. Without it the detector locates DESIGN.md, finds no
# frontmatter, and silently skips its four design-system rules — palette, type family,
# radius and type ramp went unchecked for every scan. The values below are read straight
# from :root in web/src/index.css; if you change a token there, change it here.
typography:
  body:
    fontFamily: "IBM Plex Sans"
  mono:
    fontFamily: "JetBrains Mono"
  display:
    fontFamily: "IBM Plex Serif"   # marketing site only — see "Marketing site (site-only)"
colors:
  bg: "#020203"
  bg-sidebar: "#040405"
  panel: "#08080a"
  bg-inset: "#0f0f12"
  input-bg: "#0f0f12"
  border: "#26262a"
  border-strong: "#323237"
  text: "#ededee"
  text-2: "#9d9da7"
  text-3: "#7f7f8a"
  accent: "#5b9cf6"
  accent-violet: "#8a8af2"
  accent-blue: "#5b9cf6"
  accent-teal: "#2dd4bf"
  accent-green: "#43cf8e"
  accent-amber: "#e0a458"
  accent-rose: "#f2728a"
  primary-bg: "#ededee"
  primary-fg: "#18181b"
  primary-hover: "#ffffff"
  ok: "#43cf8e"
  danger: "#ff6369"
  warn: "#e3a13a"
  info: "#5fc4f2"
  neutral: "#9d9da7"
  dc-bg: "#313338"
  dc-head: "#f2f3f5"
  dc-text: "#dbdee1"
  dc-muted: "#949ba4"
  dc-hash: "#80848e"
  dc-brand: "#5865f2"
  dc-brand-hover: "#4752e0"
  # Code-preview syntax tokens, documented under "Content — InlineCode, CodeBlock…".
  syntax-string: "#7fd1a0"
  syntax-keyword: "#b69cff"
  syntax-number: "#e0a458"
rounded:
  # The four surface radii. Modals and callouts; buttons, inputs and nav items; tags and chips;
  # and the pill.
  card: "16px"
  control: "12px"
  chip: "8px"
  pill: "999px"
  # Sub-chip radii. The prose rule under "Verify before shipping" already allows these
  # ("raw 0, 50%, ≤6px chips excepted") and design-lint.mjs enforces exactly that — they are
  # the small square-ish corners on dots, meters, slot chips and syntax pills, where 8px
  # would read as a lozenge rather than a corner.
  micro-6: "6px"
  micro-5: "5px"
  micro-4: "4px"
  micro-3: "3px"
  micro-2: "2px"
---
# Olisar — Design Guide

A dark-only design system for **Olisar**, a self-hosted AI Discord bot configured from a private admin console. The aesthetic is calm, near-monochrome, hairline-bordered — in the spirit of Resend's dashboard — with one fixed accent on a flat near-black ground and no ambient decoration behind it.

**How to use this file:** drop it in your repo (e.g. `DESIGN.md` or `.claude/DESIGN.md`). Paste the **Design tokens** block into your global CSS, wire up the two fonts and the icon set, then build UI with the **Component recipes** below. Everything is plain CSS custom properties + HTML/JSX — no framework required.

---

## Brand & voice

- **Audience:** an operator who knows what they're doing. The tool respects their time and their data.
- **Voice:** second person ("you"), plainspoken, lightly opinionated. Say *what* a setting does and *why* in one breath, no hedging.
- **Tone:** calm, competent, a little dry. Never oversell, never exclaim, never apologize performatively. Warnings are direct and specific.
- **Casing:** **sentence case everywhere** — page titles, section titles, buttons, labels. The only uppercase is the small tracked eyebrow/section-label treatment.
- **Mechanics:** settings = a terse **label** + a one-sentence **description** with a concrete consequence. Slash commands and code in mono with a leading slash (`/ask`, `/forget-me`). Numbers are concrete ("seen 7×", "12,481 messages"). Em-dash glosses in options ("both — read & talk").
- **No emoji** in the UI chrome, ever. No unicode-as-icon.

### Writing UI copy

The heading does the work. A description earns its place only when it says something the heading can't.

- **Cut a description that restates its heading.** "Desktop app — settings for the Olisar desktop application" is one fact written twice; ship the heading alone. Same for section hints that just list the fields beneath them.
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

  /* Surfaces (darkest → lightest). Near-black ground; overlays sit a hair above it
     and are read by their BORDER, not by fill contrast — they blend into the bg.
     Page content sits on the ground itself: see Section. */
  --bg: #020203;            /* app background */
  --bg-sidebar: #040405;    /* sidebar / nav rail */
  --panel: #08080a;         /* modal, menu and popover surface */
  --bg-inset: #0f0f12;      /* inset wells: inputs, chips, code */
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

  /* The one accent. FIXED, not a user preference — colour is load-bearing here: it carries
     links, the focus ring, the toggle "on" track, meters and the primary chart series, and a
     free pick that landed near --ok / --warn / --danger made an enabled toggle read as an
     error state. One accent, chosen once, is the system. */
  --accent: #5b9cf6;
  --accent-soft: rgba(91, 156, 246, 0.16);

  /* Chart series palette (.us0–.us5) and the marketplace publisher chip. Distinct hues for
     CATEGORICAL data only — never UI state, which is what the semantic tokens below are. */
  --accent-violet: #8a8af2; --accent-blue: #5b9cf6;
  --accent-teal: #2dd4bf;   --accent-green: #43cf8e; --accent-amber: #e0a458;
  --accent-rose: #f2728a;

  /* "Primary" action surface — a bright neutral, NOT the accent. One per view. */
  --primary-bg: #ededee;
  --primary-fg: #18181b;
  --primary-hover: #ffffff;

  /* Semantic states — base / -soft (fill) / -border (edge). */
  --ok: #43cf8e;     --ok-soft: rgba(67,207,142,.14);    --ok-border: rgba(67,207,142,.34);
  --danger: #ff6369; --danger-soft: rgba(255,99,105,.13); --danger-border: rgba(255,99,105,.34);
  --warn: #e3a13a;   --warn-soft: rgba(227,161,58,.14);   --warn-border: rgba(227,161,58,.34);
  /* Sky, not the accent's periwinkle. These two were byte-identical — survivable while the
     accent was switchable, permanent once it wasn't, and an info badge beside an accent
     element is a distinction the UI relies on. ΔE 32 apart; 7.9:1 on --panel. */
  --info: #5fc4f2;   --info-soft: rgba(95,196,242,.14);   --info-border: rgba(95,196,242,.34);
  --neutral: #9d9da7;--neutral-soft: rgba(157,157,167,.12);--neutral-border: rgba(157,157,167,.28);
  /* Aliases */
  --success: var(--ok); --error: var(--danger); --warning: var(--warn);

  /* Discord's own chrome, for the surfaces that SIMULATE it (the Command replies preview).
     Deliberately off-palette and quarantined behind a --dc- prefix: this is a picture of
     another product, and approximating it in Olisar's greys would make the preview a lie.
     Never use these anywhere the console is speaking as itself. */
  --dc-bg: #313338;      --dc-head: #f2f3f5;   --dc-text: #dbdee1;
  --dc-muted: #949ba4;   --dc-hash: #80848e;   --dc-brand: #5865f2;

  /* Type */
  --font-sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;

  /* Radii — generously rounded */
  --radius: 16px;       /* modals, callouts, toasts */
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

  /* Motion — quiet and quick. --ease-out is the default for every transition; bare `ease`
     is the browser's own curve and reads visibly slower off the mark on the same move, so
     the two side by side look like two systems. Route .12/.16/.3s through the tokens. */
  --ease-out: cubic-bezier(0.2, 0.9, 0.3, 1);
  /* Icon cross-fades only (see Button & IconButton). Flatter in, harder out. */
  --ease-icon: cubic-bezier(0.2, 0, 0, 1);
  --dur-fast: .12s;  --dur-mid: .16s;  --dur-slow: .3s;

  /* Raster images only. User art on a near-black ground has no edge of its own: a dark
     avatar dissolves into the surface, a light one bleeds past where it stops. Pure white
     at 10% — never a tinted neutral, which picks up the surface and reads as dirt. */
  --img-edge: oklch(1 0 0 / 0.1);
}
```

### Why the accent is not a preference

An earlier version shipped a swatch grid plus a free-form `<input type="color">` writing
`--accent` at runtime. Don't rebuild it. The accent is not decoration — it carries links, the
focus ring, the toggle "on" track, meters and the primary chart series — so a picker is a
control that can break the interface's ability to signal:

- A pick near `--ok`, `--warn` or `--danger` makes an **enabled toggle read as an error**. The
  old version clamped a too-dark pick up to 4.5:1 against `--bg`, and that clamp worked — but it
  solves contrast, and this collision is semantic. There is no arithmetic for "not green".
- Semantic tokens then have to be sized against a moving target. `--accent` and `--info` shipped
  **byte-identical** (`#5b9cf6`), so an info badge and an accent element were the same colour —
  survivable while the accent was a preference, permanent the moment it wasn't.
- The whole apparatus — presets, picker, clamp, contrast maths, a Reset, and a note explaining
  why the applied colour isn't the one that was picked — exists to let an operator restate a
  decision the design already made.

One accent, chosen once, sized against every ground it lands on, is the system. **Interface
size** is the only per-browser preference the console offers.

### Interface size

`--ui-scale` is the one per-browser preference, offered as 100% / 110% / 125% and defaulting to
**110%**. It is written to the root before first paint and persisted in `localStorage`, and it
needs no layout work, because `zoom` scales the coordinate system rather than any individual
value.

Three rules come with it:

**Never write a bare viewport unit.** They resolve against the *un-zoomed* viewport and are then
scaled, so `height: 100vh` renders `--ui-scale` taller than the window — at 1.1 that was 80px of
phantom scroll below the sidebar. Use `--vh` / `--vw` / `--dvh`, which divide the factor back out:

```css
.sidebar   { height: var(--vh); }                          /* not 100vh */
.some-modal{ width: min(560px, calc(var(--vw) * 0.94)); }  /* not 94vw  */
```
There is no exception. The rule used to carve one out for decorative shapes, on the grounds that
a 10% shift on a blurred blob is invisible — that decoration is gone, and an exemption nobody can
apply is just a hole for the next raw `100vh` to fall through.

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
  overflow-wrap: break-word;  /* a word too long for its line breaks instead of spilling */
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.014em; }
::selection { background: var(--accent-soft); }

/* The ground is flat. Two blurred accent-tinted blobs used to drift behind everything
   here; the console is a surface an operator keeps open for an hour at a time, and
   perpetual motion behind a settings form is a cost with no reader. Nothing is painted
   between --bg and the content. */

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
| Section title / brand | 15px | 600 |
| Body / inputs / buttons | 13.5px | 400 |
| Secondary / descriptions | 12.5px | 400 |
| Eyebrow / nav label | 11px | 600 uppercase, 0.04em |

Form **labels** sit at weight **550** (a hair above medium). Body line-height 1.55.

**11px is the floor, and the eyebrow is the only thing that lives there.** Every label,
category heading, status marker and axis tick in the console renders at 11px / 600 / 0.04em
uppercase — one treatment, not five. The console had drifted to 9px, 10px and 10.5px variants
of the same idea, some in monospace, which reads as a second type scale hiding under the
first. Two deliberate exceptions, both documented where they live: chart tick labels inside a
`role="img"` SVG (the values are also in the visually-hidden data table), and the 10px **APP**
badge in the **DiscordPreview**, which is a faithful reproduction of someone else's chrome.

Monospace is for **IDs, URLs, tags, slash commands, code, and numeric readouts** — not for
making a word look technical. "Community", "Games" and "Custom" are labels; they set in sans.

---

## Iconography

**One source of truth: the [Solar icon set](https://github.com/480-Design/Solar-Icon-Set).** No emoji, no unicode-as-icon.

- In a React app: `@solar-icons/react`.
- Anywhere (no build): Iconify — `<iconify-icon icon="solar:user-circle-linear"></iconify-icon>` via `https://code.iconify.design/iconify-icon/2.1.0/iconify-icon.min.js`.
- **Two weights by state:** `-linear` (outline) is idle; `-bold` (filled) marks the active item (e.g. the selected nav row swaps linear → bold). Render at 16–19px inline.

Common semantic names: `user-circle` (persona), `tuning-2` (behavior), `hashtag` (channels), `shield-keyhole` (access), `book-bookmark` (knowledge), `plug-circle` (extensions), `settings`, `power`, `magnifer` (search), `copy`, `trash-bin-minimalistic`, `check-circle`, `danger-triangle`, `info-circle`.

**One stroke weight per set.** Solar's `-linear` glyphs render at `stroke-width: 1.5`. A hand-rolled SVG at 2 sits beside them looking bolder for no reason — the `<select>` chevron did, in the same forms as the disclosure arrow. Match 1.5. The exceptions are `<CloseX>` and `<CheckMark>`, which stay at 2: a two-stroke × or ✓ carries a fraction of a full glyph's ink, so matching the number would make it optically lighter, not equal.

**Never nest a circled glyph in a circle.** Solar's `check-circle` already draws its own ring; set inside a filled disc it reads as a circle within a circle. Where a mark sits in a ring of its own (the Get started list), use the bare `<CheckMark>`.

**The one brand mark:** Discord's logo (`<DiscordLogo>`), filled in `currentColor`, and only on a Discord-blue `.btn-discord` that hands off to Discord, such as adding the bot to a server. Nowhere else, and never as a stand-in for a Solar glyph.

**Logo:** a rounded-square shield with a centered star (slate blue, navy star). Place it on `--bg`, `--bg-inset`, or `--accent-soft` tiles; don't recolor it.

### Raster images

Every `<img>` in the console — the logo, server icons, Discord avatars — carries a 1px inset edge:

```css
img.brand-logo, img.server-icon, .member-av img, .dcp-av img, .mp-who img.avatar {
  outline: 1px solid var(--img-edge); outline-offset: -1px;
}
```

`outline`, not `border`, so it costs no layout; the negative offset keeps the hairline inside the box and following the radius clip rather than boxing a circular avatar. Scope it to `img` — the tinted-initial fallbacks behind these classes are drawn chrome with a ground of their own, not art.

---

## Component recipes

Self-contained CSS + markup for the core set. Class names are illustrative — adapt to your conventions. All buttons/inputs share a **34px height**, the same radius, and the same border so they line up.

**The system has 33 components. Every one is covered below:**

| Group | Components |
|---|---|
| Buttons | **Button**, **IconButton** |
| Forms | **TextField**, **TextArea**, **Select**, **Toggle**, **Field** |
| Data display | **Section**, **Badge**, **Tag**, **RoleChip**, **StatTile**, **DocTable**, **DataTable**, **ActivityLedger**, **ScrollFade** |
| Product surfaces | **DiscordPreview**, **DangerZone** |
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

### CopyGlyph (copy → copied)

Every copy affordance in the console swaps one glyph for another. Both stay mounted and
cross-fade; nothing unmounts, so the *departure* is animated too.

```css
.copyglyph { position: relative; display: inline-grid; place-items: center; flex: none; }
.copyglyph > svg { grid-area: 1 / 1;
  transition: scale var(--dur-mid) var(--ease-icon), opacity var(--dur-mid) var(--ease-icon),
              filter var(--dur-mid) var(--ease-icon); }
.copyglyph > svg:last-child      { scale: .25; opacity: 0; filter: blur(4px); color: var(--ok); }
.copyglyph.on > svg:first-child  { scale: .25; opacity: 0; filter: blur(4px); }
.copyglyph.on > svg:last-child   { scale: 1;   opacity: 1; filter: blur(0); }
```

Both glyphs share one grid cell, so neither reflows the button. The values are fixed: `.25 → 1`
scale, `0 → 1` opacity, `4px → 0` blur — the blur is what keeps a 15px icon from looking like it
merely resized. **No overshoot.** The version this replaced ran `scale(.4) → 1.12 → 1`, and a
bounce is the one thing an icon transition should never have. Use `--ease-icon`, not
`--ease-out`: this is a swap in place, not travel. Under `prefers-reduced-motion` drop the scale
and blur and keep the opacity cross-fade — the colour and the glyph still change.

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
.toggle .track { width: 38px; height: 22px; border-radius: 99px; background: var(--border-strong); position: relative; transition: background var(--dur-mid) var(--ease-out); }
.toggle .knob { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff;
  transition: transform var(--dur-mid) var(--ease-out); }
.toggle.on .track { background: var(--accent); }
.toggle.on .knob { transform: translateX(16px); }   /* NOT left: 18px */
```

The knob travels on `transform`. `left` is a layout property, so animating it re-laid out
the track on every frame of every toggle — on the control a settings console is densest in.
The 16px is the trip, not the destination: 2px inset either end of a 38px track.

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

Inside a **Section** the same three siblings lay out as a settings row, label and description on the left and the control on the right. See Section.

A value the backend can check (a token, a secret, an API key) is checked as it's pasted, not by a
**Test** button beside it. The result is a **check line** under the input: a spinner while it
runs, then the success in `--ok` or the failure in `--danger`, with a **Try again** link only
when the check itself failed (an outage, not a wrong value). Anything that gates the next step
says what to do in the footer error, not in the check line.

On the API keys page the same line checks the saved key when nothing is typed, so it says
whether the key in use works, not only that one is set.

```css
.check-line { display: flex; align-items: center; gap: 6px; min-height: 20px; font-size: 13px; color: var(--text-2); }
.check-line.ok { color: var(--ok); }
.check-line.err { color: var(--danger); }
.setup .check-line { margin: -9px 0 16px; }       /* tucked up under a wizard field */
.key-status + .check-line { margin-top: 8px; }   /* under a key's Saved / Not set row */
```

### Choice groups (mode cards, segmented pickers)

A group of mutually exclusive cards is a **radiogroup**, not a row of clickable divs: `role="radiogroup"`
on the container with an `aria-label`, `role="radio"` + `aria-checked` on each card, roving `tabIndex`
(`0` on the selected one, `-1` on the rest), arrow keys to move, Space/Enter to pick. The same roving
pattern covers a `role="tablist"`, whose panel takes `role="tabpanel"` + `aria-labelledby`. Styling is
unchanged — `.mode-card.sel` and `.dev-tab.active` still carry the visual state.

### Setup card (the first-run wizard)

The setup card changes height on every step and whenever something arrives inside one: a
check's answer, the intents warning, the redirect URLs ticking off, an error. Three rules keep
that from reading as the card jumping around.

**It hangs from a fixed line; it doesn't centre.** A centred card moves its top by half of
every change, so the title and progress bar jumped as much as 110px between steps, and the bot
token field slid 63px while it was being typed into, the moment the intents warning appeared.
`.setup` puts the card's top where a typical card (`--card-rest`, 560px) would sit centred. A
shorter step leaves the room below it; a taller one scrolls as before. The server control panel
shares `.setup`, so a deploy that lands on it keeps the logo where the wizard had it.

**The height tweens, and the footer rides the edge.** `useHeightTween` (setup.tsx) watches an
inner wrapper that always sits at its content's height and animates `.box-body` from the height
it had to the one it now needs, clipped along the bottom only. The footer is outside that body,
so Back and Continue travel with the edge instead of being uncovered by it. This is the one
place the console animates `height`, and it is deliberate: the `0fr → 1fr` track trick only
collapses and expands, it can't interpolate between two content heights. It was measured, not
assumed. At 6× CPU throttling a step change costs about 1ms of layout per frame, because only
the body resizes and nothing inside it is laid out again. Duration grows with distance (a
20px check line in ~190ms, a whole step in ~300ms, capped at 340ms); a mid-tween change starts
from wherever the edge is; a width change snaps, because a card trailing a window drag reads as
lag; reduced motion snaps.

**Only what changed plays, and nothing plays on first paint.**

| | |
|---|---|
| Step | arrives from the side it was travelled to: 10px and a fade over `--dur-slow`, in the time the card takes to resize around it |
| Screen | the wizard ↔ "Connect to an existing server" moves the same way, and focus lands on the new screen's IP field, or back on the button that opened it |
| Progress bar | one grid track per step of the longest hosting choice; the tracks past this choice's last step are `0fr`, so picking a choice grows or shrinks the bar at its end, and two choices with as many steps leave it still. A segment fills from the left going forward and empties back toward it going back |
| Arrivals | `.wiz-appear`, 3px and a fade. A check line keeps its `role="status"` element and replaces only the words inside it, since a screen reader announces a change inside a live region and can miss one that turns up already filled. A refused Continue replays its reason, so a second press visibly did something |
| Confirmations | `.wiz-pop` for what Discord or Tailscale just confirmed ("Added", "Live at …") |

**One footer, one primary button.** The first step used to render its own Continue inside the
hover-reveal group, so moving past it swapped the element and dropped keyboard focus to the
page. Every step and both screens now share one footer and one primary button whose label and
action change, so Enter walks the whole wizard.

Under `prefers-reduced-motion` nothing travels: the height snaps, entrances only fade, and the
bar fills by colour rather than by sweep.

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

### Section (the flat group)

The console draws no cards. A group of settings is a **Section**: a hairline above it, its title in a 200px rail on the left, and its settings as rows beside it. The pages used to be cards whose inputs were themselves bordered boxes, and three levels of border read as clutter before they read as structure. Here the controls are the only things drawn.

```css
.section { container-type: inline-size; border-top: 1px solid var(--border); padding: 28px 0; position: relative; }
.section-grid { display: grid; grid-template-columns: 200px minmax(0, 1fr); column-gap: 40px; row-gap: 18px; }
.section-head { align-self: start; position: sticky; top: 24px; padding-top: 5px; }  /* on the first row's label line */
.section-head h2 { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.section-head .hint { color: var(--text-2); font-size: 12.5px; line-height: 1.5; }

/* A settings row: label and description on the left, a 300px control column on the right. */
.field-row { display: grid; grid-template-columns: minmax(0, 1fr) 300px; column-gap: 32px; padding: 16px 0; }
.field-row.wide { grid-template-columns: minmax(0, 1fr); }     /* control under its label */
.field-text { padding-top: 6px; }       /* the label's first line on the centre of a 34px control */
.field-text > label { font-size: 13.5px; font-weight: 550; }
.section-body > .field-row + .field-row { border-top: 1px solid var(--border); }
.section-body > * + * { margin-top: 16px; }                    /* everything else is spaced, not ruled */

.section.stacked .section-grid { grid-template-columns: minmax(0, 1fr); }   /* title above the body */
.section:hover, .section:focus-within { z-index: 1; }
@container (max-width: 860px) { .section-grid { grid-template-columns: minmax(0, 1fr); } }
@container (max-width: 560px) { .field-row { grid-template-columns: minmax(0, 1fr); } }
```
```jsx
<Section title="Engagement" hint="When and where Olisar joins the conversation.">
  <Field label="Reply in DMs"><Toggle value={on} onChange={set} /></Field>
  <Field wide label="System prompt" desc="…"><Area value={text} onChange={setText} /></Field>
</Section>
<Section stacked title="Requests over time" actions={<Segmented … />}>{chart}</Section>
```

- **Every Field placed directly in a Section is a row.** Switches, chip sets and segmented pickers sit at the right edge of the control column; inputs and selects fill it.
- **A switch in a row carries no text of its own.** The row label is its name, which means no two rows on a page can both be called "Enabled".
- **`wide`** puts the control under its label across the whole row: a textarea, an editor, a reply beside its Discord preview.
- **`plain`** is for a row holding several controls, such as a chip set or a pair of hour boxes. The row becomes a `role="group"` named by its label.
- **`Stack`** takes a compose form back to label-above-control. "Add a source" is one entry typed into two inputs side by side, not two settings.
- **`stacked`** puts the title above a full-width body. Use it for lists, tables and charts that need the width the rail would take. A railed section falls back to exactly this shape below 860px of its own width, so a page can mix the two.
- **`actions`** holds a control that acts on the whole group (a range picker, a refresh button): top right when stacked, under the hint in the rail.
- **Hairlines go between setting rows only.** A legend, a toolbar, a callout, or a list that rules its own rows is spaced by 16px. A line between a filter box and the list it filters cuts one control in two.
- **A collection is a list, not a card grid.** Members and the marketplace are rows under hairlines. A grid of cards makes each one as tall as its wordiest neighbour and draws a box around what is a name, a line of text and a button.
- **Two panes side by side are split by a vertical hairline** (the Extensions list and detail, the Usage charts), never two boxes with a gap between them.
- **The section title is an unnamed `<section>`'s `h2`.** A named section is a region landmark, and five or six regions a page is noise in the landmark list; the heading is the navigation stop.

Two things that will bite you if you rebuild it:

- **Query the container, not the viewport.** The rail's breakpoint is a container query because a media query fires at a physical width while the layout inside it is zoomed by `--ui-scale`. The section measures its own px, and the rail collapses in a narrow Extensions detail pane the same way it does on a phone.
- **Size containment makes each section a stacking context.** A popup that runs past a section's bottom edge (the "+N" roles card, a menu) would paint under the next section, so the section being hovered or focused rises above its siblings. Anything absolutely positioned inside a section depends on that rule.

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

### RoleChip

A Discord role, wearing **its own colour** — the way it reads in Discord's member list, so a role
is recognised rather than read. The API hands back the role's hex; set it as `--rc` inline and let
`color-mix` derive the fill and edge, so one variable drives the whole chip. An uncoloured role
(Discord returns `""`) falls back to the neutral ramp rather than inventing a hue.

```css
.rolechip { display: inline-flex; align-items: center; gap: 7px;
  padding: 2px 10px 2px 8px; border-radius: var(--radius-pill);
  /* Explicit, because a chip can sit inside a mono trigger and would inherit it. */
  font-family: var(--font-sans); font-size: 12.5px; font-weight: 550; color: var(--text);
  background: color-mix(in srgb, var(--rc) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--rc) 40%, transparent); }
.rolechip-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--rc); flex: none; }
.rolechip.plain { background: var(--bg-inset); border-color: var(--border); color: var(--text-2); }
.rolechip.plain .rolechip-dot { background: var(--text-3); }
```
```jsx
<span className="rolechip" style={{ '--rc': role.color || 'var(--text-3)' }}>
  <i className="rolechip-dot" /> {role.name}
</span>
```

**The colour is not the label.** A server can colour two roles identically, or none at all — the
name always ships beside the dot. The dot is redundant encoding, not the encoding.

### StatTile (metric) & Spinner

A single metric: an eyebrow, a big mono number, and a delta or caption under it. Several sit in one strip divided by vertical hairlines, not in boxed tiles; the four on Usage are one reading of today, and four borders said four unrelated things. The spinner is a minimal accent ring for quiet loading states.

```css
.u-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid var(--border); }
.u-kpi { padding: 24px 24px 28px; border-left: 1px solid var(--border); }
.u-kpi:first-child { border-left: 0; padding-left: 0; }
.u-big { font-family: var(--font-mono); font-size: 26px; font-weight: 500; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
/* 2 × 2 below 1120px (the second row takes a top rule), one column below 560px. */

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
.callout .linklike { color: var(--cc); }  /* an inline action ("Set a PIN") reads as a link */
.callout.warning { --cc: var(--warn); }
.callout.note    { --cc: var(--accent); }
.callout.tip     { --cc: var(--ok); }
```

### Toast (bottom-right status)

Same tinting as the callout, fixed bottom-right, with a filled-circle icon in the state colour; slides in from the right. States: success / warning / danger / info / neutral.

**Failures do not expire.** `success`, `info` and `neutral` dismiss themselves on a timer — a
confirmation the operator missed costs nothing. `danger` and `warning` wait to be dismissed, carry
a close button, and set `user-select: text` so the message can be copied into a bug report. A
timed error is an error the operator was told about and then had taken away, and the one thing they
need from it — the exact wording — is the thing they were reading when it vanished. Tone also
picks the live region: `role="alert"` for the two that persist, `role="status"` for the rest.

**`toast(message, tone, opts?)` returns a `{ dismiss }` handle**, for work whose end the toast
can't predict. Options override the tone default:

- **`sticky`** — pin it open regardless of tone (long-running work).
- **`busy`** — spinner in place of the tone glyph: this toast is progress, not a verdict.
- **`action: { label, onClick }`** — a trailing `ghost` text button (34px, e.g. **Stop** on a
  marketplace publish). Firing it also dismisses the toast.
- **`durationMs`** — auto-dismiss delay; ignored when sticky.

An action toast shows no close ×; the action is the way out, and dismissing the only handle on
work still running would strand it. `role` stays keyed to the *tone*, so a pinned progress toast
announces as `status` rather than interrupting as an alert.

```css
.toast { position: fixed; right: 24px; bottom: 24px; display: flex; align-items: center; gap: 13px;
  min-width: 300px; max-width: 430px; padding: 13px 15px; border-radius: var(--radius);
  border: 1px solid var(--tc-border); background: color-mix(in srgb, var(--tc) 11%, var(--panel)); box-shadow: var(--shadow-pop);
  transform: translateX(24px); opacity: 0; transition: transform .3s var(--ease-out), opacity .24s ease; }
.toast.show { transform: translateX(0); opacity: 1; }
.toast .ic { color: var(--tc); font-size: 22px; }      /* solar:check-circle-bold etc. */
.toast .title { font-size: 13.5px; font-weight: 650; }
.toast .toast-msg { flex: 1; min-width: 0; }           /* long text truncates, action stays put */
.toast-action { flex: none; margin: -4px 0 -4px 4px; } /* trailing ghost button */
.toast.success { --tc: var(--ok); --tc-border: var(--ok-border); }
.toast.danger  { --tc: var(--danger); --tc-border: var(--danger-border); }
.toast.warning { --tc: var(--warn); --tc-border: var(--warn-border); }
.toast.info    { --tc: var(--info); --tc-border: var(--info-border); }
```

### Overlays (Dialog / Modal / SaveDock / ActionMenu / HoverCard)

- **Dialog** (centered info+action): blurred backdrop `rgba(0,0,0,.55)` + `backdrop-filter: blur(3px)` fading in; the card (`--panel`, `--border-strong`, `--shadow-modal`) scales-and-lifts from `translateY(12px) scale(.96)` → `0/1` over `.22s var(--ease-out)`. Optional tinted icon tile (46px, `--radius` 15px) + footer actions. Close on backdrop click / Escape.
- **Modal** (full-UI sheet): same backdrop; a `min(900px,94vw) × min(620px,90vh)` sheet with a header (title + close ×), a scrollable body (put a two-pane nav+content inside), and an optional footer.
- **SaveDock** (unsaved-changes bar): `position: fixed; bottom: 22px; left: 50%`; slides up from `translate(-50%,170%)` → `translate(-50%,0)` over `.3s var(--ease-out)`. Inner padding is `4px 4px 4px 18px` — 4 around the buttons so their `--radius-sm` corners sit concentric inside the dock's `--radius`, 18 of lead-in for the message. It owns the bottom corner when something else is already there, but **only where the two actually overlap**: the dock is centred over the content column and the test-chat FAB is pinned right, so the gap between them grows with the viewport (25px at 1100, 115px at 1280, 435px at 1920). Gate the dodge behind the breakpoint where they meet — a control that jumps 64px to avoid something nowhere near it is worse than the collision it was written for. A `--panel` pill, message + Reset/Save. It carries `role="status"`: "You have unsaved changes." → "Saved" → the failure text is the state machine the whole product is organised around, and it should be announced, not just drawn.
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
| Exit | a `.14s` fade + `translateY(6px)` on the way out — softer and shorter than the `.22s` entrance, ease-out both directions |

**The exit belongs to the shell, not the caller.** Every overlay entered over `.22s` and left on
the frame it closed, because the *caller* owns the mounting (`{open && <Thing/>}`) and React
can't hold an unmount open from inside the child. Threading a `closing` flag out to all eleven
call sites — `SettingsModal` alone is rendered from five files — would put the same four lines
in five places and guarantee the sixth forgets. Instead the shell hands off its own corpse on
the way out: a frozen, `inert`, `aria-hidden` clone of the backdrop plays the exit and removes
itself. What a clone loses (handlers, focus) is what an exiting dialog shouldn't have anyway.

Two things that will bite you if you rebuild it:

- **Capture the node at mount, not in the cleanup.** React detaches object refs *before* it runs
  effect cleanups for a deleted tree, so `ref.current` is already `null` down there and the exit
  silently does nothing. This looked exactly like a broken keyframe.
- **Copy `scrollTop` into the clone.** A tall scrollable sheet that snaps to the top for its last
  140ms is a worse artifact than no animation at all.

The visual recipe above is unchanged; the shell only adds behaviour. A drawer that stays
mounted while closed (so it slides rather than pops) sets `inert` while hidden — `aria-hidden`
alone leaves its controls in the tab order, hidden from the screen reader that would name them.

**A modal makes the page behind it inert too.** `aria-modal` is a promise to assistive tech, not
an enforcement: without `inert` on the app root, the whole surface stays in the accessibility tree
and the skip link stays focusable behind the dialog. Render the overlay through a portal to
`document.body` so the root can be marked — a modal nested *inside* the element you are disabling
disables itself — and refcount the flag, because two stacked dialogs closing in sequence must not
re-enable the page under the one still open.

### Guarding unsaved work

If the system's promise is "nothing is applied until you press Save", then every route out of a
dirty page has to honour it — **navigation included**. A tab switch, a server switch and a window
close each destroy edits silently otherwise, and the operator's only signal was a save bar that
disappeared with the page.

Keep the check in one place: a module-level registry that each editing hook registers its own
`isDirty` with on mount and drops on unmount, plus one `hasUnsavedChanges()` the shell consults.
Every page routing through the shared hook is then covered with no per-page code, and the guard
lives above the navigation rather than on the nav item — a deep link from a docs page calls the
same navigate function and must be gated by it, not around it. Pair it with `beforeunload` for the
one exit the app doesn't own. A failed save leaves the page dirty, which is correct: that is
exactly when leaving would cost the most.

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

**Segmented padding is 4px, and that number is arithmetic, not taste.** Outer radius minus padding equals inner radius: the shell is `--radius-sm` (12px) and the chip `--radius-xs` (8px), so the gap has to be 4. At 3px the chip's corner is tighter than the well around it, which is the single most common thing that makes a control look slightly wrong without anyone being able to say why. The same sum governs the ActionMenu (`--radius-sm` shell, 4px padding, `--radius-xs` rows) and the SaveDock (`--radius` shell, 4px padding, `--radius-sm` buttons).

### Navigation (NavItem / PageNav / Avatar)

- **NavItem** (sidebar row): `padding: 7px 10px; border-radius: var(--radius-sm); color: var(--text-2)`. Hover → `background: var(--bg-inset); color: var(--text)`. Active → same bg, `font-weight: 600`, icon swaps to `-bold`.
- **PageNav** ("On this page"): retired from Docs, which is two panes now (see **Documentation layout**). If a page index comes back somewhere: a header (list icon + label), a vertical rail (`border-left: 1px solid var(--border)`), items muted (`--text-3`) that brighten on hover; the active item is bold `--text` with a 2px foreground bar on the rail. One level of nesting via extra left padding.
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

**Every chart ships its numbers twice.** A `role="img"` SVG with a summary label is a picture with
a caption — the values themselves are unreachable to a screen reader, and "requests per day" names
the chart without disclosing a single figure. Pair each chart with a visually-hidden `<table>`
carrying the same series:

```css
/* Off-screen for sighted users, present for assistive tech — `display:none` and
   `visibility:hidden` hide it from both. */
.visually-hidden { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip-path: inset(50%); white-space: nowrap; border: 0; }
```

Put the class on a **wrapping `<div>`, never on the `<table>` itself**. A `display: table` box
treats `width: 1px` as a *minimum* and expands to fit its content, so the table clips nothing and
pushes the page sideways — an all-time usage series overflowed the viewport by 88px this way.

### DiscordPreview

Where a setting's output lands in Discord, **show it in Discord** — same chrome, same avatar, same
blurple **APP** badge — rather than printing the string on a grey line and asking the operator to
picture it. Command replies uses this: the message you are editing renders as the message the
server will actually see.

Discord's palette is quarantined behind the `--dc-*` tokens for exactly this reason: the surface is
a picture of another product, and rendering it in Olisar's greys would make the preview a lie. The
design linter rejects raw hex, and widening its allowlist would have licensed those five values
everywhere — a token block with a comment is the honest version.

```css
.dcp { background: var(--dc-bg); border-radius: var(--radius-sm); padding: 14px 16px; }
.dcp-msg { display: flex; gap: 15px; }
.dcp-av { width: 40px; height: 40px; border-radius: 50%; flex: none; overflow: hidden;
  background: var(--dc-brand); display: grid; place-items: center; color: #fff; font-weight: 600; }
.dcp-row { display: flex; align-items: baseline; gap: 9px; margin-bottom: 4px; flex-wrap: wrap; }
.dcp-name { font-weight: 600; font-size: 15px; color: var(--dc-head); min-width: 0; overflow-wrap: anywhere; }
/* The APP badge is what makes this read as Discord rather than as a generic chat. */
.dcp-tag { background: var(--dc-brand); color: #fff; font-size: 10px; font-weight: 600;
  padding: 2px 4px; border-radius: 4px; position: relative; top: -1px; }
.dcp-time { font-size: 11.5px; color: var(--dc-muted); }
.dcp-text { color: var(--dc-text); font-size: 14.5px; line-height: 1.5; white-space: pre-wrap; }
/* A {placeholder} is substituted at send time, so it is shown as a slot — not as literal
   text the reader would expect to appear in the message. */
.dcp-slot { font-family: var(--font-mono); font-size: .85em; padding: 1px 5px; border-radius: 3px;
  background: color-mix(in srgb, var(--dc-brand) 26%, transparent); color: #fff; }
.dcp-empty { color: var(--dc-muted); font-style: italic; }
```

**Preview the real thing.** Bot name from the persona, avatar from the running bot, the operator's
override text when set and the built-in default when not — a preview showing a placeholder identity
is a mock-up, and a mock-up answers no question the operator actually has.

### DangerZone

An irreversible action gets its own section at the **bottom of the page it belongs to**, below
everything. Placement is the argument: the control that wipes an index sits under the index it
wipes, where its scope is visible, not in a settings popup two levels away from anything it
affects.

```jsx
<Section tone="danger" title="Danger zone">
  <Field plain label="Clear memory" desc="Erases everything on this page… This can't be undone.">
    <button className="danger" onClick={clearMemory}>Clear memory</button>
  </Field>
</Section>
```
```css
/* Set apart from the sections above by one step more than their rhythm. Only the title is
   red: the hairline stays grey, and the weight belongs to the confirm dialog, which is where
   the decision is actually made. */
.section.danger { margin-top: 24px; }
.section.danger .section-head h2 { color: var(--danger); }
```

**Name the target in the dialog title**, not just in the body: "Clear everything Olisar knows about
*Ravenhold*". A confirm phrase (`requirePhrase`) proves intent but cannot prove the operator has
the right server selected — the switcher is elsewhere on screen, and that is the mistake the modal
structurally cannot catch.

### ActivityLedger

What was changed here, and when. A destructive action that confirms itself in a toast has left
**no record** three seconds later; if the backend already writes an audit row, reading it back is
the difference between an operator who can answer "did that run?" and one who cannot.

```css
.activity { display: flex; flex-direction: column; }
.act-row { display: grid; grid-template-columns: 116px minmax(0,1fr) auto; align-items: baseline;
  gap: 14px; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.act-row:last-child { border-bottom: 0; }
.act-when { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-3); }
.act-row.destructive .act-what { color: var(--danger); }
/* The receipt — the counts the toast showed for 3.6s and then discarded. */
.act-receipt { display: block; color: var(--text-2); font-size: 12px; margin-top: 2px; }
.act-who { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-3); justify-self: end; }
@media (max-width: 560px) {
  .act-row { grid-template-columns: minmax(0,1fr) auto; }
  .act-when { grid-column: 1 / -1; }
}
```

Two copy rules. **Translate the action names** — a stored `set_channel_indexing` is an internal
identifier, and an operator reading their own history should not have to decode it. And **state
the log's real scope**: if the audit table has no per-server column, say the entries are
install-wide rather than rendering them under a server switcher that implies otherwise.

### ScrollFade (a capped list)

A list that shares a page with other sections stops growing after a set number of rows and scrolls from there: sources after 4, glossary facts after 8, activity after 10. `<ScrollFade rows={n}>` measures its children and sets the height, so the cap is a row count rather than a pixel height. Rows here run from one line to a stacked control group, and a fixed height would show three sources or twelve facts.

It stops partway into the first hidden row, so that row shows through the bottom fade. A cut that lands on a row boundary reads as the end of the list. Each edge fades only while there's more past it: the top stays sharp until you scroll.

```css
@property --fade-top { syntax: '<length>'; inherits: false; initial-value: 0px; }
@property --fade-bottom { syntax: '<length>'; inherits: false; initial-value: 0px; }
.scroll-fade {
  position: relative; overflow-y: auto; padding-inline: 4px; margin-inline: -4px;
  mask-image: linear-gradient(to bottom, transparent, black var(--fade-top), black calc(100% - var(--fade-bottom)), transparent);
  transition: --fade-top var(--dur-mid) var(--ease-out), --fade-bottom var(--dur-mid) var(--ease-out);
}
.scroll-fade.fade-top { --fade-top: 36px; }
.scroll-fade.fade-bottom { --fade-bottom: 36px; }
```

A mask, not a gradient overlay, so it works on any surface without knowing the colour behind it. The inline padding keeps focus rings inside the clip. Don't use it where the list is the whole view (the Activity pane in Settings runs full length).

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

## Documentation layout

A docs surface is two panes: the section nav and the article. There used to be a third, an "On this page" index on the right, and it was removed: the article's own headings do that job, and the rail's 212px was worth more as article. The mistake either way is letting the rails be the constants and the article be whatever they leave over.

**State the measure; derive everything else from it.** Before this rule the console's docs
article was pure residue: 41 characters per line on a 900px window, 55 with a page index, 90
*without* one at the same size, 106 at 1680px. None of those were chosen, and the widest came
where the reader had the most screen.

```css
.docs-shell {
  grid-template-columns: 230px minmax(0, 1fr);
  --doc-measure: 580px;   /* ~85 characters at the 15px body */
}
/* Prose holds the measure. Tables and code are deliberately absent from this list and take
   the full column — a reference table squeezed into a prose measure is just a horizontal
   scrollbar, which is the reader doing the layout's job. */
.docs-eyebrow, .docs-title, .docs-prevnext,
.doc > p, .doc > ul, .doc > ol, .doc > h2, .doc > h3, .doc > .callout {
  max-width: var(--doc-measure);
}
```

Not `ch` — see the measure note under **Marketing site**: IBM Plex Sans's zero is 1.33× its
average advance, so a `ch` value overstates a real line by a third.

**Read like documentation, not like a settings row.** The article body is 15px at a 1.75 line with 18px between paragraphs; `##` is 20px with 48px above it, `###` 16px with 32px; list items sit 8px apart; callouts, code and tables take 22–26px either side. At the 13.5px / 1.6 / 10px it had before, the Docs page was set like the settings pages around it, and a reader working through a section had no rest between blocks. Tables stay a step under the body at 13px because they are read across, not down. All of it is scoped to `.docs-content .doc`: the same `.doc` renders Markdown in test-chat replies and extension readmes, which want the compact rhythm.

**The nav yields at the width where it starts costing the measure.** Below that it becomes a band above the article. Pick the breakpoint by solving for the measure, not by round numbers: the nav goes when its rail plus the app's own sidebar would push the article under ~65 characters. Verify by reading the realized
line length at each step rather than trusting the arithmetic — and remember media queries do
not zoom, so a breakpoint fires at `value / --ui-scale` of effective width.

**A prev/next row wraps.** Two neighbour titles are content, not a fixed pair. `display: flex`
with the default `nowrap` and `min-width: auto` let a long pair push the *document* sideways —
"Create your own" beside "Slash commands & flows" wants 363px, and in a 248px column that was
a real 74px horizontal page scroll. `flex-wrap: wrap` plus `min-width: 0` on the children.

```css
.docs-prevnext { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; }
.docs-prevnext > button { min-width: 0; max-width: 100%; }
```

## Do / Don't

- **Do** structure a page with Sections: a hairline and a heading. **Don't** put a box around a group of settings, and never a box inside a box.
- **Do** reserve the accent for selection, links, focus, and active state — never as a fill for big surfaces.
- **Do** use one **primary** (bright-neutral) button per view; everything else is secondary/ghost.
- **Do** keep motion quiet (.12–.3s, ease-out), and always honour `prefers-reduced-motion` — by **slowing** motion, not deleting it. A spinner with `animation: none` is a static ring that tells the operator nothing; `animation-duration: 1.6s` still says "working".
- **Do** write the tokens: `var(--dur-fast|mid|slow)` and `var(--ease-out)`, not `.12s` and a bare `ease`. Bespoke durations are fine for bespoke moves (a .5s progress fill, a 1.4s press-and-hold) — it's the three canonical values drifting into literals that turns one motion system into twenty-eight.
- **Don't** animate a layout property. `left`, `top`, `width` and `bottom` re-lay out the page on every frame; `transform`, `translate`, `opacity` and `filter` composite on the GPU. The toggle knob travels on `transform`, the toast stack steps aside on `transform`, and the test-chat FAB lifts on `translate` — `translate` specifically, because its `transform` is already spoken for by the press scale, and one property can't carry two jobs without the more specific rule silently eating the other. The setup card's height tween is the one measured exception (see **Setup card**).
- **Don't** let a press scale go past `.96`. Below that it reads as a bounce rather than a press.
- **Do** give every `div` you attached an `onClick` to a `role`, a `tabIndex`, and a key handler in the same breath — or make it a `<button>`. This is the failure that recurs.
- **Don't** use emoji, bluish-purple gradients, drop shadows on anything that doesn't float, or Title Case headings.
- **Don't** introduce new hues — use the accent or a semantic state.
- **Don't** let a control be named by `title` alone — the tooltip host strips it on focus. See **Button & IconButton**.
- **Do** expect the bot's name to be long and unbreakable. The console calls the bot by its Discord name (`botName()`, up to 32 characters, often no spaces), so it lands in headings, labels, hints and buttons. The body's `break-word` covers text in a box of fixed width. A flex or grid item, or anything centered at its content's width, sizes to its longest word, so it needs `overflow-wrap: anywhere` on that element (see `.dcp-name`, `.mode-legend`, `.login h1`). Keep the name out of single-line rows that truncate, like the Get started steps. Test with `"W".repeat(32)` at 390px.

### Verify before shipping

The build gates on `npm run design-lint` — token use, the spacing scale, radius tokens, button
variants, no native `alert/confirm/prompt`. It cannot see any of the following, so check them
by hand on the surface you touched:

- Body-size text ≥ 4.5:1 **against the lightest ground it lands on**. `--text-2` and `--text-3` both clear it; anything you tint yourself may not.
- Focus visible on every interactive element, including the ones on `--primary-bg`.
- Tab through the whole surface: no control skipped, none trapped, nothing focusable inside a hidden container.
- Every icon-only control has an `aria-label`; every input has a label pointing at it; every switch has a subject.
- The shell at 375px, and the widest table or chart on the surface. Assert `documentElement.scrollWidth === clientWidth` — a visually-hidden `<table>` and an unclamped `min-width: auto` grid child both overflow silently.
- Every mutually exclusive group announces as one: `role="radiogroup"` + `role="radio"` + `aria-checked`, roving `tabIndex`, arrow keys. A styled `.sel` class is not a selected state.
- Leave a dirty page by every route you shipped — tab, server switcher, deep link, reload.
- Read the **realized** line length on a text surface, don't assume it. `scrollWidth` on a `overflow: visible` element is not an overflow oracle either — scroll the page and read `window.scrollX`.
- Reach every page with real data before calling it reviewed. A page the dev fixture can't populate is a page nobody looks at, and it is where the crash will be.
- Call hooks above the `if (loading) return <Spinner />`. A hook that only runs once data arrives changes the hook count between renders, which React treats as fatal — the page dies rather than degrades.
- Probe the element the style is actually on. A focus ring on `.toggle .track` reads as "no focus ring" if you measure `.toggle`, and `.focus()` from a script does not match `:focus-visible` at all — press a real Tab first.
- Break the backend and look again: a dead poll must not render as an idle one, and a failed action must leave something on screen.

---

*Provenance: distilled from the Olisar console front-end (`gcrft123/olisar`, `web/src`). For the full component library, specimen cards, and an interactive console recreation, see the Olisar Design System project this was exported from.*
