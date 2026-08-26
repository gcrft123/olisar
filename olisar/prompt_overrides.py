"""Sandbox-only overrides for the baked-in prompt blocks.

Olisar's system prompt has two halves: the admin-editable persona (the ``persona``
table, owned by the dashboard) and the fixed blocks compiled into the source — the
operating rules, the tool briefing, the proactive note. The fixed half is deliberately
not dashboard-editable, because it is the guardrail layer and a persona edit must not be
able to remove it.

That same half is the one most worth *researching*, and iterating on it by editing source
and restarting turns every experiment into a commit. ``OLISAR_PROMPT_OVERRIDES`` points at
a JSON file supplying replacement text for those blocks, so a harness can A/B dozens of
candidate rule sets and then land the winner in the source properly, once.

Unset — every real deployment, and every test that doesn't opt in — each getter returns the
text compiled into its own module and this file changes nothing.

The file is re-read when its mtime moves, so swapping variants doesn't need a restart. A
missing, unreadable, or malformed file falls back to the compiled-in defaults and logs once;
a bad override must never be able to strip Olisar's guardrails.

Recognised keys (all optional): ``operating_rules``, ``tools_note``, ``proactive_note``,
``follow_up_note``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("olisar.prompt_overrides")

_ENV_VAR = "OLISAR_PROMPT_OVERRIDES"

_cache: dict[str, str] = {}
_cache_key: tuple[str, float] | None = None
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args: object) -> None:
    """Log a failure the first time only — this runs on the reply path, and a bad
    override path would otherwise print on every single message."""
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


def _load() -> dict[str, str]:
    """The override map, re-read whenever the file's mtime changes. ``{}`` when the env
    var is unset or the file can't be read as a JSON object of strings."""
    global _cache, _cache_key

    path_str = os.environ.get(_ENV_VAR, "").strip()
    if not path_str:
        return {}
    path = Path(path_str)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _warn_once(path_str, "%s points at an unreadable path (%s) — using built-in prompts",
                   _ENV_VAR, path_str)
        return {}

    key = (path_str, mtime)
    if key == _cache_key:
        return _cache

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _warn_once(f"parse:{path_str}", "%s (%s) isn't readable JSON — using built-in prompts",
                   _ENV_VAR, path_str)
        return {}
    if not isinstance(raw, dict):
        _warn_once(f"shape:{path_str}", "%s (%s) must be a JSON object — using built-in prompts",
                   _ENV_VAR, path_str)
        return {}

    # Only non-empty strings count. A key set to null/""/a number means "no override"
    # rather than "use an empty prompt block" — the latter is never what's wanted, and
    # silently blanking the operating rules is precisely the failure to avoid.
    _cache = {k: v for k, v in raw.items() if isinstance(v, str) and v.strip()}
    _cache_key = key
    _warned.discard(path_str)
    log.info("loaded prompt overrides from %s: %s", path_str, ", ".join(sorted(_cache)) or "(none)")
    return _cache


def _get(key: str, default: str) -> str:
    try:
        return _load().get(key) or default
    except Exception:  # pragma: no cover — a prompt override must never break a reply
        log.exception("reading prompt overrides failed; using the built-in %s", key)
        return default


def operating_rules(default: str) -> str:
    """The fixed operating-rules block appended after the editable persona."""
    return _get("operating_rules", default)


def tools_note(default: str) -> str:
    """The tool briefing folded into the system prompt on the reply path."""
    return _get("tools_note", default)


def proactive_note(default: str) -> str:
    """The per-reply note used when Olisar chimes in unprompted."""
    return _get("proactive_note", default)


def follow_up_note(default: str) -> str:
    """The per-reply note for continuing a conversation Olisar is already in.

    Its own key rather than sharing ``proactive_note``: interrupting a conversation and
    continuing your own are different instructions with opposite failure modes, and one
    override for both would quietly rewrite the wrong one.
    """
    return _get("follow_up_note", default)


def active() -> dict[str, str]:
    """Which blocks are currently overridden (for the harness to report). Never the text."""
    return {k: f"{len(v)} chars" for k, v in _load().items()}
