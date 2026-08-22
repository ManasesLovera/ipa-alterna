"""Pydantic request/response models for the documents API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ipa.core.enums import DocumentStatus, PipelineStep, StepStatus


class UploadPayload(BaseModel):
    """A single file to ingest."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    data: bytes
    mime_type: str | None = None
    tag_id: UUID | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: Literal["ui", "api"] = "api"
    uploaded_by: str | None = None


class IngestResult(BaseModel):
    """Outcome of one upload."""

    model_config = ConfigDict(extra="forbid")

    document: DocumentRead
    deduplicated: bool
    http_status: int


class DocumentRead(BaseModel):
    """A document resource returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    sha256: str
    original_filename: str
    mime_type: str
    size_bytes: int
    blob_key: str
    page_count: int | None
    tag_id: UUID | None
    tag_version: int | None
    status: DocumentStatus
    current_step: PipelineStep | None
    document_confidence: float | None
    needs_review: bool
    title: str | None
    trace_id: str | None
    error_code: str | None
    error_detail: str | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class StepRead(BaseModel):
    """Per-step execution state."""

    model_config = ConfigDict(extra="forbid")

    step: PipelineStep
    status: StepStatus
    attempt: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    error_detail: str | None
    metrics: dict[str, Any]


class EventRead(BaseModel):
    """One audit-log entry."""

    model_config = ConfigDict(extra="forbid")

    id: int
    event_type: str
    step: PipelineStep | None
    payload: dict[str, Any] | None
    actor: str | None
    created_at: datetime


class PageRead(BaseModel):
    """One page index entry."""

    model_config = ConfigDict(extra="forbid")

    page: int
    blob_key: str
    thumb_key: str | None
    width: int | None
    height: int | None
    text_source: str | None
    image_url: str | None
    thumbnail_url: str | None


class ContentRead(BaseModel):
    """OCR text for a document or a single page."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    pages: list[PageTextRead]


class PageTextRead(BaseModel):
    """One page's OCR text with provenance."""

    model_config = ConfigDict(extra="forbid")

    page: int
    text: str
    source: str | None
    confidence: float | None


class ExtractionRead(BaseModel):
    """A document's extraction version."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    version: int
    tag_id: UUID
    tag_version: int
    source: str
    model: str | None
    document_confidence: float
    fields: list[dict[str, Any]]
    created_at: datetime


class DocumentFilters(BaseModel):
    """Filters applied when listing documents."""

    model_config = ConfigDict(extra="forbid")

    status: DocumentStatus | None = None
    tag_id: UUID | None = None
    needs_review: bool | None = None
    q: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class PageParams(BaseModel):
    """Cursor-style pagination parameters."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=50, ge=1, le=500)
    cursor: str | None = None


class DocumentPatch(BaseModel):
    """Editable document columns."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    tag_id: UUID | None = None
    metadata: dict[str, Any] | None = None


DocumentRead.model_rebuild()
