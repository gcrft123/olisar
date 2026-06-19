"""Role-based access control — the pure decision, kept Discord-agnostic.

Admins configure two role lists on the dashboard (stored on ``GuildConfig``):
* **blocked_role_ids** — anyone with one of these can never use Olisar.
* **allowed_role_ids** — if non-empty, ONLY members with one of these may use it.

Precedence: a server admin (Manage Server) always passes; otherwise a blocked role
denies outright; otherwise a non-empty allow-list restricts to its members; an empty
pair on both lists means open to everyone (the default). ``access_allowed`` is the
single source of truth, used for both chat replies and slash commands.
"""

from __future__ import annotations

from collections.abc import Iterable


def _as_int_set(values: Iterable | None) -> set[int]:
    """Coerce a JSON role-id list (strings, for snowflake precision) to ints."""
    out: set[int] = set()
    for value in values or []:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def access_allowed(
    *,
    role_ids: set[int],
    is_admin: bool,
    allowed: Iterable | None,
    blocked: Iterable | None,
) -> bool:
    """Whether a member with ``role_ids`` may use Olisar under the given lists."""
    if is_admin:
        return True  # Manage-Server admins always pass (can't lock themselves out)
    blocked_set = _as_int_set(blocked)
    if role_ids & blocked_set:
        return False
    allowed_set = _as_int_set(allowed)
    if allowed_set and not (role_ids & allowed_set):
        return False
    return True
