"""One measuring command at a time, enforced by a lockfile.

There is exactly one Olisar instance and one guild, and every measuring command owns both
of them: applying a variant rewrites the shared prompt-override file and can restart the
process, and a scenario recreates ``#arena-general``. Two such commands running at once do
not queue or interleave cleanly — they silently reconfigure each other, and every run
records a variant name that is not reliably the variant that produced it.

This is not hypothetical. An ``ab search-questions-kept search-questions-dropped`` was
still running when an ``ab baseline empty-search-offer-action`` was launched against the
same instance, because the first was invoked as ``python -m arena`` and the check for a
running one looked for ``arena.cli``. Nothing failed loudly. Both kept producing runs, and
the only reason it was caught was a timestamp that did not fit.

Cheap to prevent, expensive to detect after the fact, and the failure is invisible in the
output — so the lock refuses rather than waits. Two overnight rounds queueing up behind
each other would be its own kind of wrong.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
from collections.abc import Iterator
from pathlib import Path

from arena.config import ArenaConfig

LOCK_NAME = "measuring.lock"


class Busy(RuntimeError):
    """Another measuring command holds the lock. Carries an actionable message."""


def _alive(pid: int) -> bool:
    """Whether a pid is still running. Signal 0 checks without delivering anything."""
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # EPERM means it exists and belongs to someone else — still alive.
        return exc.errno == errno.EPERM
    return True


def _read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def held(cfg: ArenaConfig, command: str) -> Iterator[Path]:
    """Hold the measuring lock for the duration, or raise :class:`Busy`.

    A lock whose owner has died is taken over rather than honoured — a killed run must not
    wedge the arena until someone notices a stale file.
    """
    path = cfg.data_dir / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _read(path)
    owner = int(existing.get("pid") or 0)
    if owner and owner != os.getpid() and _alive(owner):
        raise Busy(
            f"another measuring command already owns this arena: "
            f"{existing.get('command', '?')} (pid {owner}).\n"
            f"They share one instance and one guild, so running both would have each "
            f"reconfiguring the other's runs.\n"
            f"Wait for it, or stop it with: kill {owner}"
        )
    if owner and not _alive(owner):
        # Normal after a kill or a crash; worth saying so the takeover isn't mysterious.
        print(f"taking over the arena lock from dead pid {owner} ({existing.get('command', '?')})")

    path.write_text(json.dumps({"pid": os.getpid(), "command": command}, indent=2), encoding="utf-8")
    try:
        yield path
    finally:
        # Only clear our own: a takeover means someone else's entry is legitimately here.
        if _read(path).get("pid") == os.getpid():
            path.unlink(missing_ok=True)
