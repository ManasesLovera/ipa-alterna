"""Search and RAG service.

Retrieval over the chunk corpus (hybrid/dense/sparse via the vector store) and a
grounded question-answering endpoint. Free of FastAPI types so the MCP server
(T15) imports it directly.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

import structlog

from ipa.api.schemas.search import (
    ChunkHitRead,
    FieldQuery,
    RagRequest,
    RagResponse,
    RagSource,
    SearchRequest,
    SearchResponse,
)
from ipa.contracts.models import ChunkHit, SearchFilters
from ipa.contracts.protocols import (
    BlobStore,
    CacheStore,
    EmbeddingProvider,
    LlmProvider,
    VectorStore,
)
from ipa.storage.content import MongoContentStore

logger = structlog.get_logger(__name__)

CITATION_RE = re.compile(r"\[doc:([0-9a-f-]{36})(?: p:(\d+))?\]")

# Default statuses applied to search unless the caller overrides them.
DEFAULT_SEARCH_STATUSES = ("validated", "completed")


class SearchService:
    """Orchestrates hybrid search, field search, similar and RAG."""

    def __init__(
        self,
        embeddings: EmbeddingProvider,
        vectors: VectorStore,
        content: MongoContentStore,
        llm: LlmProvider,
        cache: CacheStore,
        blob: BlobStore,
    ) -> None:
        """Initialise the service.

        Args:
            embeddings: Embedding provider.
            vectors: Vector store.
            content: Content store.
            llm: LLM provider.
            cache: Cache store.
            blob: Blob store.
        """
        self._embeddings = embeddings
        self._vectors = vectors
        self._content = content
        self._llm = llm
        self._cache = cache
        self._blob = blob

    async def search(self, req: SearchRequest) -> SearchResponse:
        """Run a hybrid/dense/sparse search over the chunk corpus.

        Args:
            req: The search request.

        Returns:
            A search response with hits and optionally grouped results.
        """
        cache_key = _search_cache_key(req)
        cached = await self._cache.get_json(cache_key)
        if cached is not None:
            return SearchResponse.model_validate(cached)

        filters = req.filters or SearchFilters()
        if not filters.statuses:
            filters = filters.model_copy(
                update={"statuses": list(DEFAULT_SEARCH_STATUSES)}
            )

        embedding = await self._embeddings.embed([req.query], kind="query")
        query_text = req.query if req.mode in ("hybrid", "sparse") else None
        if req.mode == "dense":
            query_text = None

        hits = await self._vectors.search(
            embedding[0],
            query_text=query_text,
            top_k=min(req.top_k, 50),
            filters=filters,
        )
        read_hits = await self._hydrate_hits(hits)

        grouped: list[Any] = []
        if req.group_by_document:
            grouped = _group_by_document(read_hits)

        response = SearchResponse(hits=read_hits, grouped=grouped, mode=req.mode)
        await self._cache.set_json(cache_key, response.model_dump(), ttl_s=300)
        return response

    async def search_documents(self, req: FieldQuery) -> list[dict[str, Any]]:
        """Search current extraction versions by structured field queries.

        Args:
            req: The field query.

        Returns:
            A list of matching extraction records.
        """
        records = await self._content.list_current_extractions(req.tag_id)
        results: list[dict[str, Any]] = []
        for record in records:
            if _matches_query(record, req.where):
                results.append(record.model_dump())
        return results[: req.limit]

    async def rag(self, req: RagRequest) -> RagResponse:
        """Answer a question grounded in retrieved chunks with citations.

        Args:
            req: The RAG request.

        Returns:
            A RAG response, or a no-relevant-documents response when nothing is
            retrieved.
        """
        filters = req.filters or SearchFilters()
        if not filters.statuses:
            filters = filters.model_copy(
                update={"statuses": list(DEFAULT_SEARCH_STATUSES)}
            )

        embedding = await self._embeddings.embed([req.question], kind="query")
        hits = await self._vectors.search(
            embedding[0],
            query_text=req.question,
            top_k=min(req.top_k, 50),
            filters=filters,
        )
        if not hits:
            return RagResponse(answer=None, reason="no_relevant_documents", used_chunks=0)

        context = "\n\n".join(
            f"[doc:{hit.document_id} p:{hit.page_from or 1}] {hit.text}" for hit in hits
        )
        system = (
            "Answer the question using only the provided document excerpts. "
            "Cite every factual claim with [doc:<document_id> p:<page>]. "
            "If the excerpts do not contain the answer, say you cannot determine it."
        )
        completion = await self._llm.complete_json(
            system=system,
            user=f"Question: {req.question}\n\nExcerpts:\n{context}",
            json_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        )
        answer = completion.data.get("answer") if completion.data else None

        sources = []
        if req.include_sources and answer:
            seen: set[tuple[str, int]] = set()
            for match in CITATION_RE.finditer(answer):
                doc_id, page = match.group(1), int(match.group(2) or 1)
                if (doc_id, page) in seen:
                    continue
                seen.add((doc_id, page))
                sources.append(
                    RagSource(document_id=UUID(doc_id), page=page)
                )

        return RagResponse(answer=answer, sources=sources, used_chunks=len(hits))

    async def similar(self, document_id: UUID, top_k: int) -> list[ChunkHit]:
        """Return documents similar to a document's summary chunk.

        Args:
            document_id: Identifier of the document.
            top_k: Maximum number of neighbours to return.

        Returns:
            The nearest chunk hits.
        """
        record = await self._content.get_extraction(document_id)
        if record is None or not record.fields:
            return []
        summary_text = " | ".join(f"{f.key}: {f.value}" for f in record.fields)
        embedding = await self._embeddings.embed([summary_text], kind="query")
        return await self._vectors.search(
            embedding[0], query_text=None, top_k=min(top_k, 50), filters=None
        )

    async def _hydrate_hits(self, hits: list[ChunkHit]) -> list[ChunkHitRead]:
        """Attach document titles and presigned image URLs to hits.

        Args:
            hits: The raw chunk hits.

        Returns:
            The hydrated hits.
        """
        result: list[ChunkHitRead] = []
        for hit in hits:
            page = hit.page_from or 1
            key = _page_key(hit.document_id, page)
            image_url = await self._blob.presigned_url(key)
            result.append(
                ChunkHitRead(
                    document_id=hit.document_id,
                    chunk_index=hit.chunk_index,
                    text=hit.text,
                    score=hit.score,
                    page_from=hit.page_from,
                    page_to=hit.page_to,
                    document_title=hit.document_title,
                    page_image_url=image_url,
                )
            )
        return result


def _search_cache_key(req: SearchRequest) -> str:
    """Build a cache key from a search request.

    Args:
        req: The search request.

    Returns:
        A namespaced cache key.
    """
    from ipa.storage import cache_keys

    material = json.dumps(
        {
            "q": req.query,
            "mode": req.mode,
            "top_k": req.top_k,
            "filters": req.filters.model_dump() if req.filters else None,
            "group": req.group_by_document,
        },
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha256(material.encode()).hexdigest()
    return cache_keys.search(digest)


def _group_by_document(hits: list[ChunkHitRead]) -> list[Any]:
    """Collapse hits into one entry per document.

    Args:
        hits: The hydrated hits.

    Returns:
        A list of grouped hits with nested chunks.
    """
    from ipa.api.schemas.search import GroupedHit

    by_doc: dict[UUID, list[ChunkHitRead]] = {}
    for hit in hits:
        by_doc.setdefault(hit.document_id, []).append(hit)
    grouped: list[Any] = []
    for doc_id, chunks in by_doc.items():
        best = max(chunks, key=lambda c: c.score)
        grouped.append(
            GroupedHit(
                document_id=doc_id,
                document_title=best.document_title,
                best_score=best.score,
                chunks=sorted(chunks, key=lambda c: c.score, reverse=True),
            )
        )
    return sorted(grouped, key=lambda g: g.best_score, reverse=True)


def _matches_query(record: Any, where: dict[str, Any]) -> bool:
    """Evaluate a structured where-clause against an extraction record.

    Args:
        record: An extraction record with `fields`.
        where: Field key -> condition (value or operator object).

    Returns:
        True when every condition matches.
    """
    field_values = {field.key: field.value for field in record.fields}
    for key, condition in where.items():
        value = field_values.get(key)
        if isinstance(condition, dict) and any(
            op in condition for op in ("eq", "neq", "gte", "lte", "contains", "exists")
        ):
            if not _match_operator(value, condition):
                return False
        elif value != condition:
            return False
    return True


def _match_operator(value: Any, condition: dict[str, Any]) -> bool:
    """Evaluate one operator condition.

    Args:
        value: The field's value.
        condition: The operator mapping.

    Returns:
        True when the operator condition is satisfied.
    """
    if "exists" in condition:
        return (value is not None) == bool(condition["exists"])

    if "eq" in condition and condition["eq"] is not None:  # noqa: SIM102
        if value != condition["eq"]:
            return False
    if "neq" in condition and condition["neq"] is not None:  # noqa: SIM102
        if value == condition["neq"]:
            return False
    if "gte" in condition and condition["gte"] is not None:  # noqa: SIM102
        if value is None or value < condition["gte"]:
            return False
    if "lte" in condition and condition["lte"] is not None:  # noqa: SIM102
        if value is None or value > condition["lte"]:
            return False
    if "contains" in condition and condition["contains"] is not None:  # noqa: SIM102
        if value is None or condition["contains"] not in str(value):
            return False
    return True


def _page_key(document_id: UUID, page: int) -> str:
    """Build a page-image blob key.

    Args:
        document_id: The document id.
        page: The page number.

    Returns:
        The blob key.
    """
    from ipa.core.ids import page_image_key

    return page_image_key(document_id, page)
