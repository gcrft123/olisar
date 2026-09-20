"""Record a proof pass over the better-ui fixes against the USAGE_MOCK console.

Drives the real app in Chrome and captures video. Each scene holds long enough for the
motion to read at playback speed, and the caption strip names what is on screen — a 140ms
exit is otherwise impossible to see going past.

The small controls (toggle, copy glyph, segmented chip) are magnified for their scene:
at 1280x800 a 15px cross-fade and a 1px padding change are real but invisible on camera.
Magnification is a `zoom` on an ancestor, so the control still lays out and animates
exactly as it ships — nothing is re-styled for the shot.

    python3 record.py            # writes out/proof.webm
"""

import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://localhost:5199/"
HERE = Path(__file__).parent
OUT = HERE / "out"
W, H = 1280, 800

# The caption sits at the TOP: the bottom of this app is busy — save dock, test-chat FAB,
# toast stack, sidebar footer — and those are several of the things being demonstrated.
CAPTION_JS = """
([n, title, sub]) => {
  let el = document.getElementById('__proof_caption')
  if (!el) {
    el = document.createElement('div')
    el.id = '__proof_caption'
    el.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:2147483647',
      'padding:13px 22px', 'background:rgba(2,2,3,.94)',
      'border-bottom:1px solid #26262a', 'color:#ededee',
      'font:600 15px/1.4 "IBM Plex Sans",system-ui,sans-serif',
      'letter-spacing:-0.01em', 'pointer-events:none',
      'display:flex', 'align-items:baseline', 'gap:12px',
      'backdrop-filter:blur(6px)',
    ].join(';')
    document.documentElement.appendChild(el)
  }
  el.innerHTML =
    '<span style="color:#5b9cf6;font-family:JetBrains Mono,monospace;font-size:12px">' + n + '</span>' +
    '<span>' + title + '</span>' +
    '<span style="margin-left:auto;color:#9d9da7;font-weight:400;font-size:13px;' +
    'font-family:JetBrains Mono,monospace">' + sub + '</span>'
}
"""

# Magnify an element's neighbourhood without restyling the element itself.
ZOOM_JS = """
([sel, factor, origin]) => {
  const el = document.querySelector(sel)
  if (!el) return false
  const host = el.closest('.card, .codeblock, .field, .useg, .toggle') || el.parentElement
  host.dataset.proofZoom = '1'
  host.style.zoom = factor
  host.style.transformOrigin = origin
  el.scrollIntoView({block: 'center'})
  return true
}
"""
UNZOOM_JS = """
() => document.querySelectorAll('[data-proof-zoom]').forEach(h => {
  h.style.zoom = ''; delete h.dataset.proofZoom
})
"""


class Take:
    def __init__(self, page):
        self.page = page
        self.n = 0

    def say(self, title, sub="", hold=1.6):
        self.n += 1
        self.page.evaluate(CAPTION_JS, [f"{self.n:02d}", title, sub])
        self.wait(hold)

    def note(self, sub, hold=1.9):
        """Re-label the current scene without advancing the counter."""
        self.page.evaluate(CAPTION_JS, [f"{self.n:02d}", self._title, sub])
        self.wait(hold)

    def wait(self, s):
        self.page.wait_for_timeout(int(s * 1000))

    def goto(self, hash_, settle=1.0):
        self.page.goto(URL + hash_, wait_until="load")
        self.wait(settle)

    def zoom(self, sel, factor=2.2, origin="center"):
        return self.page.evaluate(ZOOM_JS, [sel, factor, origin])

    def unzoom(self):
        self.page.evaluate(UNZOOM_JS)

    def press(self, locator, down=0.34, up=0.5):
        """A real mouse press, so :active actually fires."""
        box = locator.bounding_box()
        if not box:
            return
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        self.wait(0.3)
        self.page.mouse.down()
        self.wait(down)
        self.page.mouse.up()
        self.wait(up)


def scene_toggle(t, page):
    t.goto("#/behavior")
    t._title = "Toggle knob"
    t.say("Toggle knob", "transform: translateX(16px)   was: transition: left", 1.5)

    t.zoom(".toggle", 2.4)
    t.wait(0.7)
    tog = page.locator(".toggle").first
    for _ in range(3):
        tog.click()
        t.wait(0.75)
    t.unzoom()
    t.wait(0.4)

    prop = page.evaluate("""(() => {
      const k = document.querySelector('.toggle .knob'), cs = getComputedStyle(k)
      return cs.transitionProperty + ' / ' + cs.transitionTimingFunction.replace('cubic-bezier', 'cb')
           + ' / left: ' + cs.left
    })()""")
    t.note(prop, 2.2)


def scene_press(t, page):
    t.goto("#/behavior", 0.8)
    t._title = "Press feedback"
    t.say("Press feedback", "scale(0.96)   was: scale(0.97)", 1.4)
    page.locator("textarea, input[type=text]").first.click()
    page.keyboard.type(" x")
    t.wait(1.1)
    save = page.locator(".savedock button:has-text('Save')").first
    if save.count():
        t.zoom(".savedock-inner", 1.5, "center bottom")
        t.wait(0.6)
        for _ in range(3):
            t.press(save)
        t.unzoom()
    t.wait(0.5)


def scene_savedock(t, page):
    t._title = "Save dock"
    geo = page.evaluate("""(() => {
      const d = document.querySelector('.savedock-inner')
      if (!d) return 'dock not up'
      const b = d.querySelector('button'), cs = getComputedStyle(d)
      return cs.padding + ' pad  ' + getComputedStyle(b).borderRadius + ' btn  '
           + cs.borderRadius + ' dock  ' + Math.round(d.getBoundingClientRect().height / 1.1) + 'px tall'
    })()""")
    t.say("Save dock", geo + "   (12 + 4 = 16, concentric)", 2.4)


def scene_segmented(t, page):
    t.goto("#/usage", 1.4)
    t._title = "Segmented control"
    seg = page.locator(".useg").first
    if not seg.count():
        return
    geo = page.evaluate("""(() => {
      const s = document.querySelector('.useg'), b = s.querySelector('button')
      return getComputedStyle(s).padding + ' pad + ' + getComputedStyle(b).borderRadius
           + ' chip = ' + getComputedStyle(s).borderRadius + ' shell'
    })()""")
    t.say("Segmented control", geo + "   was: 3px, 8+3 != 12", 1.8)
    t.zoom(".useg", 2.6)
    t.wait(0.7)
    for label in ["7 days", "Forever", "30 days"]:
        b = seg.locator(f"button:has-text('{label}')")
        if b.count():
            b.first.click()
            t.wait(0.85)
    t.unzoom()
    t.wait(0.5)


def scene_copy(t, page):
    t.goto("#/docs/ext-build", 1.6)
    t._title = "Copy to copied"
    cb = page.locator(".cb-copy").first
    if not cb.count():
        return
    t.say("Copy to copied", "both glyphs mounted; scale .25>1, opacity 0>1, blur 4px>0", 1.6)
    t.zoom(".cb-copy", 3.4, "center")
    t.wait(0.8)
    for _ in range(3):
        cb.click()
        t.wait(2.1)
    t.unzoom()
    t.wait(0.5)


def scene_overlay(t, page):
    t.goto("#/persona", 1.0)
    t._title = "Overlay exit"
    t.say("Overlay exit", "140ms fade + 6px drop   was: vanished on the closing frame", 1.7)
    t.exit_window_start = time.monotonic() - t.t0
    search = page.locator(".sidebar button:has-text('Search')").first
    for _ in range(2):
        search.click()
        t.wait(1.0)
        page.keyboard.press("Escape")
        t.wait(1.3)

    # No CSS slow-motion here: playExit removes the ghost on a 140ms JS timer, so stretching
    # animation-duration just truncates it. The exit is proved frame-by-frame instead —
    # see filmstrip.py, which pulls the consecutive recorded frames out of this window.
    t.note("watch the backdrop — it fades and drops rather than cutting", 1.0)
    for _ in range(2):
        search.click()
        t.wait(0.9)
        page.keyboard.press("Escape")
        t.wait(1.4)


def scene_stack(t, page):
    t.goto("#/persona", 1.2)
    t._title = "FAB + toast stack"
    t.say("FAB + toast stack", "FAB lifts on translate, stack glides on transform", 1.6)
    ta = page.locator("textarea").first
    ta.scroll_into_view_if_needed()
    ta.click()
    page.keyboard.type(" proof", delay=55)
    t.wait(1.9)

    # Press the FAB while the dock is up. This is the state where the press scale used to
    # be eaten: both effects wanted `transform`, and the dock rule outranked :active.
    fab = page.locator(".testchat-fab").first
    if fab.count():
        t.note("pressing the FAB while lifted: scale + lift now coexist", 1.2)
        for _ in range(2):
            t.press(fab, down=0.4, up=0.7)
    t.wait(0.6)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.webm"):
        old.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=2,
            record_video_dir=str(OUT),
            record_video_size={"width": W, "height": H},
            reduced_motion="no-preference",
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = ctx.new_page()
        t = Take(page)
        t.t0 = time.monotonic()

        scene_toggle(t, page)
        scene_press(t, page)
        scene_savedock(t, page)
        scene_segmented(t, page)
        scene_copy(t, page)
        scene_overlay(t, page)
        scene_stack(t, page)

        t._title = "All nine"
        t.say("All nine fixed", "design-lint + tsc + vite build green", 2.4)

        exit_at = getattr(t, "exit_window_start", None)
        raw = page.video.path()
        ctx.close()
        browser.close()

    src = Path(raw)
    dst = OUT / "proof.webm"
    if src.exists() and src != dst:
        src.rename(dst)
    if exit_at is not None:
        (OUT / "exit_window.txt").write_text(f"{exit_at:.2f}\n")
    print(dst)


if __name__ == "__main__":
    sys.exit(main())
