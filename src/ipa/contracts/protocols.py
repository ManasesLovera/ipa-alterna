"""Structural interfaces for every replaceable dependency.

Services depend on these protocols, never on concrete adapters. Adapters in
`ipa.storage` and `ipa.providers` satisfy them structurally — there is no base
class to inherit and no registration step. Unit tests substitute fakes that
implement the same shape.

All methods are `async`: every implementation performs network I/O.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from ipa.contracts.models import (
    ChunkHit,
    ChunkVector,
    ExtractionRecord,
    ImageRef,
    LlmJsonResult,
    OcrResult,
    PageText,
    SearchFilters,
)


@runtime_checkable
class BlobStore(Protocol):
    """Object storage for original uploads, page images and thumbnails."""

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under `key`, overwriting any existing object.

        Args:
            key: Blob key, built with `ipa.core.ids`.
            data: Raw object bytes.
            content_type: MIME type recorded on the object.

        Returns:
            The key the object was stored under.

        Raises:
            IpaError: If the object store rejects the write.
        """
        ...

    async def get(self, key: str) -> bytes:
        """Fetch an object's bytes.

        Args:
            key: Blob key.

        Returns:
            The object bytes.

        Raises:
            NotFoundError: If no object exists under `key`.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Report whether an object exists.

        Args:
            key: Blob key.

        Returns:
            True when the object is present.
        """
        ...

    async def presigned_url(self, key: str, expires_s: int = 900) -> str:
        """Return a time-limited download URL for an object.

        Args:
            key: Blob key.
            expires_s: Lifetime of the URL in seconds.

        Returns:
            A presigned HTTP URL.
        """
        ...

    async def delete(self, key: str) -> None:
        """Delete an object, succeeding if it is already absent.

        Args:
            key: Blob key.

        Returns:
            None.
        """
        ...


@runtime_checkable
class ContentStore(Protocol):
    """MongoDB-backed store for OCR text and extracted JSON."""

    async def put_pages(self, document_id: UUID, pages: list[PageText]) -> None:
        """Upsert every page's text for a document.

        Idempotent: keyed on `(document_id, page)`, so a retried step overwrites
        rather than duplicates.

        Args:
            document_id: Owning document.
            pages: Page texts to write.

        Returns:
            None.
        """
        ...

    async def get_pages(self, document_id: UUID) -> list[PageText]:
        """Return a document's pages ordered by page number.

        Args:
            document_id: Owning document.

        Returns:
            The pages, or an empty list when OCR has not run.
        """
        ...

    async def put_extraction(self, record: ExtractionRecord) -> str:
        """Append a new extraction version.

        Args:
            record: The version to store; never updates an existing one.

        Returns:
            The storage identifier of the inserted record.

        Raises:
            ConflictError: If `(document_id, version)` already exists.
        """
        ...

    async def get_extraction(
        self, document_id: UUID, version: int | None = None
    ) -> ExtractionRecord | None:
        """Return one extraction version.

        Args:
            document_id: Owning document.
            version: Version to fetch; the latest when None.

        Returns:
            The record, or None when the document has no extraction.
        """
        ...

    async def list_extractions(self, document_id: UUID) -> list[ExtractionRecord]:
        """Return every extraction version for a document, newest first.

        Args:
            document_id: Owning document.

        Returns:
            All stored versions.
        """
        ...

    async def delete_document(self, document_id: UUID) -> None:
        """Delete all content for a document. Rebuildable by reprocessing.

        Args:
            document_id: Owning document.

        Returns:
            None.
        """
        ...


@runtime_checkable
class CacheStore(Protocol):
    """Redis-backed cache, idempotency registry and distributed lock."""

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Read a cached JSON value.

        Args:
            key: Cache key.

        Returns:
            The decoded value, or None on a miss.
        """
        ...

    async def set_json(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        """Cache a JSON value with an expiry.

        Args:
            key: Cache key.
            value: JSON-serialisable value.
            ttl_s: Time to live in seconds.

        Returns:
            None.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove a key, succeeding if it is already absent.

        Args:
            key: Cache key.

        Returns:
            None.
        """
        ...

    async def claim_idempotency(self, key: str, ttl_s: int) -> bool:
        """Atomically claim an idempotency key.

        Args:
            key: Idempotency key, typically from the `Idempotency-Key` header.
            ttl_s: How long the claim is held.

        Returns:
            True when this caller won the claim, False when it was already held.
        """
        ...

    async def lock(self, key: str, ttl_s: int) -> AbstractAsyncContextManager[bool]:
        """Return an async context manager holding a distributed lock.

        Args:
            key: Lock name.
            ttl_s: Lock lease in seconds; the lock auto-expires.

        Returns:
            A context manager yielding True when the lock was acquired.
        """
        ...


@runtime_checkable
class LlmProvider(Protocol):
    """Chat model that returns JSON conforming to a supplied schema."""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        images: list[ImageRef] | None = None,
        model: str | None = None,
    ) -> LlmJsonResult:
        """Run a structured completion and return validated JSON.

        Args:
            system: System prompt.
            user: User prompt.
            json_schema: JSON Schema the response must satisfy.
            images: Page images to attach; selects the vision model when given.
            model: Overrides the configured model ID.

        Returns:
            The parsed JSON plus the raw text and token accounting.

        Raises:
            ProviderError: If the response cannot be parsed or repaired.
            RetryableProviderError: On timeouts, rate limits or 5xx responses.
        """
        ...


@runtime_checkable
class OcrProvider(Protocol):
    """Transcribes a single page image to text."""

    async def ocr_image(self, image: bytes, mime: str) -> OcrResult:
        """Transcribe an image.

        Args:
            image: Raw image bytes.
            mime: MIME type of the image.

        Returns:
            The transcription, its source and a confidence when available.

        Raises:
            ProviderError: If transcription fails permanently.
            RetryableProviderError: On transient upstream failures.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces embedding vectors for text."""

    @property
    def model(self) -> str:
        """Return the model ID, recorded on every stored vector."""
        ...

    @property
    def dimension(self) -> int:
        """Return the vector dimensionality this model produces."""
        ...

    async def embed(
        self, texts: list[str], *, kind: Literal["query", "passage"]
    ) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: Texts to embed.
            kind: Asymmetric embedding role; queries and passages differ.

        Returns:
            One vector per input, in the same order.

        Raises:
            ProviderError: On a permanent provider failure.
            RetryableProviderError: On transient upstream failures.
        """
        ...


@runtime_checkable
class VectorStore(Protocol):
    """pgvector-backed similarity index over document chunks."""

    async def upsert(self, chunks: list[ChunkVector]) -> None:
        """Insert or replace chunks, keyed on `(document_id, chunk_index)`.

        Args:
            chunks: Chunks with embeddings attached.

        Returns:
            None.
        """
        ...

    async def delete_document(self, document_id: UUID) -> None:
        """Remove every chunk belonging to a document.

        Args:
            document_id: Owning document.

        Returns:
            None.
        """
        ...

    async def search(
        self,
        embedding: list[float],
        *,
        query_text: str | None,
        top_k: int,
        filters: SearchFilters | None,
    ) -> list[ChunkHit]:
        """Return the most similar chunks, best first.

        Args:
            embedding: Query vector.
            query_text: Original query, for hybrid lexical scoring; may be None.
            top_k: Maximum number of hits.
            filters: Optional narrowing; None means search everything.

        Returns:
            Scored hits ordered by descending score.
        """
        ...
