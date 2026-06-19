"""Create the database schema and seed default config for the target guild.

Run with:  uv run python -m scripts.init_db

Idempotent: safe to run repeatedly. We use direct ``create_all`` + vec0 DDL
rather than Alembic migrations during early development, while the schema is
still churning. Alembic gets wired in once the schema settles (Phase 8).
"""

from __future__ import annotations

import asyncio
import json
import os

from olisar.config import settings
from olisar.db.engine import get_engine, session_scope
from olisar.db.models import (
    Base,
)
from olisar.guild_setup import ensure_guild_defaults
from olisar.memory.vectors import create_fts_tables, create_vector_tables


def _default_literal(column) -> str:
    default = column.default
    value = None
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
    elif default is not None and getattr(default, "is_callable", False):
        # e.g. default=list / dict / lambda: [...] — evaluate to seed the literal.
        for call in (lambda: default.arg(), lambda: default.arg(None)):
            try:
                value = call()
                break
            except Exception:
                value = None
    coltype = str(column.type).upper()
    if coltype.startswith("JSON"):
        # JSON columns need a *valid-JSON* default — '' would break json.loads on
        # every load (the cause of the role-list migration bug).
        try:
            return "'" + json.dumps(value).replace("'", "''") + "'"
        except (TypeError, ValueError):
            return "'null'"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return "0" if coltype.startswith(("INT", "FLOAT", "NUM", "BOOL")) else "''"


def _add_missing_columns(sync_conn) -> None:
    """Lightweight migration: ALTER TABLE ADD COLUMN for any model column not yet
    present (SQLite supports ADD COLUMN). Evolves the schema across phases without
    dropping data — Alembic gets adopted once the schema settles (Phase 8)."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(sync_conn)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            coltype = column.type.compile(dialect=sync_conn.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {coltype}'
            if not column.nullable:
                ddl += f" DEFAULT {_default_literal(column)}"
            sync_conn.exec_driver_sql(ddl)
            print(f"  + migrated: added column {table.name}.{column.name}")


def _drop_repk_tables(sync_conn) -> None:
    """SQLite can't ALTER a primary key, so a table whose PK changed must be dropped
    and recreated by create_all. extension_state went global -> per-guild
    (key) -> (guild_id, key); dropping it just resets which extensions are on."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(sync_conn)
    if inspector.has_table("extension_state"):
        cols = {c["name"] for c in inspector.get_columns("extension_state")}
        if "guild_id" not in cols:
            sync_conn.exec_driver_sql("DROP TABLE extension_state")
            print("  + migrated: recreated extension_state with a per-guild key")


async def create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(_drop_repk_tables)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
        await create_vector_tables(conn, settings.embed_dim)
        await create_fts_tables(conn)


async def seed_defaults() -> None:
    guild_id = settings.target_guild_id
    if not guild_id:
        print("⚠  TARGET_GUILD_ID is unset; the bot seeds each guild it joins on startup.")
        return
    async with session_scope() as session:
        await ensure_guild_defaults(session, guild_id)
    print(f"✓ seeded defaults for guild {guild_id}")


async def main() -> None:
    os.makedirs(os.path.dirname(settings.database_path) or ".", exist_ok=True)
    await create_schema()
    print(f"✓ schema ready at {settings.database_path}")
    await seed_defaults()
    await get_engine().dispose()
    print("✓ done")


if __name__ == "__main__":
    asyncio.run(main())
