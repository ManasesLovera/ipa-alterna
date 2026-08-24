"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class SearchHit(BaseModel):
    """A single search result with full provenance."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    page: int | None = None
    title: str | None = None
    text: str
    score: float


class DocumentSummary(BaseModel):
    """A short document descriptor for field-search results."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str | None = None
    status: str | None = None


class DocumentDetail(BaseModel):
    """A document's metadata and status."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str | None = None
    filename: str
    status: str
    tag: str | None = None
    page_count: int | None = None
    confidence: float | None = None


class ExtractionResult(BaseModel):
    """A document's structured extraction."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version: int
    fields: list[dict[str, Any]]


class TagSummary(BaseModel):
    """A tag and its fields."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    fields: list[str]
