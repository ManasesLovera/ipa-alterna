"""Alembic environment, wired for async SQLAlchemy.

The connection URL comes from `ipa.core.config`, never from `alembic.ini`.

`target_metadata` is imported from `ipa.db.base:Base`, which T02 creates. Until
then the import is tolerated as missing so T01 can be verified standalone: the
migrations still run, autogenerate simply has nothing to diff against.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from ipa.core.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

try:  # TODO(T02): remove the fallback once ipa.db.base exists.
    from ipa.db.base import Base  # type: ignore[import-not-found]

    target_metadata: Any = Base.metadata
except ModuleNotFoundError:
    target_metadata = None

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
