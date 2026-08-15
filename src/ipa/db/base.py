"""Declarative base, naming convention and shared column mixins.

Every ORM model inherits from `Base`. The metadata naming convention pins the
generated names of constraints and indexes so that Alembic autogenerate
produces a stable, reviewable diff instead of Postgres-chosen anonymous names.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all ORM models; carries the deterministic naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_cls: type[StrEnum], type_name: str) -> sa.Enum:
    """Build a native Postgres enum type that persists `StrEnum` *values*.

    SQLAlchemy defaults to persisting enum member *names* (e.g. `PENDING`);
    every persisted value in this platform is the lowercase wire form (e.g.
    `pending`), so `values_callable` pins the member values instead.

    Args:
        enum_cls: The shared enum class to persist.
        type_name: Name of the Postgres enum type created in the database.

    Returns:
        A `sa.Enum` type bound to `enum_cls` values under `type_name`.
    """
    return sa.Enum(
        enum_cls,
        name=type_name,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class UuidPkMixin:
    """UUID primary key column shared by every aggregate root and child table."""

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """`created_at` / `updated_at` columns maintained by the database clock."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedOnlyMixin:
    """Immutable `created_at` column for append-only tables that are never updated."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
