"""Snapshot the database before a new build migrates it.

Schema evolution is forward-only: every boot runs ``create_all`` plus an ``ADD COLUMN``
sweep, and one case genuinely destroys data — ``_drop_repk_tables`` drops a table whose
primary key changed (``extension_state`` did exactly that), silently resetting which
extensions are enabled. There is no down-migration and no schema version stamp, so an
operator who updates into a bad release has nothing to go back to.

So: when the running build's version differs from the one that last migrated this
database, copy it first. The marker lives beside the DB file, which makes it per-profile
for free. Uses SQLite's own backup API rather than a file copy — that's what makes the
snapshot consistent when WAL mode has uncommitted pages in the -wal sidecar.

Best-effort throughout: a failed backup logs and lets the boot continue. Refusing to start
because we couldn't take a precaution would be worse than the risk it guards against.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger("olisar.dbbackup")

KEEP = 2  # how many pre-upgrade snapshots to retain per profile
_PREFIX = ".pre-"


def _marker(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".version")


def _safe(version: str) -> str:
    """Version strings reach filenames — keep them to something a path can hold."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", version or "unknown") or "unknown"


def _snapshot(src: Path, dst: Path) -> None:
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _prune(db_path: Path) -> None:
    """Keep only the KEEP newest snapshots so a long-lived install doesn't accumulate a
    copy of the database per release."""
    snaps = sorted(
        db_path.parent.glob(f"{db_path.name}{_PREFIX}*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in snaps[KEEP:]:
        try:
            stale.unlink()
        except OSError as exc:
            log.warning("couldn't remove old snapshot %s: %s", stale, exc)


def before_migration(db_path: str | Path, version: str) -> Path | None:
    """Snapshot ``db_path`` if ``version`` differs from the build that last migrated it.

    Returns the snapshot path, or None when nothing was taken (fresh install, unchanged
    version, or failure). Does not update the marker — call ``record_version`` only after
    the migration actually succeeds, so a crash mid-upgrade still backs up next time."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None  # fresh install: nothing to lose yet
    marker = _marker(db_path)
    try:
        previous = marker.read_text("utf-8").strip()
    except OSError:
        # No marker but a database exists — the first boot after this guard shipped, which
        # is exactly when an unknown-age schema is most worth keeping a copy of.
        previous = ""
    if previous == version:
        return None
    dst = db_path.with_name(f"{db_path.name}{_PREFIX}{_safe(previous or 'unknown')}")
    try:
        _snapshot(db_path, dst)
    except Exception as exc:  # noqa: BLE001 — never block a boot over a precaution
        log.warning("pre-upgrade database snapshot failed: %s", exc)
        return None
    log.info("saved a pre-upgrade snapshot of the database at %s", dst)
    _prune(db_path)
    return dst


def record_version(db_path: str | Path, version: str) -> None:
    """Stamp the build that migrated this database. Call after the migration succeeded."""
    try:
        _marker(Path(db_path)).write_text(version, encoding="utf-8")
    except OSError as exc:
        log.warning("couldn't record the schema's app version: %s", exc)
