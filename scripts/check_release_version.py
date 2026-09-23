#!/usr/bin/env python3
"""Assert the release version is consistent across every file that carries one.

The desktop app (electron-builder) reads ``desktop/package.json``; the Python project
reads ``pyproject.toml``; the dashboard has ``web/package.json``. If these drift from the
git tag, the release silently misfires — e.g. electron-builder builds/publishes under the
wrong version and the tagged GitHub release ends up empty (this happened on v0.4.0).

Run in CI on a ``v*`` tag (``check_release_version.py <tag>``, or ``TAG`` env), which
asserts every file matches the tag. Run with no argument locally to just confirm the
files agree with each other before you tag.

Tags are spelled the way people read them (``v2.0``, ``v2.0.beta-1``), and the files carry
the semver spelling npm and electron-builder insist on (``2.0.0``, ``2.0.0-beta.1``); see
``olisar/versioning.py``. So "matches" means "is the same release": the check translates,
names the exact string the files need when they're off, and refuses a tag that isn't in
its canonical form (``v2.0.0``, ``v2.0-beta.1``), because the updater, the server image and
the release notes all key on that spelling.

In CI it also writes ``prerelease=true|false`` to ``$GITHUB_OUTPUT``, which is how a beta
gets published as a GitHub pre-release: stable installs never see one.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from olisar.versioning import TWO_PART_FROM, parse  # noqa: E402 — needs ROOT on the path


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
    tag = re.sub(r"^refs/tags/", "", raw)

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

    if len(set(files.values())) != 1:
        sys.exit(f"\nERROR: versions disagree {sorted(set(files.values()))} — bump them all to match each other.")
    version = next(iter(files.values()))

    if tag:
        wanted = parse(tag)
        if not wanted or wanted.tag() != tag:
            sys.exit(f"\nERROR: {tag} isn't a release tag. Stable releases are v2.0, v2.1; betas are v2.0.beta-1.")
        if wanted.package() != version:
            sys.exit(f"\nERROR: the files are at {version}, but {tag} needs them at {wanted.package()}.")

    parsed = parse(version)
    if not parsed or parsed.package() != version:
        sys.exit(f"\nERROR: {version} isn't a release version. The files carry 2.0.0 for v2.0 and 2.0.0-beta.1 for v2.0.beta-1.")
    if parsed.major >= TWO_PART_FROM and parsed.patch:
        sys.exit(f"\nERROR: {version} has a third number. From {TWO_PART_FROM}.0 on, releases have two: the next one after {parsed.major}.{parsed.minor} is {parsed.major}.{parsed.minor + 1}.")

    kind = "beta" if parsed.is_beta else "stable"
    print(f"\nOK: every file is at {version}, released as {parsed.tag()} ({kind})")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"prerelease={'true' if parsed.is_beta else 'false'}\n")


if __name__ == "__main__":
    main()
