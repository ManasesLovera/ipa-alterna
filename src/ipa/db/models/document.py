"""Document aggregate models: documents, steps, events and the page index.

The `documents` table is the anchor of the pipeline state machine. Its rows
never hold file bytes or extracted bodies — only state, provenance and
references into the blob store and MongoDB.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ipa.core.enums import DocumentStatus, PipelineStep, StepStatus
from ipa.db.base import Base, CreatedOnlyMixin, TimestampMixin, UuidPkMixin, str_enum
from ipa.db.enums import DocumentSource


class Document(UuidPkMixin, TimestampMixin, Base):
    """A single ingested document and its coarse pipeline state."""

    __tablename__ = "documents"
    __table_args__ = (
        sa.Index("ix_documents_status", "status"),
        sa.Index("ix_documents_tag_id", "tag_id"),
        sa.Index("ix_documents_created_at", sa.text("created_at DESC")),
        sa.Index(
            "ix_documents_needs_review",
            "needs_review",
            postgresql_where=sa.text("needs_review"),
        ),
        sa.Index("ix_documents_metadata", "metadata", postgresql_using="gin"),
    )

    sha256: Mapped[str] = mapped_column(sa.CHAR(64), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    blob_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    tag_id: Mapped[UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("tags.id"), nullable=True)
    tag_version: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status"),
        nullable=False,
        server_default=sa.text("'received'"),
    )
    current_step: Mapped[PipelineStep | None] = mapped_column(
        str_enum(PipelineStep, "pipeline_step"), nullable=True
    )
    document_confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 3), nullable=True)
    needs_review: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source: Mapped[DocumentSource] = mapped_column(
        str_enum(DocumentSource, "document_source"), nullable=False
    )
    uploaded_by: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class DocumentStep(UuidPkMixin, TimestampMixin, Base):
    """Per-step execution state for one document; makes reprocessing possible."""

    __tablename__ = "document_steps"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "step", name="uq_document_steps_document_id_step"),
        sa.Index("ix_document_steps_status_step", "status", "step"),
    )

    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[PipelineStep] = mapped_column(
        str_enum(PipelineStep, "pipeline_step"), nullable=False
    )
    status: Mapped[StepStatus] = mapped_column(
        str_enum(StepStatus, "step_status"),
        nullable=False,
        server_default=sa.text("'pending'"),
    )
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3")
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    output_ref: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )


class DocumentEvent(CreatedOnlyMixin, Base):
    """Append-only audit log entry; rows are never updated or deleted."""

    __tablename__ = "document_events"
    __table_args__ = (
        sa.Index("ix_document_events_document_id_created_at", "document_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id"), nullable=False
    )
    step: Mapped[PipelineStep | None] = mapped_column(
        str_enum(PipelineStep, "pipeline_step"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class DocumentPage(UuidPkMixin, CreatedOnlyMixin, Base):
    """Lightweight page index; text bodies live in MongoDB."""

    __tablename__ = "document_pages"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "page", name="uq_document_pages_document_id_page"),
    )

    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    blob_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    thumb_key: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    width: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    text_source: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    char_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 3), nullable=True)
