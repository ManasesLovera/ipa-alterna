"""Vector store round-trip and hybrid search against real pgvector."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ipa.contracts.models import ChunkVector, SearchFilters
from ipa.core.enums import DocumentStatus
from ipa.db.enums import DocumentSource
from ipa.db.models import Document, Tag
from ipa.db.vector import PgVectorStore

pytestmark = pytest.mark.integration_light

DIM = 1024
EMBED_MODEL = "fake/embed"


def _vec(*leading: float) -> list[float]:
    vector = [0.0] * DIM
    for index, value in enumerate(leading):
        vector[index] = value
    return vector


async def _seed_document(session: AsyncSession, *, sha256: str, slug: str) -> tuple[Document, Tag]:
    tag = Tag(slug=slug, name=slug.title())
    session.add(tag)
    await session.flush()
    document = Document(
        sha256=sha256,
        original_filename=f"{slug}.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        blob_key=f"originals/aa/{sha256}",
        source=DocumentSource.UI,
        tag_id=tag.id,
    )
    session.add(document)
    await session.flush()
    await session.refresh(tag)
    await session.refresh(document)
    return document, tag


async def test_upsert_cosine_ranking_and_rrf(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PgVectorStore(db_sessionmaker)
    async with db_sessionmaker() as session:
        document, _tag = await _seed_document(session, sha256="1" * 64, slug="reports")
        document_id = document.id
        await session.commit()

    chunks = [
        ChunkVector(
            document_id=document_id,
            chunk_index=0,
            text="quarterly revenue report summary",
            embedding=_vec(1.0, 0.0),
            embed_model=EMBED_MODEL,
        ),
        ChunkVector(
            document_id=document_id,
            chunk_index=1,
            text="employee onboarding checklist",
            embedding=_vec(0.0, 1.0),
            embed_model=EMBED_MODEL,
        ),
        ChunkVector(
            document_id=document_id,
            chunk_index=2,
            text="printer firmware changelog",
            embedding=_vec(0.0, 0.0, 1.0),
            embed_model=EMBED_MODEL,
        ),
    ]
    await store.upsert(chunks)

    hits = await store.search(_vec(1.0, 0.5, 0.0), query_text=None, top_k=3, filters=None)
    assert [hit.chunk_index for hit in hits] == [0, 1, 2]
    assert hits[0].score > hits[1].score > hits[2].score

    lexical = await store.search(
        _vec(1.0, 0.5, 0.0), query_text="revenue report", top_k=3, filters=None
    )
    assert lexical[0].chunk_index == 0
    assert [hit.chunk_index for hit in lexical] == [0, 1, 2]


async def test_rrf_promotes_lexical_match(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PgVectorStore(db_sessionmaker)
    async with db_sessionmaker() as session:
        document, _tag = await _seed_document(session, sha256="2" * 64, slug="hybrid")
        document_id = document.id
        await session.commit()

    await store.upsert(
        [
            ChunkVector(
                document_id=document_id,
                chunk_index=0,
                text="completely unrelated filler text",
                embedding=_vec(1.0, 0.0),
                embed_model=EMBED_MODEL,
            ),
            ChunkVector(
                document_id=document_id,
                chunk_index=1,
                text="export compliance tariff schedule",
                embedding=_vec(0.0, 1.0),
                embed_model=EMBED_MODEL,
            ),
        ]
    )

    pure_vector = await store.search(_vec(1.0, 0.1, 0.0), query_text=None, top_k=2, filters=None)
    assert [hit.chunk_index for hit in pure_vector] == [0, 1]

    fused = await store.search(
        _vec(1.0, 0.1, 0.0), query_text="tariff schedule", top_k=2, filters=None
    )
    assert fused[0].chunk_index == 1


async def test_refresh_metadata_and_filters(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PgVectorStore(db_sessionmaker)
    async with db_sessionmaker() as session:
        document, tag = await _seed_document(session, sha256="3" * 64, slug="finance")
        await session.commit()
        document_id, tag_id = document.id, tag.id

    await store.upsert(
        [
            ChunkVector(
                document_id=document_id,
                chunk_index=0,
                text="invoice total amount due",
                embedding=_vec(1.0),
                embed_model=EMBED_MODEL,
            )
        ]
    )
    await store.refresh_metadata(document_id)

    no_filter = await store.search(_vec(1.0), query_text=None, top_k=5, filters=None)
    assert len(no_filter) == 1

    wrong_tag = await store.search(
        _vec(1.0),
        query_text=None,
        top_k=5,
        filters=SearchFilters(tag_ids=[tag_id]),
    )
    assert len(wrong_tag) == 1

    from uuid import uuid4

    other = await store.search(
        _vec(1.0),
        query_text=None,
        top_k=5,
        filters=SearchFilters(tag_ids=[uuid4()]),
    )
    assert other == []

    by_status = await store.search(
        _vec(1.0),
        query_text=None,
        top_k=5,
        filters=SearchFilters(statuses=[DocumentStatus.RECEIVED]),
    )
    assert len(by_status) == 1


async def test_upsert_replaces_same_key_and_delete_document(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from ipa.db.repositories.chunk import ChunkRepository

    store = PgVectorStore(db_sessionmaker)
    async with db_sessionmaker() as session:
        document, _tag = await _seed_document(session, sha256="4" * 64, slug="upsert")
        document_id = document.id
        await session.commit()

    await store.upsert(
        [
            ChunkVector(
                document_id=document_id,
                chunk_index=0,
                text="first text",
                embedding=_vec(1.0),
                embed_model=EMBED_MODEL,
            )
        ]
    )
    await store.upsert(
        [
            ChunkVector(
                document_id=document_id,
                chunk_index=0,
                text="replaced text",
                embedding=_vec(0.9, 0.1),
                embed_model=EMBED_MODEL,
            ),
            ChunkVector(
                document_id=document_id,
                chunk_index=0,
                text="other model row",
                embedding=_vec(1.0),
                embed_model="other/model",
            ),
        ]
    )

    async with db_sessionmaker() as session:
        repository = ChunkRepository(session)
        assert await repository.count(document_id) == 2

    hits = await store.search(_vec(1.0), query_text=None, top_k=10, filters=None)
    texts = {hit.text for hit in hits if hit.document_id == document_id}
    assert texts == {"replaced text", "other model row"}

    await store.delete_document(document_id)
    async with db_sessionmaker() as session:
        repository = ChunkRepository(session)
        assert await repository.count(document_id) == 0
