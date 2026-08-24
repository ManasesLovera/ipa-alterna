"""Search and RAG REST router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from ipa.api.auth import require_auth
from ipa.api.schemas.search import (
    FieldQuery,
    RagRequest,
    RagResponse,
    SearchRequest,
    SearchResponse,
)
from ipa.db.session import get_sessionmaker
from ipa.db.vector import PgVectorStore
from ipa.domain.search import SearchService
from ipa.providers.factory import get_embedding_provider, get_llm_provider
from ipa.storage.factory import blob_store_dep, cache_store_dep, content_store_dep

router = APIRouter(tags=["search"], prefix="/search")

AuthDep = Annotated[object, Depends(require_auth)]


def _service() -> SearchService:
    """Assemble a SearchService from the process singletons.

    Returns:
        A `SearchService` instance.
    """
    return SearchService(
        embeddings=get_embedding_provider(),
        vectors=PgVectorStore(get_sessionmaker()),
        content=content_store_dep(),
        llm=get_llm_provider(),
        cache=cache_store_dep(),
        blob=blob_store_dep(),
    )


@router.post("", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    _auth: AuthDep,
) -> SearchResponse:
    """Run a hybrid/dense/sparse search.

    Args:
        req: The search request.
        _auth: The authenticated principal.

    Returns:
        The search response.
    """
    return await _service().search(req)


@router.post("/documents")
async def search_documents(
    req: FieldQuery,
    _auth: AuthDep,
) -> list[dict[str, object]]:
    """Search current extractions by structured field queries.

    Args:
        req: The field query.
        _auth: The authenticated principal.

    Returns:
        The matching extraction records.
    """
    return await _service().search_documents(req)


@router.post("/rag/query", response_model=RagResponse)
async def rag_query(
    req: RagRequest,
    _auth: AuthDep,
) -> RagResponse:
    """Answer a question grounded in retrieved chunks.

    Args:
        req: The RAG request.
        _auth: The authenticated principal.

    Returns:
        A RAG response with sources.
    """
    return await _service().rag(req)


@router.get("/similar/{document_id}", response_model=list[dict[str, object]])
async def similar(
    document_id: UUID,
    _auth: AuthDep,
    top_k: int = Query(default=5, ge=1, le=50),
) -> list[dict[str, object]]:
    """Find documents similar to a document's summary chunk.

    Args:
        document_id: Identifier of the document.
        _auth: The authenticated principal.
        top_k: Maximum number of neighbours.

    Returns:
        The nearest chunk hits.
    """
    hits = await _service().similar(document_id, top_k)
    return [hit.model_dump() for hit in hits]
