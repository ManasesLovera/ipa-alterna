"""MongoContentStore behaviour against mongomock-motor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient

from ipa.contracts.models import ExtractedField, ExtractionRecord, PageText
from ipa.contracts.protocols import ContentStore
from ipa.core.errors import ConflictError
from ipa.storage.content import RAW_RESPONSE_TTL_S, MongoContentStore


@pytest.fixture
def store() -> MongoContentStore:
    """Return a store backed by an in-memory mongomock client."""
    client = AsyncMongoMockClient()
    return MongoContentStore(
        client=cast(AsyncIOMotorClient[dict[str, Any]], client), database="ipa_test"
    )


def make_record(document_id: UUID, version: int) -> ExtractionRecord:
    """Build an extraction version for tests.

    Args:
        document_id: Owning document.
        version: Extraction version.

    Returns:
        A complete `ExtractionRecord`.
    """
    return ExtractionRecord(
        document_id=document_id,
        version=version,
        tag_id=uuid4(),
        tag_version=1,
        source="model",
        model="fake/llm",
        prompt_hash="abc123",
        fields=[
            ExtractedField(key="total", value=42, confidence=0.9, page=1, evidence="Total: 42")
        ],
        document_confidence=0.88,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="pipeline:extract",
    )


def make_pages() -> list[PageText]:
    """Build three pages of text out of order.

    Returns:
        Pages numbered 3, 1, 2.
    """
    return [
        PageText(page=3, text="third", source="vlm", confidence=0.7, char_count=5),
        PageText(page=1, text="first", source="text_layer", confidence=None, char_count=5),
        PageText(page=2, text="second", source="tesseract", confidence=0.5, char_count=6),
    ]


async def test_store_satisfies_content_store_protocol(store: MongoContentStore) -> None:
    assert isinstance(store, ContentStore)


async def test_ensure_indexes_is_idempotent(store: MongoContentStore) -> None:
    await store.ensure_indexes()
    await store.ensure_indexes()

    indexes = await store._raw_responses.index_information()
    assert "ttl_30d" in indexes
    assert indexes["ttl_30d"].get("expireAfterSeconds") == RAW_RESPONSE_TTL_S


async def test_page_round_trip_preserves_fields_and_order(store: MongoContentStore) -> None:
    document_id = uuid4()

    await store.put_pages(document_id, make_pages())
    pages = await store.get_pages(document_id)

    assert [page.page for page in pages] == [1, 2, 3]
    assert pages[0].text == "first"
    assert pages[0].source == "text_layer"
    assert pages[0].confidence is None
    assert pages[2].source == "vlm"


async def test_put_pages_is_an_idempotent_upsert(store: MongoContentStore) -> None:
    document_id = uuid4()

    await store.put_pages(document_id, make_pages())
    await store.put_pages(
        document_id,
        [PageText(page=1, text="rewritten", source="vlm", confidence=0.9, char_count=9)],
    )
    pages = await store.get_pages(document_id)

    # Page 1 overwritten in place, no duplicates, siblings untouched.
    assert [page.page for page in pages] == [1, 2, 3]
    assert pages[0].text == "rewritten"
    assert pages[0].source == "vlm"


async def test_get_pages_empty_when_ocr_has_not_run(store: MongoContentStore) -> None:
    assert await store.get_pages(uuid4()) == []


async def test_extraction_round_trip_latest_and_explicit_version(
    store: MongoContentStore,
) -> None:
    document_id = uuid4()
    first = make_record(document_id, version=1)
    second = make_record(document_id, version=2)

    await store.put_extraction(first)
    await store.put_extraction(second)

    assert await store.get_extraction(document_id) == second
    assert await store.get_extraction(document_id, version=1) == first
    assert await store.get_extraction(document_id, version=3) is None
    assert await store.list_extractions(document_id) == [second, first]


async def test_duplicate_extraction_version_conflicts(store: MongoContentStore) -> None:
    document_id = uuid4()
    await store.put_extraction(make_record(document_id, version=1))

    with pytest.raises(ConflictError):
        await store.put_extraction(make_record(document_id, version=1))


async def test_put_raw_response_stores_verbatim_text(store: MongoContentStore) -> None:
    document_id = uuid4()

    await store.put_raw_response(
        document_id,
        step="extract",
        model="fake/llm",
        response_text='{"partially malformed": ',
        request_summary="extract fields for tag invoices v3",
    )

    doc = await store._raw_responses.find_one({"document_id": str(document_id)})
    assert doc is not None
    assert doc["step"] == "extract"
    assert doc["model"] == "fake/llm"
    assert doc["response_text"] == '{"partially malformed": '
    assert doc["request_summary"] == "extract fields for tag invoices v3"
    assert isinstance(doc["created_at"], datetime)


async def test_delete_document_clears_every_collection(store: MongoContentStore) -> None:
    document_id = uuid4()
    await store.put_pages(document_id, make_pages())
    await store.put_extraction(make_record(document_id, version=1))
    await store.put_raw_response(document_id, "ocr", "fake/vlm", "raw")

    await store.delete_document(document_id)

    assert await store.get_pages(document_id) == []
    assert await store.list_extractions(document_id) == []
    assert await store._raw_responses.count_documents({"document_id": str(document_id)}) == 0
