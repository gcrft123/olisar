"""Async SQLAlchemy engine + session factory.

The tricky part here is that ``sqlite-vec`` is a *loadable extension*: it has to
be loaded into every new SQLite connection before any vector query will work. We
do that (plus turn on WAL mode and foreign keys) from a SQLAlchemy ``connect``
event listener, which fires for each pooled connection.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sqlite_vec
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.util import await_only

from olisar.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _register_connection_setup(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _record):  # noqa: ANN001
        # aiosqlite exposes enable_load_extension/load_extension as *coroutines*
        # (they run on its worker thread), so we can't use sqlite_vec.load()
        # directly. Reach the real aiosqlite connection and drive its async load
        # methods with await_only, which bridges sync->async inside the greenlet.
        driver = dbapi_connection.driver_connection  # aiosqlite.Connection

        async def _load_vec() -> None:
            await driver.enable_load_extension(True)
            await driver.load_extension(sqlite_vec.loadable_path())
            await driver.enable_load_extension(False)

        await_only(_load_vec())

        # Pragmas: WAL lets the API read while the bot writes; foreign_keys
        # enforces our ON DELETE CASCADE; busy_timeout avoids "database locked".
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.async_db_url, echo=False, future=True)
        _register_connection_setup(_engine)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session: commits on success, rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
