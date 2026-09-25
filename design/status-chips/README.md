# Status chips

A standalone recreation of five status chips (Pending, Submitted, Success, Failed, Expired) in the console's type and icons. Nothing here is wired into `web/` or `DESIGN.md`.

| File | What it is |
| --- | --- |
| `status-chips.html` | The five chips. Every size is in `em`, so `font-size` scales the whole chip (13px gives a 24px chip). |
| `circle-icons.html` | Every Solar `-linear` icon whose outline is a circle at the chip icon's size and center, shown in a chip. |

## How the chips are built

Colors were sampled from the reference image. The border is a vertical gradient from a light top edge to a darker bottom edge, and the disc inside each icon's ring is the ink at 20% over the fill.

Type is IBM Plex Sans at 425 with `-0.015em` tracking; the reference was set in a narrower face, so the chips run about 3% wider. Icons are Solar `-linear` at the 1.5 stroke `DESIGN.md` asks for, which is thinner than the reference's rings.

## How the icon list was found

All 1,246 Solar icons were rendered and sampled at 360 points on both the inner and outer edge of an `r=10` stroke centered on the 24-unit box, the same circle the tinted disc fills. That test rejects polygons, off-center circles, and square frames. The pages sort the survivors into three groups:

- Complete circle (98): the disc fits exactly.
- Circle with a break or attachment (37): a small gap, dashes, or a tail or arrow crossing the ring.
- Open circle (11): a quarter or more is missing, so the disc's edge shows in the gap.

`history`, `alarm*` and `stopwatch*` look round but draw a smaller or lower circle, `danger` and `forbidden` are octagons, and in the moons the disc fills the crescent's bite, so none of them are listed.
