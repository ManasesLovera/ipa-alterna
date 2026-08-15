"""Extraction version pointers and human validation records.

Extraction bodies live in MongoDB; these tables track which version is
current and who validated what. Extractions are append-only — a correction
inserts a new version rather than editing an old one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ipa.core.enums import ExtractionSource
from ipa.db.base import Base, CreatedOnlyMixin, UuidPkMixin, str_enum
from ipa.db.enums import ValidationDecision


class ExtractionVersion(UuidPkMixin, CreatedOnlyMixin, Base):
    """Pointer to one extraction version stored in MongoDB."""

    __tablename__ = "extraction_versions"
    __table_args__ = (
        sa.UniqueConstraint(
            "document_id", "version", name="uq_extraction_versions_document_id_version"
        ),
        sa.Index(
            "uq_extraction_versions_document_id_current",
            "document_id",
            unique=True,
            postgresql_where=sa.text("is_current"),
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tag_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tags.id"), nullable=False)
    tag_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    source: Mapped[ExtractionSource] = mapped_column(
        str_enum(ExtractionSource, "extraction_source"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    document_confidence: Mapped[Decimal] = mapped_column(sa.Numeric(4, 3), nullable=False)
    mongo_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_current: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
    created_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class Validation(UuidPkMixin, CreatedOnlyMixin, Base):
    """One validation decision over an extraction version."""

    __tablename__ = "validations"

    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id"), nullable=False
    )
    extraction_version_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("extraction_versions.id"), nullable=False
    )
    decision: Mapped[ValidationDecision] = mapped_column(
        str_enum(ValidationDecision, "validation_decision"), nullable=False
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    corrected_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    auto: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.false())
