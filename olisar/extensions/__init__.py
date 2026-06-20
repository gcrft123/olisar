"""Bot extensions — togglable packages of extra features.

Importing this package populates the registry with the built-in extensions, so
anything that needs the catalog (the pipeline, the admin API) just imports from
here. See ``base.py`` for the framework and ``builtin.py`` for the examples.
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
from olisar.extensions.builtin import register_builtins
from olisar.extensions.star_citizen import register_star_citizen
from olisar.extensions.welcome import register_welcome

register_builtins()
register_star_citizen()
register_welcome()

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
