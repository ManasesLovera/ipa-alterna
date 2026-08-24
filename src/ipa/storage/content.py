"""MongoDB content store for OCR page text and versioned extraction bodies.

Mongo holds only derivable content (rebuildable by reprocessing); PostgreSQL
remains the single source of truth for state. Three collections:

- `pages`: one document per `(document_id, page)` pair.
- `extractions`: append-only, unique on `(document_id, version)`.
- `raw_responses`: verbatim provider output for debugging, TTL'd to 30 days —
  the only place a malformed model response survives.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from ipa.contracts.models import ExtractedField, ExtractionRecord, PageText
from ipa.core.config import MongoSettings, get_settings
from ipa.core.errors import ConflictError, IpaError

logger = structlog.get_logger(__name__)

RAW_RESPONSE_TTL_S = 30 * 24 * 60 * 60
"""Raw provider responses expire after 30 days."""

UPSERT_BATCH_SIZE = 50
"""Page upserts issued concurrently per batch; bounds motor's pool usage."""


def _content_error(operation: str, exc: Exception) -> IpaError:
    """Translate a MongoDB failure into an `IpaError`.

    Args:
        operation: Name of the failing operation, included in the message.
        exc: The original driver exception.

    Returns:
        The error to raise.
    """
    return IpaError(f"MongoDB {operation} failed: {exc}", code="content_store_error")


class MongoContentStore:
    """`ContentStore` over MongoDB via motor."""

    def __init__(
        self,
        *,
        client: AsyncIOMotorClient[dict[str, Any]] | None = None,
        settings: MongoSettings | None = None,
        database: str | None = None,
    ) -> None:
        """Initialise the store.

        Args:
            client: Pre-built motor client; constructed from `settings.uri`
                when None (tests inject `mongomock-motor` here).
            settings: Mongo settings; the process settings when None.
            database: Database name override; `settings.database` when None.
        """
        self._settings = settings if settings is not None else get_settings().mongo
        self._client = client if client is not None else AsyncIOMotorClient(self._settings.uri)
        database_name = database if database is not None else self._settings.database
        self._db = self._client[database_name]
        self._pages = self._db.get_collection("pages")
        self._extractions = self._db.get_collection("extractions")
        self._raw_responses = self._db.get_collection("raw_responses")

    def close(self) -> None:
        """Close the underlying client. Call once on process shutdown.

        Returns:
            None.
        """
        self._client.close()

    async def ping(self) -> None:
        """Ping the server; used by readiness checks.

        Returns:
            None.

        Raises:
            Exception: Any driver error, surfaced by the health checker.
        """
        await self._db.command("ping")

    async def ensure_indexes(self) -> None:
        """Create every collection index. Idempotent; call at startup.

        Returns:
            None.

        Raises:
            IpaError: If index creation fails.
        """
        try:
            await self._pages.create_index(
                [("document_id", ASCENDING), ("page", ASCENDING)],
                unique=True,
                name="uniq_document_page",
            )
            await self._extractions.create_index(
                [("document_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="uniq_document_version",
            )
            await self._extractions.create_index(
                [("document_id", ASCENDING)], name="idx_document_id"
            )
            await self._raw_responses.create_index(
                [("document_id", ASCENDING), ("step", ASCENDING)],
                name="idx_document_step",
            )
            await self._raw_responses.create_index(
                [("created_at", ASCENDING)],
                expireAfterSeconds=RAW_RESPONSE_TTL_S,
                name="ttl_30d",
            )
        except Exception as exc:
            raise _content_error("ensure_indexes", exc) from exc

    async def put_pages(self, document_id: UUID, pages: list[PageText]) -> None:
        """Upsert every page's text for a document.

        Idempotent: keyed on `(document_id, page)`, so a retried step overwrites
        rather than duplicates.

        Args:
            document_id: Owning document.
            pages: Page texts to write.

        Returns:
            None.

        Raises:
            IpaError: If the bulk write fails.
        """
        if not pages:
            return
        document = str(document_id)
        now = datetime.now(UTC)

        async def _upsert(page: PageText) -> None:
            """Replace one page document, inserting it when absent."""
            await self._pages.replace_one(
                {"document_id": document, "page": page.page},
                {
                    "document_id": document,
                    "page": page.page,
                    "text": page.text,
                    "source": page.source,
                    "confidence": page.confidence,
                    "char_count": page.char_count,
                    "created_at": now,
                },
                upsert=True,
            )

        # Fan out as concurrent replace_one calls rather than bulk_write:
        # pymongo >= 4.15 operation objects pass keyword arguments (let, sort)
        # that mongomock's bulk builder does not accept yet, and the fan-out is
        # equivalent — one idempotent upsert per (document_id, page).
        #
        # Batched, not one gather over every page: a 1,000-page document would
        # otherwise issue 1,000 simultaneous operations and exhaust motor's
        # connection pool (default maxPoolSize=100).
        try:
            for start in range(0, len(pages), UPSERT_BATCH_SIZE):
                batch = pages[start : start + UPSERT_BATCH_SIZE]
                await asyncio.gather(*(_upsert(page) for page in batch))
        except Exception as exc:
            raise _content_error("put_pages", exc) from exc

    async def get_pages(self, document_id: UUID) -> list[PageText]:
        """Return a document's pages ordered by page number.

        Args:
            document_id: Owning document.

        Returns:
            The pages, or an empty list when OCR has not run.

        Raises:
            IpaError: If the read fails.
        """
        try:
            cursor = self._pages.find({"document_id": str(document_id)}).sort("page", ASCENDING)
            return [
                PageText(
                    page=doc["page"],
                    text=doc["text"],
                    source=doc["source"],
                    confidence=doc.get("confidence"),
                    char_count=doc["char_count"],
                )
                async for doc in cursor
            ]
        except Exception as exc:
            raise _content_error("get_pages", exc) from exc

    async def put_extraction(self, record: ExtractionRecord) -> str:
        """Append a new extraction version.

        Args:
            record: The version to store; never updates an existing one.

        Returns:
            The storage identifier of the inserted record.

        Raises:
            ConflictError: If `(document_id, version)` already exists.
            IpaError: If the insert fails.
        """
        duplicate = (
            f"Extraction version {record.version} already exists for document {record.document_id}."
        )
        document = {
            "document_id": str(record.document_id),
            "version": record.version,
            "tag_id": str(record.tag_id),
            "tag_version": record.tag_version,
            "source": record.source,
            "model": record.model,
            "prompt_hash": record.prompt_hash,
            "fields": [field.model_dump() for field in record.fields],
            "document_confidence": record.document_confidence,
            "created_at": record.created_at,
            "created_by": record.created_by,
        }
        try:
            existing = await self._extractions.find_one(
                {"document_id": document["document_id"], "version": record.version}, {"_id": 1}
            )
            if existing is not None:
                raise ConflictError(duplicate)
            result = await self._extractions.insert_one(document)
            return str(result.inserted_id)
        except DuplicateKeyError as exc:  # race: another writer inserted first
            raise ConflictError(duplicate) from exc
        except ConflictError:
            raise
        except Exception as exc:
            raise _content_error("put_extraction", exc) from exc

    async def get_extraction(
        self, document_id: UUID, version: int | None = None
    ) -> ExtractionRecord | None:
        """Return one extraction version.

        Args:
            document_id: Owning document.
            version: Version to fetch; the latest when None.

        Returns:
            The record, or None when the document has no extraction.

        Raises:
            IpaError: If the read fails.
        """
        query: dict[str, Any] = {"document_id": str(document_id)}
        if version is not None:
            query["version"] = version
        try:
            doc = await self._extractions.find_one(query, sort=[("version", DESCENDING)])
            if doc is None:
                return None
            return self._record_from_doc(doc)
        except Exception as exc:
            raise _content_error("get_extraction", exc) from exc

    async def list_extractions(self, document_id: UUID) -> list[ExtractionRecord]:
        """Return every extraction version for a document, newest first.

        Args:
            document_id: Owning document.

        Returns:
            All stored versions, ordered by descending version.

        Raises:
            IpaError: If the read fails.
        """
        try:
            cursor = self._extractions.find({"document_id": str(document_id)}).sort(
                "version", DESCENDING
            )
            return [self._record_from_doc(doc) async for doc in cursor]
        except Exception as exc:
            raise _content_error("list_extractions", exc) from exc

    async def list_current_extractions(self, tag_id: UUID) -> list[ExtractionRecord]:
        """Return the current extraction version of every document of a tag.

        Args:
            tag_id: Owning tag.

        Returns:
            The latest extraction per document for the tag.

        Raises:
            IpaError: If the read fails.
        """
        try:
            cursor = self._extractions.aggregate(
                [
                    {"$match": {"tag_id": str(tag_id)}},
                    {"$sort": {"document_id": 1, "version": -1}},
                    {
                        "$group": {
                            "_id": "$document_id",
                            "doc": {"$first": "$$ROOT"},
                        }
                    },
                ]
            )
            return [self._record_from_doc(item["doc"]) async for item in cursor]
        except Exception as exc:
            raise _content_error("list_current_extractions", exc) from exc

    async def put_raw_response(
        self,
        document_id: UUID,
        step: str,
        model: str,
        response_text: str,
        request_summary: str | None = None,
    ) -> None:
        """Persist a provider's verbatim response for debugging.

        Stored with a 30-day TTL (see `ensure_indexes`) so the collection does
        not grow without bound.

        Args:
            document_id: Owning document.
            step: Pipeline step that made the call, e.g. `"ocr"` or `"extract"`.
            model: Provider model identifier that produced the response.
            response_text: The unparsed response body.
            request_summary: Short description of the request, excluding secrets.

        Returns:
            None.

        Raises:
            IpaError: If the insert fails.
        """
        try:
            await self._raw_responses.insert_one(
                {
                    "document_id": str(document_id),
                    "step": step,
                    "model": model,
                    "request_summary": request_summary,
                    "response_text": response_text,
                    "created_at": datetime.now(UTC),
                }
            )
        except Exception as exc:
            raise _content_error("put_raw_response", exc) from exc

    async def delete_document(self, document_id: UUID) -> None:
        """Delete all content for a document. Rebuildable by reprocessing.

        Args:
            document_id: Owning document.

        Returns:
            None.

        Raises:
            IpaError: If any delete fails.
        """
        document = str(document_id)
        try:
            await self._pages.delete_many({"document_id": document})
            await self._extractions.delete_many({"document_id": document})
            await self._raw_responses.delete_many({"document_id": document})
        except Exception as exc:
            raise _content_error("delete_document", exc) from exc

    @staticmethod
    def _record_from_doc(doc: dict[str, Any]) -> ExtractionRecord:
        """Rebuild an `ExtractionRecord` from its stored document.

        Args:
            doc: Stored Mongo document.

        Returns:
            The equivalent DTO. BSON datetimes come back naive-UTC; they are
            re-anchored to UTC so records compare equal to what was stored.
        """
        created_at = doc["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return ExtractionRecord(
            document_id=UUID(doc["document_id"]),
            version=int(doc["version"]),
            tag_id=UUID(doc["tag_id"]),
            tag_version=int(doc["tag_version"]),
            source=doc["source"],
            model=doc.get("model"),
            prompt_hash=doc.get("prompt_hash"),
            fields=[ExtractedField(**field) for field in doc.get("fields", [])],
            document_confidence=float(doc["document_confidence"]),
            created_at=created_at,
            created_by=doc.get("created_by"),
        )
