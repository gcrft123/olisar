# PROMPT — Build the "MACHINE & EMBER" design system

> Paste this whole document into Claude (Artifacts, Claude Code, or any design tool).
> It is a complete, self-contained brief. Follow it literally. Where it gives a hex,
> a clamp(), or a pixel value, use that exact value — do not "improve" the palette or
> round the scale. Creative latitude lives in *composition*, not in the tokens.

---

## 0. ROLE & GOAL

You are a senior product designer + front-end engineer building a reusable **design
system**, not a one-off page. Output tokens first, then components, then example
screens that prove the components compose. The system must be able to skin a marketing
site, a docs page, and a data dashboard without changing a single token.

**Codename:** MACHINE & EMBER (a.k.a. "Halfsteel").
**One-sentence thesis:** *One machine, warmly lit.* The interface is industrial-brutalist
telemetry **everywhere** — square, monospace, rigid 1px-hairline grids, near-black,
scanlines. EMBER is not a second mode or a separate section: it is the **warmth and
lighting threaded through that one machine** — faint blooms behind the grids, pastel pill
tokens, warm accent text, ember-tinted glows, and exactly one halftone serif word woven
into an otherwise uppercase headline.

The split is **~85% machine / ~15% ember**. Ember is the minority accent, dosed in, never
a room you walk into. Do **not** blend into mush and do **not** quarantine the warmth into
one soft section that flips font/radius/casing wholesale — that reads as two websites
stitched together. Instead: keep the *structure* machine (square, gridded, mono metadata,
crosshairs) and turn only the *temperature* warm. Structure never rounds; only the light
gets warm.

---

## 1. DELIVERABLES (output all of these)

1. A `:root` CSS custom-property block containing every token in §2–§6 (copy it verbatim from the Appendix).
2. A component library implementing every recipe in §7, each with default + hover + focus + active + disabled states.
3. Three example screens that reuse the components:
   - **Marketing hero** (machine structure throughout, ember warmth threaded in).
   - **Docs / article page** (machine chrome, calmer reading body).
   - **Telemetry dashboard** (dense machine grid, live-feel, warm accents).
4. A one-paragraph "how to extend" note explaining the machine-dominant blend so the next person doesn't over-warm it or split it back into two modes.

Stack: single self-contained HTML file with vanilla CSS + minimal vanilla JS (no build step, no framework, no external JS libs). Web fonts via Google Fonts `<link>` are allowed. Everything else inline.

---

## 2. COLOR SYSTEM

Black substrate + a single accent family (hazard orange). **One accent, locked page-wide.**
The "soft/colorful" feeling comes from *tints of the same orange hue* (amber → peach →
cream), never from introducing a second hue. Exactly one green exists and it is spent on
a single element.

### 2.1 Substrate (near-black, never pure #000)
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#0A0A0A` | page background |
| `--bg-panel` | `#0E0D0C` | raised panels, hovered cells |
| `--bg-well` | `#070605` | inset wells, HUD interiors, terminal body |
| `--bg-deep` | `#050403` | status bar, footer, section blooms |

### 2.2 Foreground (warm near-white "phosphor" — softens the machine)
| Token | Hex | Use |
|---|---|---|
| `--fg` | `#F3EEE7` | primary text, headlines |
| `--fg-dim` | `#B6AFA5` | body copy, secondary text |
| `--muted` | `#7C766C` | labels, metadata, telemetry |
| `--faint` | `#4A453E` | crosshair marks, disabled |

### 2.3 Accent — HAZARD ORANGE (the only accent)
| Token | Hex | Use |
|---|---|---|
| `--o` | `#FF5A1F` | primary accent: links, selection, focus, active, one bright button |
| `--o-bright` | `#FF7E33` | hover / emphasis of the accent |
| `--o-ember` | `#C7370B` | deep end of orange gradients |

### 2.4 Soft "human-layer" warmth (SAME hue family — not new accents)
| Token | Hex | Use |
|---|---|---|
| `--amber` | `#FFB25A` | soft-layer chips, kickers |
| `--peach` | `#FFCE9E` | soft-layer body text, headings-on-bloom |
| `--cream` | `#FFF1E2` | warm cell headings, highlights |
| `--blush` | `#FF8A4C` | soft-layer bloom + kicker labels |

### 2.5 The single green
| Token | Hex | Use |
|---|---|---|
| `--term-green` | `#4AF626` | **exactly one element** — the `OK`/success line inside the terminal. Never body text, never a second place. |

### 2.6 Structure
| Token | Value | Use |
|---|---|---|
| `--line` | `rgba(243,238,231,0.11)` | primary hairline borders + grid gaps |
| `--line-2` | `rgba(243,238,231,0.06)` | faint internal dividers, blueprint grid |
| `--edge` | `#1B1916` | the giant "watermark" wordmark color |

### 2.7 Color rules (enforce)
- **No gradients as hard structure.** Gradients are allowed only as *warmth*: (a) ember radial blooms behind sections, (b) faint warm top-tints on cells (`linear-gradient(180deg, rgba(255,138,76,.04), transparent 58%)`), (c) fills inside data viz (equalizer bars, sparklines), (d) the halftone serif. Never a gradient as a button/panel fill.
- **Shadows are ember-tinted, never neutral black**, and used with restraint: a resting glow under the single primary button, a hover glow on grid cells, a lift shadow under the featured pricing tier / HUD. Tint with `rgba(255,90,31,.4–.85)`.
- Selection = orange bg, `#0A0A0A` text.
- Contrast floor WCAG AA: body ≥ 4.5:1, large text ≥ 3:1. `--muted` on `--bg` is for ≥11px uppercase metadata only.

---

## 3. TYPOGRAPHY

Type is the primary structural material. Four families, four jobs. Never add a fifth.

| Family | Token | Job | Import |
|---|---|---|---|
| **Archivo Black** | `--black` | Macro display: wordmarks, section H2s, giant numerals. Uppercase, ultra-tight tracking. | `Archivo+Black` |
| **Space Grotesk** (400–700) | `--grot` | Warm accent type: hero subhead, cell/card headings, and any calm body copy (sentence case). The warmth-carrying sans against the mono machine. | `Space+Grotesk:wght@400;500;600;700` |
| **JetBrains Mono** (400/500/700) | `--mono` | ALL telemetry: metadata, nav, labels, body copy in machine sections, the terminal. Uppercase for labels. | `JetBrains+Mono:wght@400;500;700` |
| **Playfair Display** italic 600 | `--serif` | **Textural disruption, used exactly ONCE** on the whole page — a single word, halftone-dithered (see §5.5). | `Playfair+Display:ital,wght@1,600` |

### 3.1 Scale (use these clamps verbatim)
- **Hero wordmark (H1):** `font: 400 clamp(4.5rem, 15vw, 13rem)/0.82 var(--black)`, `letter-spacing:-.045em`, uppercase. Allowed to bleed off the right edge.
- **Section H2 (machine):** `clamp(2rem, 5.4vw, 4.6rem)/0.9`, `-.035em`, uppercase, `max-width:16ch`.
- **Final-CTA H2:** `clamp(2.6rem, 9vw, 7.5rem)/0.86`, `-.045em`, uppercase.
- **Human-layer H2:** same machine Section-H2 style (Archivo Black, uppercase) — the blend is a single lowercase halftone-serif word (`--serif`, peach) woven **inline** into that uppercase headline (e.g. `LOUD MACHINE, <serif>calm</serif> COMMAND`), not a switch to a soft sentence-case display font.
- **Hero subhead:** `500 clamp(1.1rem, 2.3vw, 1.7rem) var(--grot)`, `-.01em`; wrap one phrase in `<b style="color:var(--o)">`.
- **Body (machine):** `13–13.5px var(--mono)`, `line-height:1.7`, `color:var(--fg-dim)`, `max-width:46ch`.
- **Body (human):** `var(--grot)`, `~1rem/1.6`, `color:var(--peach)`, `max-width:52ch`.
- **Telemetry / eyebrow / labels:** `10.5–11px var(--mono)`, `letter-spacing:.14em–.24em`, `text-transform:uppercase`, `color:var(--muted)`.
- **Stat numerals:** `var(--black) clamp(2.6rem,5vw,4rem)`, `-.04em`; unit suffix `var(--mono) .32em color:var(--o)` as `<em>` superscript.

### 3.2 Casing rules
- Structural / machine type: **UPPERCASE** — section IDs, H2 headlines, nav, labels, telemetry.
- Warm accent type: **sentence case** — cell/card headings and calm body in `--grot`, plus the single inline serif word. Sentence case is a warmth cue *inside* a machine section, not a signal that you've entered a different mode.

---

## 4. SPATIAL SYSTEM

Layouts must look mathematically engineered: anchored to a grid, compartmentalized by
visible hairlines, never floated.

### 4.1 Spacing scale (px) — snap everything to this
`4, 6, 8, 10, 12, 14, 16, 20, 22, 26, 30, 34, 40, 48, 64, 88`. No off-scale padding/margin/gap.

- Page gutter: `--pad: clamp(20px, 5vw, 88px)`.
- Section vertical rhythm: header block `clamp(48px,7vw,96px)` top; big moments `clamp(64px,9vw,140px)`.
- Content max-width for centered blocks: `1180px`.

### 4.2 The corner-radius rule (shape-lock)
- **Everything is radius 0.** All 90° corners, in every section: buttons, cards, inputs, panels, cells, badges, the terminal, the HUD.
- **The ONE rounded token is the pill chip (`999px`)** — a small status/label pill. It is the single place softness is allowed, precisely because it's tiny and clearly a token, so it reads as an ember detail rather than a shape-system change.
- Nothing in between (no 6px/12px cards). If you feel the urge to round a card to make it "warmer," add a bloom or a warm tint instead — warmth comes from light, not from radius.

### 4.3 The hairline-grid technique (use everywhere for multi-cell zones)
```css
.grid { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--line); }
.grid > * { background:var(--bg); }         /* razor-thin 1px dividers, no per-cell borders */
.grid > *:hover { background:var(--bg-panel); }
```
Put a `+` crosshair at cell corners (`position:absolute; top:-6px; left:-6px; color:var(--faint)`).

### 4.4 Control heights (fixed, shared)
- Status bar: **30px**. Nav: **64px**. Primary/secondary button: **44px**. Small button: **34px**. Pill chip: **28px**.

### 4.5 Layout discipline
- Hero fits within `100dvh` minus chrome; headline ≤2 lines, subtext ≤20 words, CTAs visible without scroll. Never `h-screen`; use `min-height:calc(100dvh - <chrome>)`.
- Anti-center bias: hero is a 2-column asymmetric split (≈`1.15fr .85fr`), not centered. The final CTA may center (it's a manifesto moment).
- Nav renders on **one line** at desktop; collapse links below 640px (keep only sign-in + primary CTA).
- No layout family repeats more than once (grid, split, marquee, stat-row, terminal, pricing table, footer-mega each appear once).
- Page body must never scroll horizontally; wide content scrolls inside its own container.

---

## 5. TEXTURE & ATMOSPHERE

Analog degradation, engineered in CSS/SVG. Subtle — these are seasoning, not the meal.

### 5.1 Film grain (global)
Fixed overlay, `z-index:9000`, `pointer-events:none`, `opacity:.05`, `mix-blend-mode:overlay`,
background = an inline SVG `feTurbulence` fractal-noise data-URI (baseFrequency `0.85`, 3 octaves).

### 5.2 Scanlines
`repeating-linear-gradient(0deg, transparent 0 2px, rgba(0,0,0,.14) 2px 3px)`.
Global faint version `opacity:.35`; stronger version (`opacity:.5`) applied only to the hero field and the terminal body.

### 5.3 Ember blooms (warmth threaded through the machine)
Radial gradients of orange→peach, blurred, e.g.
`radial-gradient(closest-side, rgba(255,90,31,.32), rgba(255,138,76,.10) 45%, transparent 72%)`, `filter:blur(18px)`.
This is the primary blend device. Dose it as a minority accent, NOT a full-section wash:
- Behind the hero type and behind the final CTA (bookends).
- A faint `.sec-warm` bloom behind dense machine sections — a top-right radial
  (`radial-gradient(64% 52% at 92% -4%, rgba(255,90,31,.10), transparent 60%)`) that shows
  through the header and the 1px grid gaps while the solid cells stay crisp and machine.
- A slightly stronger pair of blooms in the "human layer" section (still square cells on top).
Keep the section *structure* untouched; the bloom only changes its temperature.

### 5.3b Ember-in-machine detail kit (the 6 ways warmth threads in)
1. Section blooms (§5.3) behind hard grids.
2. Warm hover on grid cells: `box-shadow:inset 0 1.5px 0 -0.5px var(--line-ember)` + a faint warm top-tint bg; the `+` crosshair heats to `--o`/`--amber`.
3. Pastel **pill tokens** (§7) — the only rounded thing on the page.
4. Warm accent **text**: amber/peach for select values, cell headings in `--cream`, body in `--peach`.
5. Ember-tinted **glows** under the single primary button (resting) and featured tier / HUD.
6. Exactly one **halftone serif word** (§5.5) inline in an uppercase headline.

### 5.4 Cursor-follow HUD reticle (desktop, pointer:fine only)
A full-viewport vertical + horizontal 1px orange line (`rgba(255,90,31,.22)`) that lerps toward
the pointer (rAF, transform only — never React state), plus a small mono tag showing a live
lat/lon derived from cursor position. Fade in on first move. Disable on touch + reduced-motion.

### 5.5 Halftone serif (the single textural word)
One Playfair italic word, colored `--peach`, broken into a dot-matrix via a mask:
```css
mask-image:radial-gradient(circle at center,#000 39%,transparent 42%); mask-size:5px 5px;
```
Use it exactly once, woven inline into an otherwise uppercase Archivo headline (set the serif
word `text-transform:none; letter-spacing:0`), so machine and ember fuse in a single line.

### 5.6 Industrial symbology
Sprinkle as structural marks, not decoration: `®` `©` `™` (as geometric glyphs), `+` crosshairs at
grid intersections, `[ … ]` and `< … >` framing on labels, `///` `>>>` separators, and fake
telemetry strings (`UNIT / D-01`, `REV 2.6`, `LAT 47.6062`, `SEC.03`).

---

## 6. MOTION SYSTEM

Every animation must be *motivated* (hierarchy / feedback / state / live-ness). No motion for
its own sake. Full `prefers-reduced-motion` support: disable all of the below, reveal content
instantly, render the terminal fully typed.

| Token | Value |
|---|---|
| `--ease` | `cubic-bezier(.22,.61,.36,1)` |
| `--ease-out` | `cubic-bezier(.16,1,.3,1)` |

- **Reveals:** `IntersectionObserver`, `opacity 0→1` + `translateY(26px→0)`, `.7s var(--ease-out)`, threshold ~.18, unobserve after.
- **Count-ups:** stat numerals animate 0→value over ~1400ms, cubic ease-out; fire on reveal.
- **Live signal (equalizer):** ~28 bars, `scaleY` bob, staggered `animation-delay`, `1.2–2s`.
- **Radar ping:** expanding `box-shadow` ring, `2.6s`.
- **Status dot / live dot:** opacity blip, `1.4–2.2s`.
- **Marquee:** ONE per page. `translateX(0→-50%)` `26s linear infinite`, duplicated track for seamlessness.
- **Bloom drift:** slow `translate`+`scale` `15s alternate`.
- **Terminal:** typewriter — prompt/command type at ~34ms/char, output floods at ~8ms/char, blinking block cursor at the end.
- **Primary button:** carries a *resting* ember glow (`0 8px 26px -16px rgba(255,90,31,.85)`) that deepens on hover; `:active { transform:translateY(1px) }`; arrow `→` nudges `translateX(4px)` on hover. Secondary button: border→`--fg` on hover.
- **Grid cells (caps / human / etc.):** hover stays anchored to the rigid grid — NO translate (that breaks a 1px grid). Warm it in place: faint top-tint bg + `inset 0 1.5px 0 -0.5px var(--line-ember)` + crosshair heats to `--o`/`--amber`.

"Motion claimed = motion shown": if you set a lively tone, the hero, a live HUD, scroll reveals,
and the terminal must all actually move. If you can't ship it clean, ship it static.

---

## 7. COMPONENT LIBRARY (recipes)

Build each with all states. **Everything is square** (§4.2); the pill chip is the only rounded token.

1. **Status bar (30px):** `position:sticky;top:0`, `--bg-deep`, bottom hairline. Cells divided by `--line-2`, `10.5px` mono uppercase. Contains: brand + pulsing orange dot, `UNIT / D-01`, `SYS · OPERATIONAL`, a flexible live-feed string, `LAT`, and a live UTC clock. Hide middle cells < 640px.

2. **Nav (64px):** sticky under the status bar, `rgba(10,10,10,.72)` + `backdrop-filter:blur(10px)`, bottom hairline. Left: Archivo-Black wordmark + superscript `®`. Center: mono uppercase links with an animated orange underline on hover. Right: text "Sign in" + one primary button. Collapse to CTA-only < 640px.

3. **Buttons.** Shared: `height:44px` (`.sm` 34px), mono `12px`, `.12em`, uppercase, square, `transition transform .12s`.
   - `.btn` (secondary): transparent, `1px solid var(--line)`; hover border→`--fg`.
   - `.btn-primary`: `--o` bg, `#0A0A0A` text, weight 700; hover→`--o-bright` + orange glow. **Exactly one primary per view.**
   - `.btn-ghost`: transparent, no border (use where a third weight is needed).
   - All: `:active{translateY(1px)}`, visible focus ring in `--o`, disabled `opacity:.4;cursor:not-allowed`.
   - Never mix variants; label ≤3 words, one line, one CTA intent across the whole page.

4. **Eyebrow:** mono `11px`, `.24em`, uppercase, `--muted`, preceded by a 26px orange bar. **Max 1 per 3 sections** (hero counts as one). Machine "section IDs" (`SEC.03 / CONSOLE`) read as telemetry metadata, not marketing eyebrows — still use sparingly.

5. **Section header:** left column = section ID (mono, orange) + big Archivo-Black H2; right column = a short mono note (`max-width:38ch`). Stack on mobile.

6. **Capability cell (dense):** lives in a `gap:1px` grid. Contains `+` crosshair, mono index `[ 01 ]` in orange, Space-Grotesk 700 title (turns orange on hover), mono description, and a bottom `LABEL · <b>value</b>` stat. `min-height:238px`.

7. **HUD telemetry card:** square, hairline border, `--bg-panel`. Header row: `◊ TITLE` + a `● LIVE` blip. Body: the equalizer signal + a 2×2 `gap:1px` readout grid (mono `k` label + Archivo-Black `v` value with orange `<em>` unit) + a blueprint-grid mini-map with radar pings and `+` markers. This is the hero's "real visual" — generated telemetry, **not** a fake product screenshot.

8. **Human cell (warm, in-grid):** lives in the SAME `gap:1px` square grid as the capability cells — square, hairline-divided, `+` crosshair. The blend is tonal only: a faint warm top-tint bg (`linear-gradient(180deg, rgba(255,138,76,.04), transparent 58%), var(--bg)`), a pastel pill chip (the one rounded token), a Space-Grotesk 600 `--cream` heading, `--peach` body, and a mono `--muted` foot label. Hover warms in place (no lift): stronger top-tint + `inset 0 1.5px 0 -0.5px var(--line-ember)`, crosshair → `--amber`. The section sits on ember blooms (§5.3); the cells stay crisp and machine.

9. **Pill chip:** `height:28px`, `border-radius:999px`, mono `10.5px`, tinted bg + matching text (`.chip-amber/.chip-peach/.chip-cream`). The single rounded token in the system — use it as the warm status marker inside otherwise-square cells.

10. **Stat cell:** in a `gap:1px` 4-col grid; mono `k` label, giant Archivo-Black count-up numeral with orange `<em>` unit, mono `sub` caption, `+` crosshair at a corner. Alternate two cells to orange numerals for rhythm.

11. **CRT terminal:** square window, orange traffic-dot + two outline dots, title bar (`OLISAR · /bin/olisar · UNIT D-01`), body with scanline overlay and typewriter output. Color roles: prompt `--o`, command `--fg`, output `--fg-dim`, **success `--term-green` (the only green)**, warn `--amber`, block cursor `--o`.

12. **Pricing tier:** in a `gap:1px` 3-col grid; square cards. Featured tier = `--bg-panel` + `inset 0 0 0 1px var(--o)` + an orange "◆ RECOMMENDED" corner tab. Price in Archivo Black; features as mono list with orange `+` bullets; each card ends in a button. **All CTAs share one label/intent.**

13. **Marquee (single):** full-bleed orange band, `#0A0A0A` Archivo-Black text, faded `///` separators, scrolling `26s linear`. `aria-hidden`.

14. **Footer:** a `gap:1px` grid (brand + 3 link columns) over `--bg-deep`; then a giant clipped Archivo-Black wordmark in `--edge` with one orange letter bleeding off the bottom; then a mono bottom bar with `©`, coordinates, and `BUILD / REV` strings.

---

## 8. PAGE COMPOSITION (marketing example order)

status bar → nav → hero (hard type over an ember bloom, live HUD right) → single marquee →
dense capability grid (ember bloom + warm hover) → **human-layer section** (same square grid,
warmed: blooms + pastel pills + one inline halftone serif word in an uppercase headline) →
count-up telemetry stats (ember bloom) → CRT terminal → pricing (ember glow on featured tier)
→ final CTA (bloom bookend) → footer with mega wordmark.

Warmth is continuous, not sectioned: every zone is machine structure with ember lighting
dialed up or down. The "human-layer" section just runs the warmth dial highest while keeping
the exact same square, gridded, crosshaired bones.

Theme lock: the whole page is one dark theme. Warmth is a *tint of light* on that dark theme
(blooms, glows, warm text), never a light-mode invert and never a section that swaps to rounded
cards + a soft display font. If a section suddenly feels like a different website, you flipped a
mode instead of dialing the warmth.

---

## 9. VOICE & COPY

- **Machine register:** terse, technical, telemetric. "Numbers that don't blink." "Put the fleet online." Unit IDs, coordinates, revs.
- **Human register:** calm, plain, reassuring — used in the warmest zone (the human-layer section) and the final CTA. "Loud machine, calm command." "Nothing runs unseen."
- This is the one place a warmer register is allowed, because that section runs the ember dial highest. Everywhere else, stay in the machine register.
- Label any invented metric as sample data (`SAMPLE FEED`, `<!-- mock -->`). No fake-precise numbers presented as real.
- **No em-dashes** anywhere in visible copy (use `·`, `,`, `:` or a period). No AI-cute wordplay; if a line sounds clever-but-wrong, replace it with a plain sentence.

---

## 10. ACCESSIBILITY

- Full keyboard focus states (orange ring), visible on every interactive element.
- Every icon-only control gets `aria-label` + a tooltip; decorative marquee/reticle are `aria-hidden`.
- Respect `prefers-reduced-motion` completely (§6).
- Respect `prefers-reduced-transparency`: swap `backdrop-filter` panels to solid `--bg-panel`.
- Semantic HTML: `<header> <nav> <main> <section> <footer>`, plus `<data> <samp> <kbd> <output> <dl>` for telemetry.
- Contrast AA minimum on every text/background pair (audit `--muted` and `--peach` on their backgrounds).

---

## 11. ANTI-SLOP GUARDRAILS (hard fails)

- ❌ A second accent hue. ❌ Purple/blue "AI glow." ❌ Neutral (non-tinted) drop shadows. ❌ Any radius other than 0 except the `999px` pill token — no 20px cards, no rounded panels. ❌ A "soft section" that flips to a sentence-case display font + rounded cards (the mode-flip; warm the light, not the structure). ❌ Ember dosed above ~15% — if it stops feeling like a machine, pull warmth back. ❌ Cell hover that lifts/translates inside a 1px grid (warm in place instead). ❌ More than one marquee. ❌ An eyebrow above every section. ❌ Two CTAs with different labels for the same intent. ❌ Em-dashes. ❌ Div-based fake product screenshots (the HUD/terminal are stylized telemetry, which is fine; a fake "dashboard app screenshot" is not). ❌ A light-mode section in the dark page. ❌ Green used anywhere but the one terminal line. ❌ Off-scale spacing. ❌ Horizontal body scroll (clip decorative blooms with `overflow:hidden` on their section). ❌ Motion claimed but not shipped.

---

## APPENDIX — copy-paste token block

```css
:root{
  /* substrate */
  --bg:#0A0A0A; --bg-panel:#0E0D0C; --bg-well:#070605; --bg-deep:#050403;
  /* foreground */
  --fg:#F3EEE7; --fg-dim:#B6AFA5; --muted:#7C766C; --faint:#4A453E;
  /* accent — hazard orange (the only accent) */
  --o:#FF5A1F; --o-bright:#FF7E33; --o-ember:#C7370B;
  /* soft human-layer warmth (same hue family) */
  --amber:#FFB25A; --peach:#FFCE9E; --cream:#FFF1E2; --blush:#FF8A4C;
  /* the single green — terminal success only */
  --term-green:#4AF626;
  /* structure */
  --line:rgba(243,238,231,0.11); --line-2:rgba(243,238,231,0.06); --edge:#1B1916;
  /* motion */
  --ease:cubic-bezier(.22,.61,.36,1); --ease-out:cubic-bezier(.16,1,.3,1);
  /* type */
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace;
  --grot:"Space Grotesk",system-ui,-apple-system,sans-serif;
  --black:"Archivo Black","Space Grotesk",system-ui,sans-serif;
  --serif:"Playfair Display",Georgia,serif;
  /* warm hairline accent (hover/edges) */
  --line-ember:rgba(255,120,60,0.20);
  /* spacing / shape */
  --pad:clamp(20px,5vw,88px);
  --r-pill:999px; /* everything else is radius:0 */
}
```

Fonts (one link):
```html
<link href="https://fonts.googleapis.com/css2?family=Archivo+Black&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Playfair+Display:ital,wght@1,600&display=swap" rel="stylesheet">
```

---

**Extend rule (include verbatim in output):** *This is ONE machine, warmly lit — not two modes.
Every section is square, monospace, uppercase, black-on-black with 1px hairlines, crosshairs,
and one hazard orange. EMBER is the minority warmth threaded through it (~15%): ember blooms
behind grids, pastel `999px` pill tokens, warm amber/peach/cream text, ember-tinted glows, and
one inline halftone serif word. Warm the light, never the structure — no rounded cards, no
soft-font section, no mode-flip. One accent hue, one primary button per view, one marquee, one
green (terminal only), one halftone word per page.*
