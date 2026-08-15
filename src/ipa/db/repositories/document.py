"""Document repository."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import DocumentStatus
from ipa.db.dtos import DocumentDto, DocumentPatch
from ipa.db.enums import DocumentSource
from ipa.db.models import Document


class DocumentRepository:
    """Reads and writes `documents` rows; returns DTOs only."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def create(
        self,
        *,
        sha256: str,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        blob_key: str,
        source: DocumentSource,
        uploaded_by: str | None = None,
        title: str | None = None,
        tag_id: UUID | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentDto:
        """Insert a new document row.

        Args:
            sha256: Hex digest of the uploaded bytes; unique across documents.
            original_filename: Client-supplied filename.
            mime_type: Declared MIME type of the upload.
            size_bytes: Size of the upload in bytes.
            blob_key: Content-addressed blob store key.
            source: Channel the document arrived through.
            uploaded_by: Actor identifier, when known.
            title: Optional human-facing title.
            tag_id: Tag assigned at upload, when known.
            trace_id: OpenTelemetry trace propagated from the request.
            metadata: Free-form jsonb payload; defaults to an empty object.

        Returns:
            The created document as a DTO.
        """
        entity = Document(
            sha256=sha256,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            blob_key=blob_key,
            source=source,
            uploaded_by=uploaded_by,
            title=title,
            tag_id=tag_id,
            trace_id=trace_id,
            meta=metadata or {},
        )
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return DocumentDto.model_validate(entity)

    async def get(self, document_id: UUID) -> DocumentDto | None:
        """Fetch one document regardless of soft-delete state.

        Args:
            document_id: Identifier of the document.

        Returns:
            The document DTO, or None when the id is unknown.
        """
        entity = await self._session.get(Document, document_id)
        return DocumentDto.model_validate(entity) if entity else None

    async def find_by_sha256(self, sha256: str) -> DocumentDto | None:
        """Find a live (not soft-deleted) document by content digest.

        Args:
            sha256: Hex digest of the original bytes.

        Returns:
            The matching document DTO, or None.
        """
        stmt = sa.select(Document).where(Document.sha256 == sha256, Document.deleted_at.is_(None))
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentDto.model_validate(entity) if entity else None

    async def patch(self, document_id: UUID, patch: DocumentPatch) -> DocumentDto | None:
        """Apply a partial update to a document row.

        Args:
            document_id: Identifier of the document.
            patch: Columns to update; only fields explicitly set are applied.

        Returns:
            The updated document DTO, or None when the id is unknown.
        """
        entity = await self._session.get(Document, document_id)
        if entity is None:
            return None
        changes = patch.model_dump(exclude_unset=True)
        if "document_confidence" in changes and changes["document_confidence"] is not None:
            changes["document_confidence"] = Decimal(str(changes["document_confidence"]))
        for key, value in changes.items():
            setattr(entity, key, value)
        await self._session.flush()
        await self._session.refresh(entity)
        return DocumentDto.model_validate(entity)

    async def list_documents(
        self,
        *,
        status: DocumentStatus | None = None,
        needs_review: bool | None = None,
        tag_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DocumentDto]:
        """List live documents, newest first.

        Args:
            status: Filter by coarse status; None for all.
            needs_review: Filter by the review flag; None for all.
            tag_id: Filter by tag; None for all.
            limit: Maximum rows to return.
            offset: Number of rows to skip.

        Returns:
            Matching document DTOs ordered by `created_at` descending.
        """
        stmt = sa.select(Document).where(Document.deleted_at.is_(None))
        if status is not None:
            stmt = stmt.where(Document.status == status)
        if needs_review is not None:
            stmt = stmt.where(Document.needs_review == needs_review)
        if tag_id is not None:
            stmt = stmt.where(Document.tag_id == tag_id)
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DocumentDto.model_validate(row) for row in rows]
