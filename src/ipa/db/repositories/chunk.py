"""Chunk repository over the pgvector table."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.contracts.models import ChunkVector
from ipa.db.models import Chunk


class ChunkRepository:
    """Writes and deletes `chunks` rows; search lives in `ipa.db.vector`."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def upsert(self, chunks: list[ChunkVector]) -> None:
        """Insert or replace chunks keyed on `(document_id, chunk_index, embed_model)`.

        Args:
            chunks: Chunks with embeddings attached; an empty list is a no-op.
        """
        if not chunks:
            return
        table = cast(sa.Table, Chunk.__table__)
        values = [
            {
                "id": uuid4(),
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "embedding": chunk.embedding,
                "embed_model": chunk.embed_model,
                "embed_dim": chunk.embed_dim,
                "page_from": chunk.page_from,
                "page_to": chunk.page_to,
                "metadata": chunk.metadata,
            }
            for chunk in chunks
        ]
        insert_stmt = pg_insert(table)
        stmt = insert_stmt.values(values).on_conflict_do_update(
            index_elements=["document_id", "chunk_index", "embed_model"],
            set_={
                "text": insert_stmt.excluded.text,
                "embedding": insert_stmt.excluded.embedding,
                "embed_dim": insert_stmt.excluded.embed_dim,
                "page_from": insert_stmt.excluded.page_from,
                "page_to": insert_stmt.excluded.page_to,
                "metadata": insert_stmt.excluded.metadata,
            },
        )
        await self._session.execute(stmt)

    async def delete_document(self, document_id: UUID) -> None:
        """Remove every chunk belonging to a document.

        Args:
            document_id: Owning document.

        Returns:
            None.
        """
        await self._session.execute(sa.delete(Chunk).where(Chunk.document_id == document_id))

    async def count(self, document_id: UUID) -> int:
        """Count a document's chunk rows.

        Args:
            document_id: Owning document.

        Returns:
            The number of chunk rows across all embedding models.
        """
        stmt = sa.select(sa.func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        return int((await self._session.execute(stmt)).scalar_one())
