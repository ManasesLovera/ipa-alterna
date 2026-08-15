"""Alembic environment, wired for async SQLAlchemy.

The connection URL comes from `ipa.core.config`, never from `alembic.ini`.
`target_metadata` is the ORM metadata from `ipa.db`; importing `ipa.db.models`
registers every table on it so autogenerate diffs the full schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import ipa.db.models  # noqa: F401  (imports register tables on Base.metadata)
from ipa.core.config import get_settings
from ipa.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", get_settings().postgres.dsn)


def run_migrations_offline() -> None:
    """Emit migration SQL without connecting to a database.

    Returns:
        None.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an open connection.

    Args:
        connection: A synchronous connection facade over the async driver.

    Returns:
        None.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run the migrations through it.

    Returns:
        None.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database.

    Returns:
        None.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
