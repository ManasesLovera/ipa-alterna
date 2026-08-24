"""Pydantic request/response models for the review API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApproveRequest(BaseModel):
    """Body for approving a document as-is."""

    model_config = ConfigDict(extra="forbid")

    notes: str | None = None


class CorrectRequest(BaseModel):
    """Body for submitting field corrections."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any]
    expected_version: int = Field(ge=1)
    notes: str | None = None


class RejectRequest(BaseModel):
    """Body for rejecting a document."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    action: str = Field(default="quarantine", pattern="^(quarantine|reprocess)$")


class AssignTagRequest(BaseModel):
    """Body for assigning a tag to a parked document."""

    model_config = ConfigDict(extra="forbid")

    tag_id: str


class BulkApproveRequest(BaseModel):
    """Body for bulk-approving documents."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[str]


class ReviewPayload(BaseModel):
    """The one-round-trip payload a review screen needs."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    extraction: dict[str, Any] | None
    tag_schema: dict[str, Any] | None
    page_images: list[dict[str, Any]] = Field(default_factory=list)
    prev_id: str | None = None
    next_id: str | None = None
