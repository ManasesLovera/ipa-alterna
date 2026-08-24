"""Embed step: chunk text, embed it, and write to pgvector.

Registered as the `embed` step handler. Loads page text, chunks it, prepends a
context header, embeds each chunk with the embedding provider, and upserts into
the `chunks` table keyed on `(document_id, chunk_index, embed_model)`. Also emits
one extraction-summary chunk per document. Empty documents are skipped.
"""

from __future__ import annotations

from typing import Any

import structlog

from ipa.contracts.models import ChunkVector, StepContext, StepResult
from ipa.core.enums import PipelineStep, StepStatus
from ipa.pipeline.registry import register

logger = structlog.get_logger(__name__)


@register(PipelineStep.EMBED)
async def embed_handler(context: StepContext) -> StepResult:
    """Chunk and embed a document's text into the vector store.

    Args:
        context: The step context.

    Returns:
        A step result with chunk/embedding metrics, or a skipped status when the
        document has no text.
    """
    from ipa.core.config import get_settings
    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.repositories.event import EventRepository
    from ipa.db.session import get_sessionmaker, session_scope
    from ipa.db.vector import PgVectorStore
    from ipa.processing.chunking import chunk_pages
    from ipa.storage.factory import get_content_store

    settings = get_settings()
    content = get_content_store()

    pages = await content.get_pages(context.document_id)
    if not pages:
        skip = {"skipped_empty": 1.0}
        return StepResult(status=StepStatus.SKIPPED, detail="no text to embed", metrics=skip)

    async with session_scope() as session:
        documents = DocumentRepository(session)
        events = EventRepository(session)
        document = await documents.get(context.document_id)
        if document is None:
            return StepResult(status=StepStatus.FAILED, detail="document not found")

        raw_chunks = chunk_pages(
            pages,
            target_tokens=settings.pipeline.chunk_tokens,
            overlap_tokens=settings.pipeline.chunk_overlap,
        )

        summary = await _summary_chunk(context, document)
        header = _context_header(document, None, None)

        to_embed: list[str] = []
        vectors: list[ChunkVector] = []
        for chunk in raw_chunks:
            embedded_text = f"[{header}, pages {chunk.page_from}-{chunk.page_to}]\n{chunk.text}"
            to_embed.append(embedded_text)
            vectors.append(
                ChunkVector(
                    document_id=context.document_id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    embedding=[],
                    embed_model="",
                    page_from=chunk.page_from,
                    page_to=chunk.page_to,
                    metadata={"kind": "text"},
                )
            )

        provider = _get_embedding_provider()
        model = provider.model
        for vector in vectors:
            vector.embed_model = model

        embeddings = await provider.embed(to_embed, kind="passage")
        for vector, embedding in zip(vectors, embeddings, strict=True):
            vector.embedding = embedding

        if summary is not None:
            summary_embedding = await provider.embed([summary[1]], kind="passage")
            vectors.append(
                ChunkVector(
                    document_id=context.document_id,
                    chunk_index=-1,
                    text=summary[0],
                    embedding=summary_embedding[0],
                    embed_model=model,
                    page_from=None,
                    page_to=None,
                    metadata={"kind": "extraction_summary"},
                )
            )

        vector_store = PgVectorStore(get_sessionmaker())
        await vector_store.upsert(vectors)
        await events.append(context.document_id, "embedded", payload={"chunks": len(vectors)})

        return StepResult(
            status=StepStatus.SUCCEEDED,
            metrics={
                "chunks": float(len(vectors)),
                "tokens_embedded": float(sum(chunk.tokens for chunk in raw_chunks)),
                "batches": float(len(embeddings)),
                "provider_ms": 0.0,
                "skipped_empty": 0.0,
            },
        )


def _context_header(document: object, tag_name: str | None, page: int | None) -> str:
    """Build the context header prepended to each chunk's embedded text.

    Args:
        document: The document DTO.
        tag_name: The document's tag name, when known.
        page: A page number to include, when known.

    Returns:
        A header string.
    """
    title = getattr(document, "title", None) or getattr(document, "original_filename", "document")
    parts = [str(title)]
    if tag_name:
        parts.append(tag_name)
    if page is not None:
        parts.append(f"page {page}")
    return " — ".join(parts)


async def _summary_chunk(context: StepContext, document: object) -> tuple[str, str] | None:
    """Build the extraction-summary chunk from the document's fields.

    Args:
        context: The step context.
        document: The document DTO.

    Returns:
        A tuple of `(display_text, embedded_text)`, or None when the document has
        no extraction.
    """
    from ipa.storage.factory import get_content_store

    content = get_content_store()
    record = await content.get_extraction(context.document_id)
    if record is None or not record.fields:
        return None
    parts = [f"{field.key}: {field.value}" for field in record.fields]
    display = " | ".join(parts)
    header = _context_header(document, None, None)
    return display, f"[{header}, extraction summary]\n{display}"


def _get_embedding_provider() -> Any:
    """Return the configured embedding provider.

    Returns:
        An `EmbeddingProvider`.
    """
    from ipa.providers.factory import get_embedding_provider

    return get_embedding_provider()
