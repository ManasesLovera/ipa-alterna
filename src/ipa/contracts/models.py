"""Shared data transfer objects.

These pydantic models cross every layer boundary in the platform: adapters
return them, services consume them, the API serialises them. They are pure data
— no I/O, no persistence concerns, no ORM types.

Field names are part of the public contract. Adding an optional field is safe;
renaming or removing one is a breaking change for every downstream task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ipa.core.enums import DocumentStatus, ExtractionSource, PipelineStep, StepStatus

TextSource = Literal["text_layer", "vlm", "tesseract"]
"""Where a page's text came from, in OCR resolution order."""

OcrSource = Literal["vlm", "tesseract"]
"""Where an OCR result came from. The native text layer never goes through OCR."""


class IpaModel(BaseModel):
    """Base for every shared DTO: strict about unknown fields, safe to copy."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ImageRef(IpaModel):
    """A reference to a rendered page image held in the blob store."""

    page: int = Field(ge=1, description="1-based page number within the document.")
    blob_key: str = Field(description="Blob store key, see the blob key scheme.")
    mime: str = Field(description="MIME type of the stored image, e.g. image/png.")


class PageText(IpaModel):
    """The text of a single page, with its provenance."""

    page: int = Field(ge=1, description="1-based page number within the document.")
    text: str
    source: TextSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    char_count: int = Field(ge=0)


class OcrResult(IpaModel):
    """The output of one OCR provider call for one image."""

    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: OcrSource


class LlmJsonResult(IpaModel):
    """A structured completion: parsed JSON plus the accounting around it."""

    data: dict[str, Any]
    raw_text: str = Field(description="Verbatim model output, persisted for debugging.")
    model: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    repaired: bool = Field(
        default=False,
        description="True when the first response failed schema validation and was repaired.",
    )


class ExtractedField(IpaModel):
    """One field extracted from a document, with evidence and validation state."""

    key: str = Field(description="Field key as defined on the tag.")
    value: Any = None
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = Field(default=None, ge=1)
    evidence: str | None = Field(
        default=None, description="Verbatim snippet from the document supporting the value."
    )
    valid: bool = True
    validation_errors: list[str] = Field(default_factory=list)


class ExtractionRecord(IpaModel):
    """One append-only version of a document's extracted content."""

    document_id: UUID
    version: int = Field(ge=1, description="Monotonic per document; never updated in place.")
    tag_id: UUID
    tag_version: int = Field(ge=1)
    source: ExtractionSource
    model: str | None = Field(default=None, description="None when source is HUMAN.")
    prompt_hash: str | None = None
    fields: list[ExtractedField] = Field(default_factory=list)
    document_confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime
    created_by: str | None = None


class ChunkVector(IpaModel):
    """An embedded text chunk ready to be written to the vector store."""

    document_id: UUID
    chunk_index: int = Field(
        ge=-1,
        description=(
            "0-based position within the document. -1 is reserved for the "
            "per-document extraction-summary chunk synthesised from extracted fields."
        ),
    )
    text: str
    embedding: list[float]
    embed_model: str
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def embed_dim(self) -> int:
        """Return the dimensionality of the embedding."""
        return len(self.embedding)


class SearchFilters(IpaModel):
    """Optional narrowing applied to a vector or field search. None means no filter."""

    tag_ids: list[UUID] | None = None
    document_ids: list[UUID] | None = None
    statuses: list[DocumentStatus] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class ChunkHit(IpaModel):
    """One scored chunk returned by the vector store."""

    document_id: UUID
    chunk_index: int = Field(
        ge=-1, description="-1 identifies the extraction-summary chunk."
    )
    text: str
    score: float = Field(description="Higher is more similar; comparable within one result set.")
    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    document_title: str | None = None


class StepContext(IpaModel):
    """Everything a pipeline step handler needs to run one attempt."""

    document_id: UUID
    step: PipelineStep
    attempt: int = Field(ge=1, description="1-based attempt counter for this step.")
    trace_id: str | None = Field(
        default=None, description="Trace propagated from upload so a document is one trace."
    )


class StepResult(IpaModel):
    """The outcome of one pipeline step attempt."""

    status: StepStatus
    detail: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    next_step_override: PipelineStep | None = Field(
        default=None, description="Set to divert the pipeline away from the default STEP_ORDER."
    )
