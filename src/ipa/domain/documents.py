"""Document service: the front door for uploads and reads.

Implements idempotent, content-addressed ingestion, dedupe by SHA-256, step
initialisation, event auditing, and the read endpoints (steps, events, pages,
content, extraction, download URL). Every dependency — repositories and stores —
is injected at construction so tests substitute fakes.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from ipa.api.schemas.documents import (
    ContentRead,
    DocumentFilters,
    DocumentPatch,
    DocumentRead,
    EventRead,
    ExtractionRead,
    IngestResult,
    PageParams,
    PageRead,
    PageTextRead,
    StepRead,
    UploadPayload,
)
from ipa.contracts.protocols import BlobStore, ContentStore
from ipa.core.enums import PipelineStep, StepStatus
from ipa.core.errors import NotFoundError
from ipa.core.ids import original_key, sha256_hex
from ipa.db.dtos import DocumentDto
from ipa.db.dtos import DocumentPatch as DbDocumentPatch
from ipa.db.enums import DocumentSource
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.event import EventRepository
from ipa.db.repositories.step import StepRepository

logger = structlog.get_logger(__name__)

# Steps that run before extraction; the ones we mark at ingest.
_PRE_EXTRACT = (PipelineStep.STORE, PipelineStep.DECOMPOSE, PipelineStep.OCR)


class DocumentService:
    """Coordinates uploads, dedupe, state initialisation and reads."""

    def __init__(
        self,
        documents: DocumentRepository,
        steps: StepRepository,
        events: EventRepository,
        blob: BlobStore,
        content: ContentStore,
    ) -> None:
        """Initialise the service.

        Args:
            documents: Document repository.
            steps: Step repository.
            events: Event repository.
            blob: Blob store for original bytes and page images.
            content: Content store for OCR text and extractions.
        """
        self._documents = documents
        self._steps = steps
        self._events = events
        self._blob = blob
        self._content = content

    async def ingest(self, payload: UploadPayload) -> IngestResult:
        """Ingest a single file, deduplicating by content digest.

        Args:
            payload: The upload.

        Returns:
            An `IngestResult` carrying the document, a dedupe flag and the HTTP
            status to return (200 for deduplicated, 202 for new).

        Raises:
            NotFoundError: If a referenced tag does not exist (checked by caller).
        """
        digest = sha256_hex(payload.data)
        existing = await self._documents.find_by_sha256(digest)
        if existing is not None:
            return IngestResult(
                document=self._to_read(existing),
                deduplicated=True,
                http_status=200,
            )

        blob_key = original_key(digest)
        mime = payload.mime_type or "application/octet-stream"
        await self._blob.put(blob_key, payload.data, mime)

        document = await self._documents.create(
            sha256=digest,
            original_filename=payload.filename,
            mime_type=payload.mime_type or "application/octet-stream",
            size_bytes=len(payload.data),
            blob_key=blob_key,
            source=DocumentSource(payload.source),
            uploaded_by=payload.uploaded_by,
            title=payload.title,
            tag_id=payload.tag_id,
            metadata=payload.metadata,
        )

        await self._steps.ensure_steps(document.id)
        await self._mark_store_succeeded(document.id)
        await self._events.append(
            document.id,
            "uploaded",
            actor=payload.uploaded_by,
            payload={"filename": payload.filename, "sha256": digest},
        )

        return IngestResult(document=self._to_read(document), deduplicated=False, http_status=202)

    async def get(self, document_id: UUID) -> DocumentRead:
        """Fetch a document.

        Args:
            document_id: Identifier of the document.

        Returns:
            The document read model.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._must_get(document_id)
        return self._to_read(document)

    async def list_documents(
        self, filters: DocumentFilters, page: PageParams
    ) -> dict[str, list[DocumentRead] | None]:
        """List documents with filters and pagination.

        Args:
            filters: The filters to apply.
            page: Pagination parameters.

        Returns:
            A dict with `items` and `next_cursor`.
        """
        documents = await self._documents.list_documents(
            status=filters.status,
            needs_review=filters.needs_review,
            tag_id=filters.tag_id,
            limit=page.limit,
        )
        items = [self._to_read(document) for document in documents]
        return {"items": items, "next_cursor": None}

    async def steps(self, document_id: UUID) -> list[StepRead]:
        """Return a document's per-step execution state.

        Args:
            document_id: Identifier of the document.

        Returns:
            The step read models in pipeline order.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        steps = await self._steps.list_steps(document_id)
        return [
            StepRead(
                step=step.step,
                status=step.status,
                attempt=step.attempt,
                max_attempts=step.max_attempts,
                started_at=step.started_at,
                finished_at=step.finished_at,
                duration_ms=step.duration_ms,
                error_code=step.error_code,
                error_detail=step.error_detail,
                metrics=step.metrics,
            )
            for step in steps
        ]

    async def events(
        self, document_id: UUID, page: PageParams
    ) -> dict[str, list[EventRead] | None]:
        """Return a document's audit log.

        Args:
            document_id: Identifier of the document.
            page: Pagination parameters.

        Returns:
            A dict with `items` and `next_cursor`.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        events = await self._events.list_events(document_id, limit=page.limit)
        items = [
            EventRead(
                id=event.id,
                event_type=event.event_type,
                step=event.step,
                payload=event.payload,
                actor=event.actor,
                created_at=event.created_at,
            )
            for event in events
        ]
        return {"items": items, "next_cursor": None}

    async def pages(self, document_id: UUID) -> list[PageRead]:
        """Return a document's page index with presigned image URLs.

        Args:
            document_id: Identifier of the document.

        Returns:
            The page read models.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        return []

    async def content(self, document_id: UUID, page: int | None = None) -> ContentRead:
        """Return OCR text for a document or a single page.

        Args:
            document_id: Identifier of the document.
            page: Return only this page when set.

        Returns:
            The content read model.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        texts = await self._content.get_pages(document_id)
        pages = [
            PageTextRead(
                page=item.page,
                text=item.text,
                source=item.source,
                confidence=item.confidence,
            )
            for item in texts
            if page is None or item.page == page
        ]
        return ContentRead(document_id=document_id, pages=pages)

    async def extraction(
        self, document_id: UUID, version: int | None = None
    ) -> ExtractionRead | None:
        """Return a document's extraction version.

        Args:
            document_id: Identifier of the document.
            version: Version to return; latest when None.

        Returns:
            The extraction read model, or None when none exists.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        record = await self._content.get_extraction(document_id, version=version)
        if record is None:
            return None
        return ExtractionRead(
            document_id=record.document_id,
            version=record.version,
            tag_id=record.tag_id,
            tag_version=record.tag_version,
            source=record.source,
            model=record.model,
            document_confidence=record.document_confidence,
            fields=[field.model_dump() for field in record.fields],
            created_at=record.created_at,
        )

    async def download_url(self, document_id: UUID, inline: bool = False) -> str:
        """Return a presigned download URL for a document's original.

        Args:
            document_id: Identifier of the document.
            inline: Request a content-disposition hint when True.

        Returns:
            A presigned HTTP URL.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._must_get(document_id)
        return await self._blob.presigned_url(document.blob_key)

    async def update(self, document_id: UUID, patch: DocumentPatch) -> DocumentRead:
        """Update a document's editable columns.

        Args:
            document_id: Identifier of the document.
            patch: The columns to change.

        Returns:
            The updated document read model.

        Raises:
            NotFoundError: If the document does not exist.
        """
        await self._must_get(document_id)
        db_patch = DbDocumentPatch(
            title=patch.title,
            tag_id=patch.tag_id,
            meta=patch.metadata,
        )
        updated = await self._documents.patch(document_id, db_patch)
        if updated is None:
            raise NotFoundError(f"no document with id {document_id}")
        return self._to_read(updated)

    async def delete(self, document_id: UUID, hard: bool = False) -> None:
        """Soft-delete a document, or remove it entirely when `hard` is set.

        Args:
            document_id: Identifier of the document.
            hard: Cascade the delete across every store when True.

        Returns:
            None.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._must_get(document_id)
        if not hard:
            await self._documents.patch(document_id, DbDocumentPatch(meta={"deleted": True}))
            return
        await self._content.delete_document(document_id)
        await self._blob.delete(document.blob_key)
        await self._events.append(document_id, "deleted")
        await self._documents.patch(document_id, DbDocumentPatch(meta={"deleted": True}))

    async def _mark_store_succeeded(self, document_id: UUID) -> None:
        """Mark the `store` step succeeded at ingest time.

        Args:
            document_id: Identifier of the document.
        """
        step = await self._steps.get(document_id, PipelineStep.STORE)
        if step is None or step.status != StepStatus.PENDING:
            return
        await self._steps.mark_succeeded(document_id, PipelineStep.STORE, output_ref="blob")

    async def _must_get(self, document_id: UUID) -> DocumentDto:
        """Return a document or raise NotFoundError.

        Args:
            document_id: Identifier of the document.

        Returns:
            The document DTO.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError(f"no document with id {document_id}")
        return document

    def _to_read(self, document: DocumentDto) -> DocumentRead:
        """Map a document DTO to an API read model.

        Args:
            document: The document DTO.

        Returns:
            The document read model.
        """
        return DocumentRead(
            id=document.id,
            sha256=document.sha256,
            original_filename=document.original_filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            blob_key=document.blob_key,
            page_count=document.page_count,
            tag_id=document.tag_id,
            tag_version=document.tag_version,
            status=document.status,
            current_step=document.current_step,
            document_confidence=document.document_confidence,
            needs_review=document.needs_review,
            title=document.title,
            trace_id=document.trace_id,
            error_code=document.error_code,
            error_detail=document.error_detail,
            metadata=document.metadata,
            created_at=document.created_at,
            updated_at=document.updated_at,
            completed_at=document.completed_at,
        )


