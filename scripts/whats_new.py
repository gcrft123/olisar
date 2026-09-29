#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=12.3.0"]
# ///
"""Add and check the desktop app's What's new cards.

The first time the desktop app opens on a new stable release, it shows a card on what came
with it (``web/src/whatsnew.tsx``). Each card is two files in ``web/src/whats-new/``, named for
the release as people read it: ``2.1.webp``, the banner, and ``2.1.json``, its alt text and
points. The console picks up whatever is in the folder, so adding one touches no code::

    uv run scripts/whats_new.py add 2.1 --poster poster.png --alt "…" --point "…" --point "…"
    uv run scripts/whats_new.py add 2.1        # asks for whatever's left out
    python3 scripts/whats_new.py check v2.1    # what the release workflow runs
    python3 scripts/whats_new.py check         # every card in the folder

``add`` takes a 16:9 poster at 1280×720 or larger (the 3840×2160 renders are what it's for),
brings it down to a 1280×720 WebP, and writes the JSON. Anything left out keeps what the card
already has, so running it again with only ``--point`` rewrites the points and keeps the
banner. It needs Pillow, which ``uv run`` brings from the block at the top of this file.

``check`` needs nothing but Python. Given a stable tag (as an argument, or ``TAG`` in the
environment, as in CI) it fails unless that release's card is there and meets the rules below;
a beta tag passes, since a beta gets the "Updated to" toast instead. With no tag it checks
every card in the folder. The release workflow runs it before it opens the draft release, so a
stable tag without a card stops there with nothing published.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from olisar.versioning import parse  # noqa: E402 — needs ROOT on the path

CARDS = ROOT / "web" / "src" / "whats-new"

BANNER = (1280, 720)   # 2x the card's 400px at 110%, with room for 125%
POINTS = (2, 3)
POINT_MAX = 80         # two lines at the card's width, at most
KEYS = {"alt", "points"}


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:  # a folder outside the repo, as the tests use
        return str(p)


def webp_size(head: bytes) -> tuple[int, int] | None:
    """Width and height from a WebP file's first 30 bytes, or None if it isn't one."""
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    chunk = head[12:16]
    if chunk == b"VP8 ":   # lossy: a 3-byte frame tag, the start code, then 14-bit sizes
        if head[23:26] != b"\x9d\x01\x2a":
            return None
        return int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF
    if chunk == b"VP8L":   # lossless: a signature byte, then both sizes less one, 14 bits each
        if head[20] != 0x2F:
            return None
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":   # extended: the canvas size less one, 24 bits each
        return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
    return None


def card_problems(card: object) -> list[str]:
    """What's wrong with a card's JSON, as sentences. Empty when nothing is."""
    if not isinstance(card, dict):
        return ["the card is a JSON object with alt and points"]
    out = []
    extra = sorted(set(card) - KEYS)
    if extra:
        out.append(f"has keys the card doesn't read: {', '.join(extra)} (it reads alt and points)")
    alt = card.get("alt")
    if not isinstance(alt, str) or not alt.strip():
        out.append("needs alt text: what the banner shows, including any words set in it")
    points = card.get("points")
    if not isinstance(points, list) or not all(isinstance(p, str) for p in points):
        out.append(f"needs points: a list of {POINTS[0]} or {POINTS[1]} lines")
        return out
    if not POINTS[0] <= len(points) <= POINTS[1]:
        out.append(f"has {len(points)} point{'' if len(points) == 1 else 's'}; the card takes {POINTS[0]} or {POINTS[1]}")
    for i, p in enumerate(points, 1):
        if not p.strip():
            out.append(f"point {i} is empty")
        elif len(p) > POINT_MAX:
            out.append(f"point {i} is {len(p)} characters; keep it to {POINT_MAX}, two lines on the card")
    return out


def problems(version: str) -> list[str]:
    """What's wrong with a release's card on disk, as sentences. Empty when nothing is."""
    out = []
    v = parse(version)
    if not v or v.display() != version:
        want = f" (call it {v.display()})" if v else ""
        return [f"{version} isn't a release as people read it{want}"]
    if v.is_beta:
        return [f"{version} is a beta, and betas get no card"]

    j, w = CARDS / f"{version}.json", CARDS / f"{version}.webp"
    if not j.exists():
        out.append(f"{rel(j)} is missing")
    else:
        try:
            card = json.loads(j.read_text(encoding="utf-8"))
        except ValueError as e:
            out.append(f"{rel(j)} isn't valid JSON ({e})")
        else:
            out += [f"{rel(j)} {p}" for p in card_problems(card)]
    if not w.exists():
        out.append(f"{rel(w)} is missing")
    else:
        with w.open("rb") as fh:
            size = webp_size(fh.read(30))
        if size is None:
            out.append(f"{rel(w)} isn't a WebP image")
        elif size != BANNER:
            out.append(f"{rel(w)} is {size[0]}×{size[1]}; the banner is {BANNER[0]}×{BANNER[1]}")
    return out


def check(tag: str) -> None:
    if tag:
        v = parse(tag)
        if not v:
            sys.exit(f"ERROR: {tag} isn't a release tag.")
        if v.is_beta:
            print(f"{tag} is a beta. Betas get the update toast, not a What's new card.")
            return
        versions = [v.display()]
    else:
        names = {p.stem for p in CARDS.glob("*.json")} | {p.stem for p in CARDS.glob("*.webp")}
        versions = sorted(names, key=lambda n: parse(n).sort_key() if parse(n) else (0,))
        if not versions:
            print(f"No cards in {rel(CARDS)} yet.")
            return

    failed = []
    for version in versions:
        issues = problems(version)
        print(f"{'✗' if issues else '✓'} {version}")
        for issue in issues:
            print(f"    {issue}")
        if issues:
            failed.append(version)
    if not failed:
        return
    if tag:
        v = versions[0]
        sys.exit(
            f"\nERROR: {tag} is a stable release, and its What's new card isn't ready. Nothing was"
            f" published. Add it with `uv run scripts/whats_new.py add {v}`, commit, and move the"
            " tag to that commit."
        )
    sys.exit(f"\nERROR: {'this card needs' if len(failed) == 1 else 'these cards need'} fixing: {', '.join(failed)}.")


def ask(prompt: str, flag: str) -> str:
    if not sys.stdin.isatty():
        sys.exit(f"ERROR: {flag} is missing, and there's no terminal to ask in.")
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nStopped. Nothing was written.")


def ask_points() -> list[str]:
    if not sys.stdin.isatty():
        sys.exit("ERROR: --point is missing, and there's no terminal to ask in.")
    print(f"{POINTS[0]} or {POINTS[1]} points: the release's headline changes, a few words each.")
    points = []
    for i in range(1, POINTS[1] + 1):
        optional = i > POINTS[0]
        p = ask(f"  Point {i}{' (Enter to skip)' if optional else ''}: ", "--point")
        if not p and optional:
            break
        points.append(p)
    return points


def write_banner(src: Path, dst: Path) -> None:
    from PIL import Image  # only `add` needs it; `check` runs on a bare Python in CI

    if not src.is_file():
        sys.exit(f"ERROR: {src} doesn't exist.")
    with Image.open(src) as im:
        w, h = im.size
        if abs(w / h - 16 / 9) > 0.01:
            sys.exit(f"ERROR: {src.name} is {w}×{h}. The banner is 16:9, e.g. 3840×2160.")
        if w < BANNER[0]:
            sys.exit(f"ERROR: {src.name} is {w}×{h}. It needs to be {BANNER[0]}×{BANNER[1]} or larger, or the banner comes out soft.")
        # Anything see-through lands on black, the poster's own ground.
        rgba = im.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (0, 0, 0))
        flat.paste(rgba, mask=rgba.getchannel("A"))
    flat.resize(BANNER, Image.Resampling.LANCZOS).save(dst, "WEBP", quality=92, method=6)


def add(args: argparse.Namespace) -> None:
    v = parse(args.version)
    if not v:
        sys.exit(f"ERROR: {args.version} isn't a release. Stable releases are 2.0, 2.1.")
    if v.is_beta:
        sys.exit(f"ERROR: {v.display()} is a beta. Betas get the update toast, not a card.")
    version = v.display()
    j, w = CARDS / f"{version}.json", CARDS / f"{version}.webp"
    CARDS.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if j.exists():
        try:
            existing = json.loads(j.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

    poster = args.poster
    if not poster and not w.exists():
        poster = ask("Poster (16:9, 1280×720 or larger): ", "--poster")
    alt = args.alt or existing.get("alt") or ask("Alt text (what the banner shows, with any words set in it): ", "--alt")
    points = args.point or existing.get("points") or ask_points()

    card = {"alt": alt.strip(), "points": [p.strip() for p in points]}
    issues = card_problems(card)
    if issues:
        sys.exit("ERROR: the card isn't right yet:\n" + "\n".join(f"    {i}" for i in issues))

    if poster:
        # A file dragged into a terminal arrives quoted, or with its spaces escaped.
        poster = poster.strip().strip("'\"").replace("\\ ", " ")
        write_banner(Path(poster).expanduser().resolve(), w)
        print(f"wrote {rel(w)} ({w.stat().st_size // 1024} KB)")
    j.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {rel(j)}")

    issues = problems(version)
    if issues:
        sys.exit("ERROR: the card isn't right yet:\n" + "\n".join(f"    {i}" for i in issues))
    print(
        f"\nThe v{version} card is ready. To see it, run `USAGE_MOCK=1 npx vite` in web/ and open"
        f" /?update=stable&whatsnew={version}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Add and check the desktop app's What's new cards.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="make or update a release's card")
    a.add_argument("version", help="the stable release, e.g. 2.1")
    a.add_argument("--poster", help="a 16:9 image, 1280×720 or larger")
    a.add_argument("--alt", help="what the banner shows, including any words set in it")
    a.add_argument("--point", action="append", help=f"a point; give {POINTS[0]} or {POINTS[1]}")
    c = sub.add_parser("check", help="check one release's card, or every card")
    c.add_argument("tag", nargs="?", help="a release tag, e.g. v2.1 (default: TAG, else every card)")
    args = ap.parse_args()

    if args.cmd == "add":
        add(args)
    else:
        raw = (args.tag or os.environ.get("TAG", "")).strip()
        check(re.sub(r"^refs/tags/", "", raw))


if __name__ == "__main__":
    main()
