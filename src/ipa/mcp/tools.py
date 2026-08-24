"""MCP tool implementations.

Every tool is a thin adapter over the service layer. Tools return bounded,
attributed payloads (snippet-level, not whole documents) so they stay useful in
an agent's context window.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from ipa.contracts.models import SearchFilters
from ipa.domain.documents import DocumentService
from ipa.domain.search import SearchService
from ipa.domain.tags import TagService
from ipa.mcp.schemas import (
    DocumentDetail,
    DocumentSummary,
    ExtractionResult,
    SearchHit,
    TagSummary,
)


def _search_service() -> SearchService:
    """Assemble the search service from process singletons.

    Returns:
        A `SearchService` instance.
    """
    from ipa.db.session import get_sessionmaker
    from ipa.db.vector import PgVectorStore
    from ipa.providers.factory import get_embedding_provider, get_llm_provider
    from ipa.storage.factory import blob_store_dep, cache_store_dep, content_store_dep

    return SearchService(
        embeddings=get_embedding_provider(),
        vectors=PgVectorStore(get_sessionmaker()),
        content=content_store_dep(),
        llm=get_llm_provider(),
        cache=cache_store_dep(),
        blob=blob_store_dep(),
    )


async def search_documents(
    query: str, top_k: int = 8, tag_slug: str | None = None, status: str | None = None
) -> list[SearchHit]:
    """Search document content semantically and by keyword.

    Call this when the user asks about information that might be stored in the
    corpus — anything from invoice totals to policy clauses. Returns snippets,
    not whole documents. By default only validated and completed documents are
    searched; pass an explicit status to include others.

    Args:
        query: The search text.
        top_k: Maximum hits to return.
        tag_slug: Restrict to one tag, when known.
        status: Override the default validated/completed status filter.

    Returns:
        A list of attributed search hits.
    """
    from ipa.api.schemas.search import SearchRequest

    statuses = [status] if status else None
    service = _search_service()
    response = await service.search(
        SearchRequest(
            query=query,
            top_k=top_k,
            filters=SearchFilters(statuses=statuses),
        )
    )
    return [
        SearchHit(
            document_id=str(hit.document_id),
            page=hit.page_from,
            title=hit.document_title,
            text=hit.text,
            score=hit.score,
        )
        for hit in response.hits
    ]


async def find_documents_by_field(tag_slug: str, where: dict[str, Any]) -> list[DocumentSummary]:
    """Search documents by structured extracted-field filters.

    Call for exact-value or numeric filters such as "invoices over 1000 from
    ACME". Do not use `search_documents` for numeric or exact-value matching —
    it is lexical/semantic.

    Args:
        tag_slug: The tag to search.
        where: Field key to value or operator-object conditions.

    Returns:
        Matching document summaries.
    """
    from ipa.db.repositories.tag import TagRepository
    from ipa.db.session import session_scope

    async with session_scope() as session:
        tag = await TagRepository(session).get_by_slug(tag_slug)
        if tag is None:
            return []
        from ipa.api.schemas.search import FieldQuery

        records = await _search_service().search_documents(
            FieldQuery(tag_id=tag.id, where=where)
        )
        return [
            DocumentSummary(
                document_id=str(record["document_id"]),
                title=record.get("title"),
            )
            for record in records
        ]


async def get_document(document_id: str) -> DocumentDetail | None:
    """Fetch a document's metadata, status, tag and confidence.

    Call after a search to get context before reading content.

    Args:
        document_id: The document id.

    Returns:
        The document detail, or None when unknown.
    """
    from uuid import UUID

    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.repositories.event import EventRepository
    from ipa.db.repositories.step import StepRepository
    from ipa.db.session import session_scope
    from ipa.storage.factory import blob_store_dep, content_store_dep

    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return None
    async with session_scope() as session:
        service = DocumentService(
            documents=DocumentRepository(session),
            steps=StepRepository(session),
            events=EventRepository(session),
            blob=blob_store_dep(),
            content=content_store_dep(),
        )
        read = await service.get(doc_uuid)
        return DocumentDetail(
            document_id=str(read.id),
            title=read.title,
            filename=read.original_filename,
            status=str(read.status),
            page_count=read.page_count,
            confidence=read.document_confidence,
        )


async def get_extraction(document_id: str, version: int | None = None) -> ExtractionResult | None:
    """Fetch a document's structured extraction with per-field confidence.

    Prefer this over reading raw text when the answer is a known field.

    Args:
        document_id: The document id.
        version: The extraction version to fetch; latest when None.

    Returns:
        The extraction, or None when none exists.
    """
    from uuid import UUID

    from ipa.storage.factory import content_store_dep

    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        return None
    record = await content_store_dep().get_extraction(doc_uuid, version=version)
    if record is None:
        return None
    return ExtractionResult(
        document_id=str(record.document_id),
        version=record.version,
        fields=[field.model_dump() for field in record.fields],
    )


async def get_document_text(
    document_id: str, page_from: int = 1, page_to: int | None = None
) -> str:
    """Fetch raw OCR text for a page range.

    Call only when the extraction does not contain the answer. Bounded to 20
    pages per call; longer ranges are truncated with a marker telling the caller
    where to resume.

    Args:
        document_id: The document id.
        page_from: First page (1-based).
        page_to: Last page (inclusive); defaults to page_from.

    Returns:
        The page text for the range.
    """
    from uuid import UUID

    from ipa.storage.factory import content_store_dep

    end = page_to if page_to is not None else page_from
    max_pages = 20
    if end - page_from + 1 > max_pages:
        end = page_from + max_pages - 1
        remaining = "..." + (
            f"[truncated, pages remaining, call again with page_from={end + 1}]"
        )
    else:
        remaining = ""
    record = await content_store_dep().get_pages(UUID(document_id))
    parts = [
        f"<page n=\"{page.page}\">\n{page.text}\n</page>"
        for page in record
        if page_from <= page.page <= end
    ]
    return "\n".join(parts) + remaining


async def list_tags() -> list[TagSummary]:
    """List the available document types and their fields.

    Call first when you do not know what kinds of documents exist.

    Returns:
        The tag catalogue.
    """
    from ipa.db.repositories.tag import TagRepository
    from ipa.db.session import session_scope
    from ipa.storage.factory import cache_store_dep

    async with session_scope() as session:
        service = TagService(TagRepository(session), cache_store_dep())
        tags = await service.list_tags()
        return [
            TagSummary(slug=tag.slug, name=tag.name, fields=[f.key for f in tag.fields])
            for tag in tags
        ]


async def get_page_image(document_id: str, page: int) -> dict[str, str]:
    """Return a presigned URL for a page image.

    Call when the text is ambiguous or the layout matters.

    Args:
        document_id: The document id.
        page: The page number.

    Returns:
        A dict with the image URL.
    """
    from ipa.core.ids import page_image_key
    from ipa.storage.factory import blob_store_dep

    url = await blob_store_dep().presigned_url(page_image_key(UUID(document_id), page))
    return {"url": url}


async def assign_tag(document_id: str, tag_id: str) -> dict[str, str]:
    """Assign a tag to a document and reprocess from extract.

    Only available when the MCP server is started with write access enabled.

    Args:
        document_id: The document id.
        tag_id: The tag id to assign.

    Returns:
        A status message.
    """
    from uuid import UUID

    from ipa.core.enums import PipelineStep
    from ipa.db.dtos import DocumentPatch
    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.repositories.step import StepRepository
    from ipa.db.session import session_scope
    from ipa.pipeline.orchestrator import Orchestrator
    from ipa.pipeline.runner import _enqueue

    async with session_scope() as session:
        await DocumentRepository(session).patch(
            UUID(document_id), DocumentPatch(tag_id=UUID(tag_id))
        )
        await Orchestrator(
            DocumentRepository(session), StepRepository(session), _enqueue
        ).reprocess(UUID(document_id), PipelineStep.EXTRACT)
    return {"status": "assigned"}


async def request_reprocess(document_id: str, from_step: str) -> dict[str, str]:
    """Request that a document be reprocessed from a given pipeline step.

    Only available when the MCP server is started with write access enabled.

    Args:
        document_id: The document id.
        from_step: The step to reprocess from.

    Returns:
        A status message.
    """
    from uuid import UUID

    from ipa.core.enums import PipelineStep
    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.repositories.step import StepRepository
    from ipa.db.session import session_scope
    from ipa.pipeline.orchestrator import Orchestrator
    from ipa.pipeline.runner import _enqueue

    async with session_scope() as session:
        await Orchestrator(
            DocumentRepository(session), StepRepository(session), _enqueue
        ).reprocess(UUID(document_id), PipelineStep(from_step))
    return {"status": "reprocessing"}
