"""System configuration and idempotency models.

`schema_config` records build-time schema decisions — most importantly the
pgvector dimension — so startup can refuse to run when the deployed schema no
longer matches the configuration that generated it. `idempotency_keys` is the
durable backstop for the Redis claim made on every creating POST.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ipa.db.base import Base, CreatedOnlyMixin, TimestampMixin

VECTOR_CONFIG_KEY = "vector"
"""Row key under which the embedding dimension is recorded."""


class SchemaConfig(TimestampMixin, Base):
    """A single key/value row of build-time schema configuration."""

    __tablename__ = "schema_config"

    key: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IdempotencyKey(CreatedOnlyMixin, Base):
    """A durable idempotency key with its cached response, if any."""

    __tablename__ = "idempotency_keys"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    key: Mapped[str] = mapped_column(sa.Text, unique=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(sa.Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
