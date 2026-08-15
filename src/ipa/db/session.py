"""Async engine and session lifecycle.

One engine per process, built lazily from `get_settings()`. API code uses the
`get_session` FastAPI dependency; Celery task bodies use `session_scope`.
Both commit on clean exit and roll back on error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ipa.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async engine.

    Returns:
        The cached `AsyncEngine` built from the current settings.
    """
    settings = get_settings()
    return create_async_engine(
        settings.postgres.dsn,
        pool_size=settings.postgres.pool_size,
        echo=settings.postgres.echo,
        pool_pre_ping=True,
    )


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the process engine.

    Returns:
        An `async_sessionmaker` producing sessions that keep loaded attributes
        usable after commit.
    """
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session that commits on success and rolls back on error.

    Intended as a FastAPI dependency; call sites never commit explicitly.

    Yields:
        An `AsyncSession`, committed on clean completion of the request.
    """
    async with session_scope() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope for worker code.

    Args are none; the engine comes from `get_engine()`.

    Yields:
        An `AsyncSession`; commits on clean exit, rolls back on exception.
    """
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the cached engine and drop it so a new one is built next use.

    Returns:
        None.
    """
    engine = get_engine()
    get_engine.cache_clear()
    await engine.dispose()
