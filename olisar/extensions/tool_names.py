"""Which tool names an extension may declare.

Olisar's own tools keep their names (``olisar.tools.CORE_TOOL_NAMES``). An extension tool
called ``remember`` or ``web_search`` would otherwise get every call the model meant for the
core tool, arguments and all, and the model would take what it returned as the core tool's
answer. A reply always runs the core tool under its name; an installed extension's tool that
takes one is left out when the extension loads, and installing or saving one is refused
before it gets that far.
"""

from __future__ import annotations

import logging

log = logging.getLogger("olisar.extensions.tool_names")

# (extension key, tool name) pairs already logged as skipped, so a reload every few
# seconds doesn't repeat the warning.
_reported: set[tuple[str, str]] = set()


def reserved() -> frozenset[str]:
    """The core tools' names. Imported late: ``olisar.tools`` pulls in most of the bot."""
    from olisar.tools import CORE_TOOL_NAMES

    return CORE_TOOL_NAMES


def declared_tools(manifest: dict | None) -> list[str]:
    """The tool names a manifest declares."""
    return [
        str(tool["name"])
        for tool in (manifest or {}).get("tools", []) or []
        if isinstance(tool, dict) and tool.get("name")
    ]


def conflicts(manifest: dict | None) -> list[str]:
    """Why a manifest's tools can't be used, one line per name that's a core tool's; empty
    when they all can."""
    taken = reserved()
    return [
        f"{name} is one of Olisar's own tools"
        for name in dict.fromkeys(declared_tools(manifest))
        if name in taken
    ]


def usable(ext_key: str, name: str) -> bool:
    """Whether extension ``ext_key`` may have a tool called ``name``. Logs the first time
    one is skipped."""
    if name not in reserved():
        return True
    if (ext_key, name) not in _reported:
        _reported.add((ext_key, name))
        log.warning("extension %s declares a tool named %s, which is one of Olisar's own; skipped", ext_key, name)
    return False


__all__ = ["conflicts", "declared_tools", "reserved", "usable"]
