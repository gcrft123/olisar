"""Bot extensions — togglable packages of extra features.

All extensions are now **SDK extensions** — operator-authored TypeScript plus the
migrated built-ins (dice, calculator, concise_mode, welcome, star_citizen) — stored in
the DB as ``ExtensionPackage`` rows, loaded live by ``user_registry``, and executed in
the sandbox (see ``olisar/sandbox``). The Python ``star_citizen.py`` is kept on disk as
the reference implementation but is no longer registered.
"""

from __future__ import annotations

from olisar.extensions.base import (
    Extension,
    ExtensionTool,
    GatheredExtensions,
    all_extensions,
    enabled_keys,
    gather_enabled,
    get_extension,
    is_enabled,
    register,
)

__all__ = [
    "Extension",
    "ExtensionTool",
    "GatheredExtensions",
    "all_extensions",
    "enabled_keys",
    "gather_enabled",
    "get_extension",
    "is_enabled",
    "register",
]
