"""Olisar's release versions: how they're spelled, and which one is newer.

From 2.0 on, a stable release has two numbers (``2.0``, ``2.1``) and a beta carries the
number of the stable release it leads up to (``2.0.beta-1``, ``2.0.beta-2``, then
``2.0``). Releases before 2.0 had three numbers (``1.5.0``); those still parse and still
sort below everything after them.

npm and electron-builder refuse anything that isn't semver, so the three version files
carry a translation of the same version: ``2.0`` is ``2.0.0`` there and ``2.0.beta-1`` is
``2.0.0-beta.1``. That is also what the running app reports about itself
(``app.getVersion()`` reads ``desktop/package.json``). Tags, release titles and anything a
person reads use :meth:`Version.display`.

Stdlib-only, so ``scripts/check_release_version.py`` can import it in CI without the
project's dependencies installed. ``desktop/updater.js`` and ``web/src/version.ts`` carry
small ports of :func:`parse` and the ordering; keep the three in step.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Releases from this major on are spelled without a third number.
TWO_PART_FROM = 2

# Every spelling a version reaches us in: the tag ("v2.0.beta-1"), the version files
# ("2.0.0-beta.1"), an OCI image label (the tag), and PEP 440's normal form ("2.0.0b1"),
# which is what uv writes into uv.lock.
_VERSION = re.compile(
    r"""^v?
        (\d+)\.(\d+)(?:\.(\d+))?
        (?:[.-]?(?:beta|b)[.-]?(\d+))?
        $""",
    re.IGNORECASE | re.VERBOSE,
)


class Version(NamedTuple):
    major: int
    minor: int
    patch: int
    beta: int | None  # None for a stable release

    @property
    def is_beta(self) -> bool:
        return self.beta is not None

    def sort_key(self) -> tuple[int, ...]:
        """A beta sorts below the stable release it leads up to: 2.0.beta-9 < 2.0."""
        if self.beta is None:
            return (self.major, self.minor, self.patch, 1, 0)
        return (self.major, self.minor, self.patch, 0, self.beta)

    def display(self) -> str:
        """How a person reads it: ``2.0``, ``2.0.beta-1``, or ``1.5.0`` from before 2.0."""
        base = f"{self.major}.{self.minor}"
        if self.patch or self.major < TWO_PART_FROM:
            base += f".{self.patch}"
        return base if self.beta is None else f"{base}.beta-{self.beta}"

    def tag(self) -> str:
        return "v" + self.display()

    def package(self) -> str:
        """The semver spelling the version files carry: ``2.0.0``, ``2.0.0-beta.1``."""
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.beta is None else f"{base}-beta.{self.beta}"


def parse(v: str | None) -> Version | None:
    m = _VERSION.match((v or "").strip())
    if not m:
        return None
    major, minor, patch, beta = m.groups()
    return Version(int(major), int(minor), int(patch or 0), int(beta) if beta else None)


def _key(v: str | None) -> tuple[int, ...]:
    """Ordering for any string. One that doesn't parse (a ``main`` image label, say) keeps
    the old digits-only reading, so it still compares instead of raising."""
    parsed = parse(v)
    if parsed:
        return parsed.sort_key()
    nums = [int(n) for n in re.findall(r"\d+", v or "")][:3]
    return (*nums, *[0] * (3 - len(nums)), 1, 0)


def is_newer(remote: str | None, local: str | None) -> bool:
    return _key(remote) > _key(local)


def same_version(a: str | None, b: str | None) -> bool:
    """Whether two strings name the same release, whatever spelling each arrived in:
    "v2.0.beta-1" from a tag and "2.0.0-beta.1" from the app are the same build."""
    return _key(a) == _key(b)


def is_beta(v: str | None) -> bool:
    parsed = parse(v)
    return bool(parsed and parsed.is_beta)


def display(v: str | None) -> str:
    """``v`` as a person reads it, without a leading "v". Passed through (minus the "v")
    when it doesn't parse, so an odd label still shows up as itself."""
    parsed = parse(v)
    return parsed.display() if parsed else (v or "").strip().lstrip("vV")
