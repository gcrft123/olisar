"""``sqlite-vec`` vector tables and KNN helpers.

Vectors live in ``vec0`` virtual tables that SQLAlchemy's ORM can't model, so we
manage them with raw SQL here. Each vector table is keyed by ``rowid`` set equal
to the parent relational row's primary key (e.g. ``message.id``), so a vector and
its metadata are joined by id and deleted together.

Each vector is filed under its parent row's ``guild_id`` (0 for DMs, as in the parent
tables), a vec0 partition key, and KNN searches only the guilds it's given. KNN is a
brute-force scan, so one server's reply no longer reads every server's vectors, and the
top k are that server's own rather than whatever of it survived a global top k.
"""

from __future__ import annotations

import heapq
import logging
import re
import sqlite3
import time
from collections.abc import Iterable, Sequence

import sqlite_vec
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

log = logging.getLogger("olisar.vectors")

# Vector table name -> the relational table whose rowid it mirrors.
VECTOR_TABLES: dict[str, str] = {
    "message_embedding": "message",
    "channel_summary_embedding": "channel_summary",
    "user_memory_embedding": "user_memory",
    "kb_chunk_embedding": "kb_chunk",
}

# Vectors per vec0 chunk. A chunk's storage is allocated whole, per table and per partition,
# so vec0's default of 1024 (3 MB of 768-d vectors) would cost every server 3 MB a table
# from its first vector. 128 keeps that to 384 KB, doesn't slow KNN, and makes each insert
# and delete (which walk the chunk's blob) several times cheaper.
CHUNK_SIZE = 128

# Parent rows copied per transaction when a vector table is rebuilt partitioned.
_REBUILD_BATCH = 1000


def _vec0_ddl(name: str, dim: int) -> str:
    return (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING vec0("
        f"guild_id integer partition key, embedding float[{dim}], chunk_size={CHUNK_SIZE})"
    )


async def create_vector_tables(conn: AsyncConnection, dim: int) -> None:
    """Create all vec0 virtual tables (idempotent). Call inside engine.begin()."""
    for name in VECTOR_TABLES:
        await conn.exec_driver_sql(_vec0_ddl(name, dim))


def partition_vector_tables(path: str, *, batch: int = _REBUILD_BATCH) -> None:
    """Rebuild any vector table from before guild partitioning, at startup.

    vec0 can't add a partition key to a table or rename one, so each is copied into a new
    partitioned table, ``batch`` parent rows per committed transaction with the guild read
    off the parent row, and then swapped in by one transaction that drops the old table and
    renames the new one (and its shadow tables) into place. Until that swap the old table is
    untouched, so a copy cut short anywhere is picked up where it stopped on the next start,
    and the table is always one or the other. Vectors whose row is gone aren't carried over.

    Runs on its own connection (in a thread, from create_schema) for the explicit
    transactions, with a larger page cache: vec0 reads a vector by walking its chunk's blob,
    which thrashes the default 2 MB cache."""
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_size_limit=67108864")
        con.execute("PRAGMA cache_size=-65536")
        for name, parent in VECTOR_TABLES.items():
            _partition(con, name, parent, batch)
    finally:
        con.close()


def _partition(con: sqlite3.Connection, name: str, parent: str, batch: int) -> None:
    row = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    if row is None or "partition key" in row[0]:
        return
    dim = int(re.search(r"float\[(\d+)\]", row[0]).group(1))
    rebuild = f"{name}_rebuild"
    con.execute(_vec0_ddl(rebuild, dim))
    done = con.execute(f"SELECT max(rowid) FROM {rebuild}").fetchone()[0] or 0
    started = time.monotonic()
    log.info("filing %s under each vector's server (from row %d)", name, done)
    while True:
        ids = [r[0] for r in con.execute(
            f"SELECT id FROM {parent} WHERE id > ? ORDER BY id LIMIT ?", (done, batch)
        )]
        if not ids:
            break
        con.execute("BEGIN IMMEDIATE")
        try:
            # CROSS JOIN keeps the parent's id range as the outer loop, so the old table is
            # read by rowid lookups rather than scanned whole for every batch.
            con.execute(
                f"INSERT INTO {rebuild}(rowid, guild_id, embedding) "
                f"SELECT p.id, p.guild_id, v.embedding FROM {parent} p "
                f"CROSS JOIN {name} v ON v.rowid = p.id WHERE p.id BETWEEN ? AND ?",
                (ids[0], ids[-1]),
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        done = ids[-1]

    before = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(f"DROP TABLE {name}")
        con.execute(f"ALTER TABLE {rebuild} RENAME TO {name}")
        prefix = f"{rebuild}_"
        shadows = con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND substr(name, 1, ?) = ?",
            (len(prefix), prefix),
        ).fetchall()
        for (shadow,) in shadows:
            con.execute(f'ALTER TABLE "{shadow}" RENAME TO "{name}_{shadow[len(prefix):]}"')
        after = con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    log.info(
        "%s is filed by server: %d vectors in %.1f s (%d without a row left behind)",
        name, after, time.monotonic() - started, before - after,
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
    """Insert/replace one embedding row, keyed to the parent's primary key and filed under
    the parent row's guild, which is read from the row itself (so it must be flushed)."""
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    await session.execute(
        text(
            f"INSERT OR REPLACE INTO {table}(rowid, guild_id, embedding) "
            f"SELECT id, guild_id, :emb FROM {VECTOR_TABLES[table]} WHERE id = :rowid"
        ),
        {"rowid": rowid, "emb": serialize(vector)},
    )


async def delete_embedding(session: AsyncSession, table: str, *rowids: int) -> None:
    """Delete the embeddings of ``rowids``. vec0 answers ``rowid = ?`` from its rowid
    index but reads ``rowid IN (...)`` as a scan of the whole table (110 ms at 500k
    vectors, however few ids), so a batch goes as one executemany of point deletes."""
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    if not rowids:
        return
    await session.execute(
        text(f"DELETE FROM {table} WHERE rowid = :rowid"),
        [{"rowid": int(r)} for r in rowids],
    )


async def knn(
    session: AsyncSession,
    table: str,
    query_vector: Sequence[float],
    k: int = 5,
    *,
    guild_ids: Iterable[int],
) -> list[tuple[int, float]]:
    """Return the ``k`` rows filed under ``guild_ids`` (0 is DMs) nearest the query, as
    ``(rowid, distance)``, closest first. Each guild is searched on its own and the results
    merged: given several partitions at once, vec0 applies ``k`` to each of them."""
    assert table in VECTOR_TABLES, f"unknown vector table {table!r}"
    q = serialize(query_vector)
    hits: list[tuple[int, float]] = []
    for guild_id in dict.fromkeys(int(g) for g in guild_ids):
        result = await session.execute(
            text(
                f"SELECT rowid, distance FROM {table} "
                "WHERE embedding MATCH :q AND guild_id = :g ORDER BY distance LIMIT :k"
            ),
            {"q": q, "g": guild_id, "k": k},
        )
        hits.extend((int(row[0]), float(row[1])) for row in result.all())
    return heapq.nsmallest(k, hits, key=lambda hit: hit[1])
