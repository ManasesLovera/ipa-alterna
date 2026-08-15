"""pgvector-backed `VectorStore` implementation.

Implements the `ipa.contracts.protocols.VectorStore` protocol over the
`chunks` table: cosine KNN plus optional full-text retrieval, fused with
Reciprocal Rank Fusion. Query parsing and embedding live in the callers
(T12/T14); this module only executes searches.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ipa.contracts.models import ChunkHit, ChunkVector, SearchFilters
from ipa.db.models import Chunk, Document, Tag
from ipa.db.repositories.chunk import ChunkRepository

RRF_K = 60
"""Reciprocal Rank Fusion constant; 60 is the canonical value from the literature."""

_FETCH_MULTIPLIER = 10
_FETCH_FLOOR = 100
_FETCH_CEILING = 500


def rrf_fuse(rankings: list[list[Any]], *, top_k: int) -> list[tuple[Any, float]]:
    """Fuse ranked row lists with Reciprocal Rank Fusion.

    Rows are tuples whose first three elements are `(document_id, chunk_index,
    text)` and form the identity key. A row absent from a ranking contributes
    nothing from that ranking.

    Args:
        rankings: Ranked row lists, best first.
        top_k: Maximum fused rows to return.

    Returns:
        Up to `top_k` `(row, fused_score)` pairs, best first.
    """
    scores: dict[tuple[Any, ...], float] = {}
    rows_by_key: dict[tuple[Any, ...], Any] = {}
    for ranking in rankings:
        for rank, row in enumerate(ranking):
            key = (row[0], row[1], row[2])
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
            rows_by_key[key] = row
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [(rows_by_key[key], score) for key, score in ordered[:top_k]]


class PgVectorStore:
    """`VectorStore` over the `chunks` table; one session per operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Bind the store to a session factory.

        Args:
            session_factory: Produces one `AsyncSession` per operation, each
                committed on success.
        """
        self._session_factory = session_factory

    async def upsert(self, chunks: list[ChunkVector]) -> None:
        """Insert or replace chunks, keyed on `(document_id, chunk_index, embed_model)`.

        Args:
            chunks: Chunks with embeddings attached.
        """
        async with self._session_factory() as session:
            repository = ChunkRepository(session)
            await repository.upsert(chunks)
            await session.commit()

    async def delete_document(self, document_id: UUID) -> None:
        """Remove every chunk belonging to a document.

        Args:
            document_id: Owning document.
        """
        async with self._session_factory() as session:
            repository = ChunkRepository(session)
            await repository.delete_document(document_id)
            await session.commit()

    async def refresh_metadata(self, document_id: UUID) -> None:
        """Re-sync denormalised document metadata onto the document's chunks.

        Copies `tag_id`, `tag_slug` and `document_status` into each chunk's
        jsonb metadata so search can filter without a join. Documents without
        a tag store JSON nulls for the tag members.

        Args:
            document_id: Document whose chunk metadata should be refreshed.
        """
        async with self._session_factory() as session:
            document = (
                await session.execute(
                    sa.select(Document.tag_id, Document.status, Tag.slug)
                    .select_from(Document)
                    .outerjoin(Tag, Tag.id == Document.tag_id)
                    .where(Document.id == document_id)
                )
            ).one_or_none()
            if document is None:
                return
            denorm = {
                "tag_id": str(document.tag_id) if document.tag_id else None,
                "tag_slug": document.slug,
                "document_status": document.status.value,
            }
            await session.execute(
                sa.update(Chunk)
                .values(meta=Chunk.meta.op("||")(sa.type_coerce(denorm, JSONB)))
                .where(Chunk.document_id == document_id)
            )
            await session.commit()

    async def search(
        self,
        embedding: list[float],
        *,
        query_text: str | None,
        top_k: int,
        filters: SearchFilters | None,
    ) -> list[ChunkHit]:
        """Return the most similar chunks, best first.

        Runs cosine KNN and, when `query_text` is given, a full-text ranking;
        the two lists are fused with RRF. A pure vector search reports
        `1 - cosine_distance` as the score, preserving cosine ordering.

        Args:
            embedding: Query vector.
            query_text: Original query for lexical scoring; may be None.
            top_k: Maximum number of hits.
            filters: Optional narrowing; None means search everything.

        Returns:
            Scored hits ordered by descending score.
        """
        fetch = min(max(top_k * _FETCH_MULTIPLIER, _FETCH_FLOOR), _FETCH_CEILING)
        distance = Chunk.embedding.cosine_distance(embedding)
        async with self._session_factory() as session:
            conditions = _filter_conditions(filters)
            vector_rows: list[Any] = list(
                (
                    await session.execute(
                        sa.select(
                            Chunk.document_id,
                            Chunk.chunk_index,
                            Chunk.text,
                            Chunk.page_from,
                            Chunk.page_to,
                            distance,
                        )
                        .where(*conditions)
                        .order_by(distance)
                        .limit(fetch)
                    )
                ).all()
            )
            text_rows: list[Any] = []
            if query_text:
                tsquery = sa.func.plainto_tsquery("simple", query_text)
                rank_expr = sa.func.ts_rank(Chunk.tsv, tsquery)
                text_rows = list(
                    (
                        await session.execute(
                            sa.select(
                                Chunk.document_id,
                                Chunk.chunk_index,
                                Chunk.text,
                                Chunk.page_from,
                                Chunk.page_to,
                                rank_expr,
                            )
                            .where(*conditions, Chunk.tsv.op("@@")(tsquery))
                            .order_by(rank_expr.desc())
                            .limit(fetch)
                        )
                    ).all()
                )
        if text_rows:
            fused = rrf_fuse([vector_rows, text_rows], top_k=top_k)
            return [
                ChunkHit(
                    document_id=row[0],
                    chunk_index=row[1],
                    text=row[2],
                    score=score,
                    page_from=row[3],
                    page_to=row[4],
                )
                for row, score in fused
            ]
        return [
            ChunkHit(
                document_id=row[0],
                chunk_index=row[1],
                text=row[2],
                score=1.0 - float(row[5]),
                page_from=row[3],
                page_to=row[4],
            )
            for row in vector_rows[:top_k]
        ]


def _filter_conditions(filters: SearchFilters | None) -> list[Any]:
    """Translate search filters into chunk where-clauses.

    Args:
        filters: Optional narrowing; None or unset members are skipped.

    Returns:
        A list of SQLAlchemy conditions, empty when unfiltered.
    """
    if filters is None:
        return []
    conditions: list[Any] = []
    if filters.tag_ids:
        conditions.append(
            Chunk.meta["tag_id"].astext.in_([str(tag_id) for tag_id in filters.tag_ids])
        )
    if filters.document_ids:
        conditions.append(Chunk.document_id.in_(filters.document_ids))
    if filters.statuses:
        conditions.append(
            Chunk.meta["document_status"].astext.in_([status.value for status in filters.statuses])
        )
    if filters.created_after is not None:
        conditions.append(Chunk.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(Chunk.created_at <= filters.created_before)
    return conditions
