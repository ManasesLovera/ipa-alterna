"""Tag, tag field and tag version models.

A tag is a document type: it owns an ordered list of typed fields that
generate the JSON Schema handed to the extraction LLM. Tags are versioned via
immutable snapshots so historical extractions stay interpretable.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ipa.core.enums import FieldType
from ipa.db.base import Base, TimestampMixin, UuidPkMixin, str_enum


class Tag(UuidPkMixin, TimestampMixin, Base):
    """A document type and extraction schema; see `docs/tasks/T02-database.md`."""

    __tablename__ = "tags"
    __table_args__ = (
        sa.CheckConstraint(
            "auto_approve_threshold IS NULL OR "
            "(auto_approve_threshold >= 0 AND auto_approve_threshold <= 1)",
            name="auto_approve_threshold_range",
        ),
    )

    slug: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("''"))
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    auto_approve_threshold: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 3), nullable=True)
    prompt_template: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    classification_hints: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class TagField(UuidPkMixin, TimestampMixin, Base):
    """One typed field within a tag's extraction schema."""

    __tablename__ = "tag_fields"
    __table_args__ = (
        sa.UniqueConstraint("tag_id", "key", name="uq_tag_fields_tag_id_key"),
        sa.UniqueConstraint(
            "tag_id", "position", name="uq_tag_fields_tag_id_position", deferrable=True
        ),
    )

    tag_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    label: Mapped[str] = mapped_column(sa.Text, nullable=False)
    field_type: Mapped[FieldType] = mapped_column(str_enum(FieldType, "field_type"), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    is_required: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    enum_values: Mapped[list[str] | None] = mapped_column(ARRAY(sa.Text), nullable=True)
    regex: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    min_value: Mapped[Decimal | None] = mapped_column(sa.Numeric, nullable=True)
    max_value: Mapped[Decimal | None] = mapped_column(sa.Numeric, nullable=True)
    min_length: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    max_length: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    item_type: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    object_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class TagVersion(UuidPkMixin, Base):
    """Immutable snapshot of a tag and its fields at a given version."""

    __tablename__ = "tag_versions"
    __table_args__ = (
        sa.UniqueConstraint("tag_id", "version", name="uq_tag_versions_tag_id_version"),
    )

    tag_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tags.id"), nullable=False)
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
