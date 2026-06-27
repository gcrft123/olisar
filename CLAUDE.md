# Olisar — repo guide

## Frontend / UI / design work

The admin console (`web/`) follows a canonical design system documented in **[web/DESIGN.md](web/DESIGN.md)**.

**Before building or restyling any console UI, read `web/DESIGN.md`** and conform to its tokens and component recipes — buttons, inputs, toggle, field, card, badge, tag, callout, toast, **tooltip**, **code-preview box**, tabs, data table, overlays (dialog/modal/savedock). The live token source of truth is `web/src/index.css` and must stay in sync with DESIGN.md. When you add a new UI component, **add its recipe to DESIGN.md**.

Essentials:
- Dark-only, near-black palette; structure comes from hairline borders + inset wells, not fill contrast.
- Reserve the accent for selection / links / focus / active state; exactly one bright `primary` (bright-neutral, not the accent) button per view.
- Icons: the Solar set via the `web/src/icons.tsx` registry only — no emoji or unicode-as-icon. Modal/menu closes use the simple line `<CloseX>`.
- Controls share a 34px height (28px `.sm`); every icon-only button carries a `data-tip`/`title` (styled via the delegated tooltip host in `web/src/overlays.tsx`) plus an `aria-label`.
- Sentence case everywhere except the small uppercase tracked eyebrow/nav labels.
- **All text buttons share the 34px height** (only `icon-btn` may be `sm`/28px). Each button uses exactly one variant: `primary` / secondary(base) / `ghost` / `danger` / `caution` — never mix (no `ghost danger`), never `warn`.

## Design linter (enforced on build)

`npm run build` runs `web/scripts/design-lint.mjs` first and **fails the build** on:
- **colours** — raw hex/rgb outside `:root` or in inline styles (use `var(--…)`; brand/syntax/neutral allowlisted)
- **buttons** — text buttons with `sm`, mixed variants, or `warn`
- **corner radii** — raw `border-radius` (use `var(--radius*)`; only `0` / `50%` / `≤6px` chips allowed raw)
- **spacing** — `padding`/`margin`/`gap` off the established px scale (CSS + inline styles)
- **animation easing** — raw `cubic-bezier()` (use `var(--ease-out)`)
- **native dialogs** — `alert/confirm/prompt` (use the overlays)

Keep it green; add new rules / scale values there as the system grows.
