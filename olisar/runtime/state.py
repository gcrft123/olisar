"""Publish a machine-readable snapshot of this backend into its data dir.

The desktop app's server-mode control panel drives a remote container over SSH. Without
this file it had to *infer* what was over there by grepping ``docker compose logs`` for a
``…ts.net`` URL — which streamed the container's whole log history on a miss (the cause of
a 40s status timeout), and gave no way at all to learn which version was running.

So the backend writes ``state.json`` into ``OLISAR_DATA_DIR`` at boot and whenever the
tunnel changes, and the client reads it with one ``docker exec … cat``. Only what the
*running process* knows belongs here: the public URL and the startup self-checks. Version
and health deliberately don't — the client reads those from the image's OCI labels and
Docker's own healthcheck, which still resolve when the container is stopped. ``version``
is written anyway as a cross-check, since a bind-mounted or copied data dir can outlive
the image that made it.

Writes are atomic (tmp + ``os.replace``) so a reader never sees a half-written file, and
every failure here is swallowed — publishing state must never be able to fail a boot.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("olisar.state")

STATE_FILENAME = "state.json"

# Captured once per process so rewrites (e.g. the tunnel coming up later) keep reporting
# when the backend actually started, not when the file was last touched.
_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")

# The last payload written. Rewrites merge into this so a partial update (just the URL)
# doesn't drop fields an earlier, fuller write published.
_last: dict = {}


def state_path() -> Path:
    from olisar.runtime import paths

    return paths.data_dir() / STATE_FILENAME


def _prune(fields: dict) -> dict:
    """Drop unset values so absent facts are *missing* keys, not nulls/empties a reader
    would have to special-case. ``False`` is a real value and survives."""
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def write(**fields) -> dict:
    """Merge ``fields`` into the published snapshot and write it atomically.

    Returns the payload written (empty on failure). Pass ``public_url=""`` to clear a
    fact — pruning turns it back into an absent key."""
    global _last
    from olisar.config import settings
    from olisar.updates import current_version

    payload = _prune({**_last, **fields})
    payload["version"] = current_version()
    payload["headless"] = bool(settings.headless)
    payload["started_at"] = _STARTED_AT
    payload["written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    path = state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — never fail a boot over telemetry
        log.warning("couldn't write %s: %s", path, exc)
        with_suppressed_unlink(tmp)
        return {}
    _last = payload
    return payload


def with_suppressed_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def read() -> dict:
    """The published snapshot, or ``{}`` when it's missing or unreadable."""
    try:
        return json.loads(state_path().read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
