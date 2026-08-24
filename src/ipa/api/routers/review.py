"""Review REST router: queue, decision payload and reviewer actions."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import require_auth
from ipa.api.schemas.review import (
    ApproveRequest,
    AssignTagRequest,
    BulkApproveRequest,
    CorrectRequest,
    RejectRequest,
    ReviewPayload,
)
from ipa.contracts.protocols import ContentStore
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.extraction import ExtractionRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.repositories.tag import TagRepository
from ipa.db.session import get_session
from ipa.domain.validation import ValidationService
from ipa.storage.factory import content_store_dep

router = APIRouter(tags=["review"], prefix="/review")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[object, Depends(require_auth)]


def _service(
    session: AsyncSession, content: ContentStore
) -> ValidationService:
    """Assemble a ValidationService bound to a session and content store.

    Args:
        session: The request session.
        content: The content store.

    Returns:
        A `ValidationService` instance.
    """
    return ValidationService(
        documents=DocumentRepository(session),
        extractions=ExtractionRepository(session),
        steps=StepRepository(session),
        tags=TagRepository(session),
        content=content,
    )


@router.get("/queue")
async def review_queue(
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    tag_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    """List documents awaiting review, worst confidence first.

    Args:
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.
        tag_id: Filter by tag.
        limit: Maximum results.

    Returns:
        A dict with `items` and `next_cursor`.
    """
    documents = await DocumentRepository(session).list_documents(
        needs_review=True, limit=limit
    )
    items = []
    for doc in documents:
        if tag_id and str(doc.tag_id) != tag_id:
            continue
        items.append(
            {
                "document_id": str(doc.id),
                "filename": doc.original_filename,
                "status": str(doc.status),
                "confidence": doc.document_confidence,
                "created_at": str(doc.created_at),
            }
        )
    items.sort(key=lambda item: (item["confidence"] is None, item["confidence"] or 0))
    return {"items": items, "next_cursor": None}


@router.get("/queue/stats")
async def review_stats(
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, object]:
    """Return review-queue statistics.

    Args:
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        A dict with counts by status.
    """
    documents = await DocumentRepository(session).list_documents(needs_review=True, limit=1000)
    return {"pending_review_count": len(documents)}


@router.get("/{document_id}", response_model=ReviewPayload)
async def review_payload(
    document_id: UUID,
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> ReviewPayload:
    """Return everything a review screen needs in one round trip.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        The review payload.
    """
    record = await content.get_extraction(document_id)
    return ReviewPayload(
        document_id=str(document_id),
        extraction=record.model_dump() if record else None,
        tag_schema=None,
    )


@router.post("/{document_id}/approve")
async def approve(
    document_id: UUID,
    body: ApproveRequest,
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> dict[str, str]:
    """Approve a document's current extraction as-is.

    Args:
        document_id: Identifier of the document.
        body: The approval request.
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        A status message.
    """
    await _service(session, content).approve(document_id, notes=body.notes)
    return {"status": "approved"}


@router.post("/{document_id}/correct")
async def correct(
    document_id: UUID,
    body: CorrectRequest,
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> dict[str, str]:
    """Create a new HUMAN extraction version from corrections.

    Args:
        document_id: Identifier of the document.
        body: The corrections.
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        A status message.
    """
    await _service(session, content).correct(
        document_id,
        body.fields,
        expected_version=body.expected_version,
        notes=body.notes,
    )
    return {"status": "corrected"}


@router.post("/{document_id}/reject")
async def reject(
    document_id: UUID,
    body: RejectRequest,
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> dict[str, str]:
    """Reject a document, optionally reprocessing from extract.

    Args:
        document_id: Identifier of the document.
        body: The rejection request.
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        A status message.
    """
    if body.action == "reprocess":
        from ipa.core.enums import PipelineStep
        from ipa.pipeline.orchestrator import Orchestrator
        from ipa.pipeline.runner import _enqueue

        await Orchestrator(
            DocumentRepository(session), StepRepository(session), _enqueue
        ).reprocess(document_id, PipelineStep.EXTRACT)
    return {"status": "rejected"}


@router.post("/{document_id}/assign-tag")
async def assign_tag(
    document_id: UUID,
    body: AssignTagRequest,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, str]:
    """Assign a tag to a parked document and reprocess from extract.

    Args:
        document_id: Identifier of the document.
        body: The tag assignment.
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        A status message.
    """
    from ipa.core.enums import PipelineStep
    from ipa.db.dtos import DocumentPatch
    from ipa.pipeline.orchestrator import Orchestrator
    from ipa.pipeline.runner import _enqueue

    await DocumentRepository(session).patch(document_id, DocumentPatch(tag_id=UUID(body.tag_id)))
    await Orchestrator(
        DocumentRepository(session), StepRepository(session), _enqueue
    ).reprocess(document_id, PipelineStep.EXTRACT)
    return {"status": "assigned"}


@router.post("/bulk/approve")
async def bulk_approve(
    body: BulkApproveRequest,
    session: SessionDep,
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> dict[str, int]:
    """Bulk-approve documents, capped at 100 per call.

    Args:
        body: The document ids.
        session: The request session.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        A dict with the number approved.
    """
    if len(body.document_ids) > 100:
        raise HTTPException(status_code=422, detail="too many document ids")
    service = _service(session, content)
    count = 0
    for document_id in body.document_ids:
        try:
            await service.approve(UUID(document_id))
            count += 1
        except Exception:
            continue
    return {"approved": count}
