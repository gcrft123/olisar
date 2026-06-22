#!/usr/bin/env python3
"""Assert the release version is consistent across every file that carries one.

The desktop app (electron-builder) reads ``desktop/package.json``; the Python project
reads ``pyproject.toml``; the dashboard has ``web/package.json``. If these drift from the
git tag, the release silently misfires — e.g. electron-builder builds/publishes under the
wrong version and the tagged GitHub release ends up empty (this happened on v0.4.0).

Run in CI on a ``v*`` tag (``check_release_version.py <tag>``, or ``TAG`` env), which
asserts every file matches the tag. Run with no argument locally to just confirm the
files agree with each other before you tag.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        sys.exit("could not find a version in pyproject.toml")
    return m.group(1)


def _json_version(rel: str) -> str:
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    v = data.get("version")
    if not v:
        sys.exit(f"could not find a version in {rel}")
    return v


def main() -> None:
    raw = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TAG", "")).strip()
    tag = re.sub(r"^refs/tags/", "", raw).lstrip("v")

    files = {
        "pyproject.toml": _pyproject_version(),
        "desktop/package.json": _json_version("desktop/package.json"),
        "web/package.json": _json_version("web/package.json"),
    }

    print("version sources:")
    for name, v in files.items():
        print(f"  {name:24} {v}")
    if tag:
        print(f"  {'git tag':24} {tag}")

    expected = set(files.values())
    if tag:
        expected.add(tag)

    if len(expected) != 1:
        target = f" the tag ({tag})" if tag else " each other"
        sys.exit(
            f"\nERROR: versions disagree {sorted(expected)} — bump them all to match{target}."
        )
    print(f"\nOK: all versions match {next(iter(expected))}")


if __name__ == "__main__":
    main()
