"""Which slash command names an extension may declare.

A slash command name belongs to one command at a time. Olisar's own commands keep theirs, so
an extension declaring ``killswitch`` can't take over the panic button or ``/forget-me`` in
every server, and between extensions the one installed first keeps a name. The bot skips a
command that breaks this when it builds the command list; installing or saving one is refused
before it gets that far.
"""

from __future__ import annotations

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from olisar.db.models import ExtensionPackage

# The slash commands the bot's own cogs register (``bot/client.py`` INITIAL_COGS). A test
# checks this against the cogs, so a new built-in command can't be left off.
BUILTIN_COMMANDS = frozenset({
    "ask", "catchup", "dm-indexing", "forget-me", "killswitch", "olisar", "ping", "privacy",
})


def declared_commands(manifest: dict | None) -> list[str]:
    """The command names a manifest declares, as the bot registers them."""
    out: list[str] = []
    for cmd in (manifest or {}).get("commands", []) or []:
        if isinstance(cmd, dict) and cmd.get("name"):
            out.append(str(cmd["name"])[:32])
    return out


def claim_order():
    """The order extensions claim command names in: built-ins, then the operator's own, then
    installed ones, each oldest first."""
    rank = case(
        (ExtensionPackage.kind == "builtin", 0),
        (ExtensionPackage.origin == "local", 1),
        else_=2,
    )
    return (rank, ExtensionPackage.created_at, ExtensionPackage.key)


async def conflicts(session: AsyncSession, key: str, manifest: dict | None) -> list[str]:
    """Why ``key``'s commands can't be registered, one line per clashing name; empty when
    they all can. The extension's own current version doesn't count against it."""
    problems: list[str] = []
    taken: dict[str, str] = {}
    for pkg in (await session.scalars(select(ExtensionPackage).where(ExtensionPackage.key != key))).all():
        for name in declared_commands(pkg.manifest):
            taken.setdefault(name, pkg.key)
    seen: set[str] = set()
    for name in declared_commands(manifest):
        if name in BUILTIN_COMMANDS:
            problems.append(f"/{name} is one of Olisar's own commands")
        elif name in taken:
            problems.append(f"/{name} already belongs to the '{taken[name]}' extension")
        elif name in seen:
            problems.append(f"/{name} is declared twice")
        seen.add(name)
    return problems


__all__ = ["BUILTIN_COMMANDS", "claim_order", "conflicts", "declared_commands"]
