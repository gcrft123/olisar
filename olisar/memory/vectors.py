"""``sqlite-vec`` vector tables and KNN helpers.

Vectors live in ``vec0`` virtual tables that SQLAlchemy's ORM can't model, so we
manage them with raw SQL here. Each vector table is keyed by ``rowid`` set equal
to the parent relational row's primary key (e.g. ``message.id``), so a vector and
its metadata are joined by id and deleted together.

Single-guild note: KNN runs globally and the caller filters results by joining
to the parent table on ``guild_id``. With one server (TARGET_GUILD_ID) that's
exact; for true multi-guild use, switch these to vec0 partition keys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlite_vec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# Vector table name -> the relational table whose rowid it mirrors.
VECTOR_TABLES: dict[str, str] = {
    "message_embedding": "message",
    "channel_summary_embedding": "channel_summary",
    "user_memory_embedding": "user_memory",
    "kb_chunk_embedding": "kb_chunk",
}


async def create_vector_tables(conn: AsyncConnection, dim: int) -> None:
    """Create all vec0 virtual tables (idempotent). Call inside engine.begin()."""
    for name in VECTOR_TABLES:
        await conn.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING vec0(embedding float[{dim}])"
        )


# Full-text keyword index over the server-wide search corpus (search_message).
# External-content FTS5 (stores only the inverted index, not a second copy of the
# text) kept in lockstep with the table by triggers. The AFTER DELETE trigger uses
# FTS5's special 'delete' command with the OLD content image — this is what keeps
# the index consistent when olisar/memory/purge.py deletes a user's rows, so
# /forget-me purges searchable text too. Do NOT write search_message.content via
# raw SQL that bypasses these triggers, or the index will drift.
_FTS_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_message_fts USING fts5("
    "content, content='search_message', content_rowid='id', tokenize='unicode61')",
    "CREATE TRIGGER IF NOT EXISTS search_message_ai AFTER INSERT ON search_message BEGIN "
    "INSERT INTO search_message_fts(rowid, content) VALUES (new.id, new.content); END",
    "CREATE TRIGGER IF NOT EXISTS search_message_ad AFTER DELETE ON search_message BEGIN "
    "INSERT INTO search_message_fts(search_message_fts, rowid, content) "
    "VALUES ('delete', old.id, old.content); END",
    "CREATE TRIGGER IF NOT EXISTS search_message_au AFTER UPDATE ON search_message BEGIN "
    "INSERT INTO search_message_fts(search_message_fts, rowid, content) "
    "VALUES ('delete', old.id, old.content); "
    "INSERT INTO search_message_fts(rowid, content) VALUES (new.id, new.content); END",
]


async def create_fts_tables(conn: AsyncConnection) -> None:
    """Create the search_message FTS5 index + sync triggers, then backfill any
    rows that predate the index (idempotent). Call inside engine.begin(), after
    the search_message table exists."""
    for stmt in _FTS_DDL:
        await conn.exec_driver_sql(stmt)
    # One-time catch-up: index rows inserted before the triggers existed.
    await conn.exec_driver_sql(
        "INSERT INTO search_message_fts(rowid, content) "
        "SELECT id, content FROM search_message "
        "WHERE id NOT IN (SELECT rowid FROM search_message_fts)"
    )


def serialize(vector: Sequence[float]) -> bytes:
    """Pack a float vector into sqlite-vec's compact binary format."""
    return sqlite_vec.serialize_float32(list(vector))


async def upsert_embedding(
    session: AsyncSession, table: str, rowid: int, vector: Sequence[float]
) -> None:
    """Insert/replace one embedding row, keyed to the parent's primary key."""
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    await session.execute(
        text(f"INSERT OR REPLACE INTO {table}(rowid, embedding) VALUES (:rowid, :emb)"),
        {"rowid": rowid, "emb": serialize(vector)},
    )


async def delete_embedding(session: AsyncSession, table: str, rowid: int) -> None:
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    await session.execute(
        text(f"DELETE FROM {table} WHERE rowid = :rowid"), {"rowid": rowid}
    )


async def knn(
    session: AsyncSession, table: str, query_vector: Sequence[float], k: int = 5
) -> list[tuple[int, float]]:
    """Return the ``k`` nearest rows as ``(rowid, distance)``, closest first."""
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    result = await session.execute(
        text(
            f"SELECT rowid, distance FROM {table} "
            "WHERE embedding MATCH :q ORDER BY distance LIMIT :k"
        ),
        {"q": serialize(query_vector), "k": k},
    )
    return [(int(row[0]), float(row[1])) for row in result.all()]
