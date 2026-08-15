"""Protocols are importable and satisfied structurally by conforming fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Literal
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
from ipa.contracts.protocols import (
    BlobStore,
    CacheStore,
    ContentStore,
    EmbeddingProvider,
    LlmProvider,
    OcrProvider,
    VectorStore,
)


class FakeBlobStore:
    """In-memory BlobStore used to prove the protocol is implementable."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def presigned_url(self, key: str, expires_s: int = 900) -> str:
        return f"http://blobs.test/{key}?e={expires_s}"

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeContentStore:
    """In-memory ContentStore."""

    async def put_pages(self, document_id: UUID, pages: list[PageText]) -> None:
        return None

    async def get_pages(self, document_id: UUID) -> list[PageText]:
        return []

    async def put_extraction(self, record: ExtractionRecord) -> str:
        return "id"

    async def get_extraction(
        self, document_id: UUID, version: int | None = None
    ) -> ExtractionRecord | None:
        return None

    async def list_extractions(self, document_id: UUID) -> list[ExtractionRecord]:
        return []

    async def delete_document(self, document_id: UUID) -> None:
        return None


class FakeCacheStore:
    """In-memory CacheStore."""

    async def get_json(self, key: str) -> dict[str, Any] | None:
        return None

    async def set_json(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None

    async def claim_idempotency(self, key: str, ttl_s: int) -> bool:
        return True

    async def lock(self, key: str, ttl_s: int) -> AbstractAsyncContextManager[bool]:
        @asynccontextmanager
        async def _lock() -> AsyncIterator[bool]:
            yield True

        return _lock()


class FakeLlmProvider:
    """Deterministic LlmProvider."""

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        images: list[ImageRef] | None = None,
        model: str | None = None,
    ) -> LlmJsonResult:
        return LlmJsonResult(data={}, raw_text="{}", model=model or "fake", latency_ms=1)


class FakeOcrProvider:
    """Deterministic OcrProvider."""

    async def ocr_image(self, image: bytes, mime: str) -> OcrResult:
        return OcrResult(text="", source="tesseract", confidence=0.5)


class FakeEmbeddingProvider:
    """Deterministic EmbeddingProvider."""

    @property
    def model(self) -> str:
        return "fake/embed"

    @property
    def dimension(self) -> int:
        return 3

    async def embed(
        self, texts: list[str], *, kind: Literal["query", "passage"]
    ) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]


class FakeVectorStore:
    """Deterministic VectorStore."""

    async def upsert(self, chunks: list[ChunkVector]) -> None:
        return None

    async def delete_document(self, document_id: UUID) -> None:
        return None

    async def search(
        self,
        embedding: list[float],
        *,
        query_text: str | None,
        top_k: int,
        filters: SearchFilters | None,
    ) -> list[ChunkHit]:
        return []


def test_fakes_satisfy_their_protocols() -> None:
    assert isinstance(FakeBlobStore(), BlobStore)
    assert isinstance(FakeContentStore(), ContentStore)
    assert isinstance(FakeCacheStore(), CacheStore)
    assert isinstance(FakeLlmProvider(), LlmProvider)
    assert isinstance(FakeOcrProvider(), OcrProvider)
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)
    assert isinstance(FakeVectorStore(), VectorStore)


def test_incomplete_implementation_does_not_satisfy_a_protocol() -> None:
    class Partial:
        async def put(self, key: str, data: bytes, content_type: str) -> str:
            return key

    assert not isinstance(Partial(), BlobStore)


async def test_blob_store_fake_behaves() -> None:
    store = FakeBlobStore()

    await store.put("originals/ab/abcd", b"bytes", "application/pdf")

    assert await store.exists("originals/ab/abcd") is True
    assert await store.get("originals/ab/abcd") == b"bytes"
    await store.delete("originals/ab/abcd")
    assert await store.exists("originals/ab/abcd") is False


def test_static_assignment_to_protocol_types() -> None:
    blob: BlobStore = FakeBlobStore()
    content: ContentStore = FakeContentStore()
    cache: CacheStore = FakeCacheStore()
    llm: LlmProvider = FakeLlmProvider()
    ocr: OcrProvider = FakeOcrProvider()
    embed: EmbeddingProvider = FakeEmbeddingProvider()
    vectors: VectorStore = FakeVectorStore()

    assert all(obj is not None for obj in (blob, content, cache, llm, ocr, embed, vectors))
