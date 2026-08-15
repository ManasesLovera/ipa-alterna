"""DTOs returned by repositories.

Nothing in `src/ipa/db/` may leak an ORM object; repositories convert rows to
these immutable pydantic models before returning them. Field names mirror the
database columns so the mapping is mechanical.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ipa.core.enums import (
    DocumentStatus,
    ExtractionSource,
    FieldType,
    PipelineStep,
    StepStatus,
)
from ipa.db.enums import DocumentSource, UserRole, ValidationDecision, WebhookDeliveryStatus


class DbDto(BaseModel):
    """Base for every DTO: built from ORM rows, strict about unknown fields."""

    model_config = ConfigDict(
        from_attributes=True, extra="forbid", frozen=True, populate_by_name=True
    )


class UserDto(DbDto):
    """A human account row."""

    id: UUID
    email: str
    full_name: str
    password_hash: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ApiKeyDto(DbDto):
    """A machine credential row."""

    id: UUID
    name: str
    key_hash: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TagFieldCreate(BaseModel):
    """Input for one field when creating or rewriting a tag's schema."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    field_type: FieldType
    description: str | None = None
    is_required: bool = False
    position: int
    enum_values: list[str] | None = None
    regex: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    item_type: str | None = None
    object_schema: dict[str, Any] | None = None


class TagDto(DbDto):
    """A tag row."""

    id: UUID
    slug: str
    name: str
    description: str
    version: int
    is_active: bool
    auto_approve_threshold: float | None
    prompt_template: str | None
    llm_model: str | None
    classification_hints: str | None
    created_at: datetime
    updated_at: datetime


class TagFieldDto(DbDto):
    """A tag field row."""

    id: UUID
    tag_id: UUID
    key: str
    label: str
    field_type: FieldType
    description: str | None
    is_required: bool
    position: int
    enum_values: list[str] | None
    regex: str | None
    min_value: float | None
    max_value: float | None
    min_length: int | None
    max_length: int | None
    item_type: str | None
    object_schema: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class TagVersionDto(DbDto):
    """An immutable tag snapshot row."""

    id: UUID
    tag_id: UUID
    version: int
    snapshot: dict[str, Any]
    created_at: datetime


class DocumentDto(DbDto):
    """A document row."""

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
    source: DocumentSource
    uploaded_by: str | None
    trace_id: str | None
    error_code: str | None
    error_detail: str | None
    metadata: dict[str, Any] = Field(alias="meta")
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    deleted_at: datetime | None


class DocumentPatch(BaseModel):
    """Mutable document columns; only fields explicitly set are updated."""

    model_config = ConfigDict(extra="forbid")

    page_count: int | None = None
    tag_id: UUID | None = None
    tag_version: int | None = None
    status: DocumentStatus | None = None
    current_step: PipelineStep | None = None
    document_confidence: float | None = None
    needs_review: bool | None = None
    title: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    completed_at: datetime | None = None


class DocumentStepDto(DbDto):
    """A per-step execution state row."""

    id: UUID
    document_id: UUID
    step: PipelineStep
    status: StepStatus
    attempt: int
    max_attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    error_detail: str | None
    output_ref: str | None
    metrics: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DocumentEventDto(DbDto):
    """An append-only audit log row."""

    id: int
    document_id: UUID
    step: PipelineStep | None
    event_type: str
    payload: dict[str, Any] | None
    actor: str | None
    trace_id: str | None
    created_at: datetime


class DocumentPageDto(DbDto):
    """A page index row."""

    id: UUID
    document_id: UUID
    page: int
    blob_key: str
    thumb_key: str | None
    width: int | None
    height: int | None
    text_source: str | None
    char_count: int | None
    ocr_confidence: float | None
    created_at: datetime


class ExtractionVersionDto(DbDto):
    """An extraction version pointer row."""

    id: UUID
    document_id: UUID
    version: int
    tag_id: UUID
    tag_version: int
    source: ExtractionSource
    model: str | None
    prompt_hash: str | None
    document_confidence: float
    mongo_id: str
    is_current: bool
    created_by: str | None
    created_at: datetime


class ValidationDto(DbDto):
    """A validation decision row."""

    id: UUID
    document_id: UUID
    extraction_version_id: UUID
    decision: ValidationDecision
    reviewer_id: UUID | None
    notes: str | None
    corrected_fields: dict[str, Any] | None
    auto: bool
    created_at: datetime


class WebhookDto(DbDto):
    """A webhook subscription row."""

    id: UUID
    url: str
    secret: str
    events: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryDto(DbDto):
    """A webhook delivery attempt row."""

    id: int
    webhook_id: UUID
    event_type: str
    payload: dict[str, Any] | None
    status: WebhookDeliveryStatus
    attempt: int
    response_status: int | None
    error: str | None
    created_at: datetime
    delivered_at: datetime | None


class IdempotencyKeyDto(DbDto):
    """A durable idempotency key row backing the Redis claim."""

    id: UUID
    key: str
    endpoint: str
    request_hash: str
    response_status: int | None
    response_body: dict[str, Any] | None
    created_at: datetime
    expires_at: datetime


class ChunkDto(DbDto):
    """A chunk row."""

    id: UUID
    document_id: UUID
    chunk_index: int
    text: str
    embed_model: str
    embed_dim: int
    page_from: int | None
    page_to: int | None
    token_count: int | None
    metadata: dict[str, Any] = Field(alias="meta")
    created_at: datetime
