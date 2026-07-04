"""Async SQLAlchemy engine + session factory.

The tricky part here is that ``sqlite-vec`` is a *loadable extension*: it has to
be loaded into every new SQLite connection before any vector query will work. We
do that (plus turn on WAL mode and foreign keys) from a SQLAlchemy ``connect``
event listener, which fires for each pooled connection.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

import sqlite_vec
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.util import await_only

# Engines/sessionmakers are keyed by the SQLite file path rather than a single global, so
# switching the active bot profile just disposes the current entry and builds the next. The
# ``current_profile`` contextvar is the seam for *future* concurrent local bots: v1 leaves it
# unset (so everything resolves to the registry's active profile), while a later concurrent
# build would set it per bot task / per API request to route each context to its own DB —
# with no call-site changes, since all DB access already flows through ``session_scope()``.
_engines: dict[str, AsyncEngine] = {}
_sessionmakers: dict[str, async_sessionmaker[AsyncSession]] = {}

current_profile: ContextVar[str | None] = ContextVar("current_profile", default=None)


def current_db_path() -> str:
    """The SQLite path for the async context's profile: the ``current_profile`` contextvar
    when set, else the registry's active profile."""
    from olisar.runtime import profiles

    return str(profiles.db_path_for(current_profile.get() or profiles.active_id()))


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
    path = current_db_path()
    engine = _engines.get(path)
    if engine is None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{path}", echo=False, future=True
        )
        _register_connection_setup(engine)
        _engines[path] = engine
    return engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    path = current_db_path()
    sessionmaker = _sessionmakers.get(path)
    if sessionmaker is None:
        sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
        _sessionmakers[path] = sessionmaker
    return sessionmaker


async def reset_engine(path: str | None = None) -> None:
    """Dispose and forget the engine for ``path`` (default: the current profile's DB), so
    the next ``get_engine()`` rebuilds it. Disposing closes the pooled aiosqlite connections
    and releases the WAL/SHM handles — required before another profile opens the same file,
    and used by a profile switch to tear down the outgoing bot's engine."""
    target = path or current_db_path()
    engine = _engines.pop(target, None)
    _sessionmakers.pop(target, None)
    if engine is not None:
        with contextlib.suppress(Exception):
            await engine.dispose()


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
