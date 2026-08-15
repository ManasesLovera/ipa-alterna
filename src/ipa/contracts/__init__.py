"""Shared protocols and DTOs.

This package is the seam between layers: adapters implement the protocols,
services depend on them. It contains **no logic** and imports nothing from
`ipa.db`, `ipa.storage`, `ipa.providers` or `ipa.api`.
"""

from __future__ import annotations

from ipa.contracts.models import (
    ChunkHit,
    ChunkVector,
    ExtractedField,
    ExtractionRecord,
    ImageRef,
    LlmJsonResult,
    OcrResult,
    PageText,
    SearchFilters,
    StepContext,
    StepResult,
)
from ipa.contracts.protocols import (
    BlobStore,
    CacheStore,
    ContentStore,
    EmbeddingProvider,
    LlmProvider,
    OcrProvider,
    VectorStore,
)

__all__ = [
    "BlobStore",
    "CacheStore",
    "ChunkHit",
    "ChunkVector",
    "ContentStore",
    "EmbeddingProvider",
    "ExtractedField",
    "ExtractionRecord",
    "ImageRef",
    "LlmJsonResult",
    "LlmProvider",
    "OcrProvider",
    "OcrResult",
    "PageText",
    "SearchFilters",
    "StepContext",
    "StepResult",
    "VectorStore",
]
