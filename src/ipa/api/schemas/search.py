"""Pydantic request/response models for search and RAG."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ipa.contracts.models import SearchFilters


class SearchRequest(BaseModel):
    """Body for a hybrid/dense/sparse search."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid"
    top_k: int = Field(default=10, ge=1, le=500)
    filters: SearchFilters | None = None
    group_by_document: bool = False


class ChunkHitRead(BaseModel):
    """One scored chunk in a search response."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    chunk_index: int
    text: str
    score: float
    page_from: int | None = None
    page_to: int | None = None
    document_title: str | None = None
    tag_slug: str | None = None
    page_image_url: str | None = None


class SearchResponse(BaseModel):
    """The result of a search."""

    model_config = ConfigDict(extra="forbid")

    hits: list[ChunkHitRead]
    grouped: list[GroupedHit] = Field(default_factory=list)
    mode: str


class GroupedHit(BaseModel):
    """A document with its best chunks nested."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    document_title: str | None = None
    best_score: float
    chunks: list[ChunkHitRead]


class FieldQueryOperator(BaseModel):
    """A comparison operator applied to one field."""

    model_config = ConfigDict(extra="forbid")

    eq: Any | None = None
    neq: Any | None = None
    gte: Any | None = None
    lte: Any | None = None
    contains: Any | None = None
    in_: list[Any] | None = Field(default=None, alias="in")
    exists: bool | None = None


class FieldQuery(BaseModel):
    """Body for structured search over extracted fields."""

    model_config = ConfigDict(extra="forbid")

    tag_id: UUID
    where: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=50, ge=1, le=200)


class RagRequest(BaseModel):
    """Body for a grounded question-answering request."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=50)
    filters: SearchFilters | None = None
    include_sources: bool = True


class RagSource(BaseModel):
    """A citation resolved from the model's answer."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    page: int
    document_title: str | None = None
    page_image_url: str | None = None


class RagResponse(BaseModel):
    """The result of a RAG query."""

    model_config = ConfigDict(extra="forbid")

    answer: str | None = None
    reason: str | None = None
    sources: list[RagSource] = Field(default_factory=list)
    used_chunks: int = 0


class SimilarRequest(BaseModel):
    """Body for nearest-neighbour document search."""

    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=5, ge=1, le=50)


SearchResponse.model_rebuild()
SearchFilters.model_rebuild()
