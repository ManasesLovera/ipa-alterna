"""Document page repository for the `document_pages` index."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.db.dtos import DocumentPageDto
from ipa.db.models import DocumentPage


class PageRepository:
    """Reads and writes `document_pages` rows; returns DTOs only."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def replace_all(
        self,
        document_id: UUID,
        pages: list[dict[str, Any]],
    ) -> list[DocumentPageDto]:
        """Replace a document's page rows in one transaction.

        Idempotent: existing rows for the document are deleted first, then the
        new set is inserted, so a retried decompose never duplicates or leaks.

        Args:
            document_id: Owning document.
            pages: Page rows as dicts with `page`, `blob_key`, etc.

        Returns:
            The newly written page DTOs ordered by page number.
        """
        await self._session.execute(
            sa.delete(DocumentPage).where(DocumentPage.document_id == document_id)
        )
        for page in pages:
            self._session.add(DocumentPage(document_id=document_id, **page))
        await self._session.flush()
        return await self.list_pages(document_id)

    async def list_pages(self, document_id: UUID) -> list[DocumentPageDto]:
        """Return a document's pages ordered by page number.

        Args:
            document_id: Owning document.

        Returns:
            The page DTOs ordered by `page`.
        """
        stmt = (
            sa.select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DocumentPageDto.model_validate(row) for row in rows]

    async def update_page(
        self, document_id: UUID, page: int, **updates: Any
    ) -> DocumentPageDto | None:
        """Patch one page row.

        Args:
            document_id: Owning document.
            page: The page number.
            updates: Columns to set.

        Returns:
            The updated page DTO, or None when the page does not exist.
        """
        stmt = (
            sa.update(DocumentPage)
            .where(DocumentPage.document_id == document_id, DocumentPage.page == page)
            .values(**updates)
            .returning(DocumentPage)
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentPageDto.model_validate(row) if row else None
