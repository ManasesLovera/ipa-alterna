"""Fixtures for database tests.

Two tiers:

- Pure unit tests run everywhere and only exercise metadata and pure helpers.
- `integration_light` tests run migrations and statements against a real
  PostgreSQL (the compose `pgvector/pgvector:pg16`); they are skipped
  automatically when no server answers at `IPA_TEST_POSTGRES_DSN`, falling
  back to `localhost:5432`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ipa.core.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DB_NAME = "ipa_unit_test"

_DSN = os.environ.get("IPA_TEST_POSTGRES_DSN", "postgresql+asyncpg://ipa:ipa@localhost:5432/ipa")
_SERVER_SYNC_DSN = _DSN.replace("+asyncpg", "+psycopg").rsplit("/", 1)[0] + "/ipa"
_TEST_SYNC_DSN = _SERVER_SYNC_DSN.rsplit("/", 1)[0] + "/" + TEST_DB_NAME
_TEST_ASYNC_DSN = _DSN.rsplit("/", 1)[0] + "/" + TEST_DB_NAME

_TRUNCATE = (
    "TRUNCATE users, tags, tag_fields, tag_versions, documents, document_steps, "
    "document_events, document_pages, extraction_versions, validations, chunks, "
    "webhooks, webhook_deliveries, idempotency_keys RESTART IDENTITY CASCADE"
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the integration_light marker.

    Args:
        config: The pytest configuration object.
    """
    config.addinivalue_line(
        "markers",
        "integration_light: needs a live PostgreSQL at IPA_TEST_POSTGRES_DSN,"
        " auto-skipped otherwise",
    )


def _run_migrations(revision: str) -> None:
    """Run an alembic command against the test database.

    Args:
        revision: Target revision, e.g. `head` or `base`.
    """
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    cfg.set_main_option("prepend_sys_path", str(REPO_ROOT / "src"))
    cfg.set_main_option("path_separator", "os")
    if revision == "head":
        command.upgrade(cfg, "head")
    else:
        command.downgrade(cfg, "base")


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create an isolated migrated database for the session.

    Returns:
        The async DSN of the freshly migrated test database.

    Yields:
        The DSN while the session runs; drops the database afterwards.
    """
    admin = sa.create_engine(_SERVER_SYNC_DSN, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
            connection.execute(sa.text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable at {_SERVER_SYNC_DSN}: {exc}")
    finally:
        admin.dispose()

    previous = os.environ.get("IPA_POSTGRES_DSN")
    os.environ["IPA_POSTGRES_DSN"] = _TEST_ASYNC_DSN
    get_settings.cache_clear()
    _run_migrations("head")
    yield _TEST_ASYNC_DSN
    _run_migrations("base")
    get_settings.cache_clear()
    if previous is None:
        os.environ.pop("IPA_POSTGRES_DSN", None)
    else:
        os.environ["IPA_POSTGRES_DSN"] = previous
    cleanup = sa.create_engine(_SERVER_SYNC_DSN, isolation_level="AUTOCOMMIT")
    with cleanup.connect() as connection:
        connection.execute(sa.text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)"))
    cleanup.dispose()


@pytest.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Provide an async engine bound to the migrated test database.

    Args:
        database_url: Session-scoped DSN fixture.

    Yields:
        The engine, disposed after the test.
    """
    engine = create_async_engine(database_url, pool_size=5)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_sessionmaker(
    db_engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Provide a session factory and truncate every table after the test.

    Truncation lives here rather than on `db_session` because some tests
    commit through their own sessions built from this factory.

    Args:
        db_engine: Engine fixture.

    Yields:
        The sessionmaker.
    """
    yield async_sessionmaker(db_engine, expire_on_commit=False)
    async with db_engine.begin() as connection:
        await connection.execute(sa.text(_TRUNCATE))


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide an open session, rolled back at test end.

    Args:
        db_sessionmaker: Session factory fixture.

    Yields:
        An open `AsyncSession`.
    """
    async with db_sessionmaker() as session:
        yield session
        await session.rollback()


@pytest.fixture
def document_factory(db_session: AsyncSession) -> Any:
    """Provide a factory inserting a minimal document row for FK tests.

    Args:
        db_session: Session fixture the inserts run through.

    Returns:
        An async callable accepting `sha256` and optional `tag_id` keyword
        arguments, returning the persisted `Document` DTO-ish ORM entity.
    """

    async def _create(*, sha256: str = "a" * 64, tag_id: UUID | None = None) -> Any:
        from ipa.db.enums import DocumentSource
        from ipa.db.models import Document

        document = Document(
            sha256=sha256,
            original_filename="sample.pdf",
            mime_type="application/pdf",
            size_bytes=1234,
            blob_key=f"originals/aa/{sha256}",
            source=DocumentSource.UI,
            tag_id=tag_id,
        )
        db_session.add(document)
        await db_session.flush()
        await db_session.refresh(document)
        return document

    return _create
