"""Documents REST router: upload, read, list, download."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import require_auth
from ipa.api.schemas.documents import (
    ContentRead,
    DocumentPatch,
    DocumentRead,
    EventRead,
    ExtractionRead,
    PageRead,
    StepRead,
)
from ipa.contracts.protocols import BlobStore, ContentStore
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.event import EventRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.session import get_session
from ipa.domain.documents import DocumentService
from ipa.storage.factory import blob_store_dep, content_store_dep

router = APIRouter(tags=["documents"], prefix="/documents")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[object, Depends(require_auth)]


def _service(
    session: AsyncSession, blob: BlobStore, content: ContentStore
) -> DocumentService:
    """Assemble a DocumentService bound to the request dependencies.

    Args:
        session: The request session.
        blob: The blob store.
        content: The content store.

    Returns:
        A `DocumentService` instance.
    """
    return DocumentService(
        documents=DocumentRepository(session),
        steps=StepRepository(session),
        events=EventRepository(session),
        blob=blob,
        content=content,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentRead)
async def upload(
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    file: Annotated[UploadFile, File()],
    tag_id: Annotated[UUID | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
) -> DocumentRead:
    """Upload a document file for ingestion.

    Args:
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        file: The uploaded file.
        tag_id: Optional tag to assign.
        title: Optional human-facing title.

    Returns:
        The created document.
    """
    data = await file.read()
    from ipa.api.schemas.documents import UploadPayload

    payload = UploadPayload(
        filename=file.filename or "upload",
        data=data,
        mime_type=file.content_type,
        tag_id=tag_id,
        title=title,
    )
    result = await _service(session, blob, content).ingest(payload)
    return result.document


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[DocumentRead]:
    """List documents, newest first.

    Args:
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        limit: Maximum results.

    Returns:
        The documents.
    """
    from ipa.api.schemas.documents import DocumentFilters, PageParams

    page = await _service(session, blob, content).list_documents(
        DocumentFilters(), PageParams(limit=limit)
    )
    return page["items"] or []


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> DocumentRead:
    """Fetch a document.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        The document.
    """
    return await _service(session, blob, content).get(document_id)


@router.get("/{document_id}/steps", response_model=list[StepRead])
async def get_steps(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> list[StepRead]:
    """Return a document's per-step execution state.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        The steps.
    """
    return await _service(session, blob, content).steps(document_id)


@router.get("/{document_id}/events", response_model=list[EventRead])
async def get_events(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[EventRead]:
    """Return a document's audit log.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        limit: Maximum results.

    Returns:
        The events.
    """
    from ipa.api.schemas.documents import PageParams

    page = await _service(session, blob, content).events(
        document_id, PageParams(limit=limit)
    )
    return page["items"] or []


@router.get("/{document_id}/pages", response_model=list[PageRead])
async def get_pages(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> list[PageRead]:
    """Return a document's page index.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        The pages.
    """
    return await _service(session, blob, content).pages(document_id)


@router.get("/{document_id}/content", response_model=ContentRead)
async def get_content(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    page: int | None = Query(default=None, ge=1),
) -> ContentRead:
    """Return OCR text for a document or one page.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        page: Restrict to one page when set.

    Returns:
        The content.
    """
    return await _service(session, blob, content).content(document_id, page=page)


@router.get("/{document_id}/extraction", response_model=ExtractionRead | None)
async def get_extraction(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    version: int | None = Query(default=None, ge=1),
) -> ExtractionRead | None:
    """Return a document's extraction version.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        version: Version to return; latest when None.

    Returns:
        The extraction, or None when none exists.
    """
    return await _service(session, blob, content).extraction(document_id, version=version)


@router.get("/{document_id}/download")
async def download(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> dict[str, str]:
    """Return a presigned download URL for the document.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        A dict with the download `url`.
    """
    url = await _service(session, blob, content).download_url(document_id)
    return {"url": url}


@router.patch("/{document_id}", response_model=DocumentRead)
async def update_document(
    document_id: UUID,
    patch: DocumentPatch,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
) -> DocumentRead:
    """Update a document's editable columns.

    Args:
        document_id: Identifier of the document.
        patch: The columns to change.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.

    Returns:
        The updated document.
    """
    return await _service(session, blob, content).update(document_id, patch)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    session: SessionDep,
    blob: Annotated[BlobStore, Depends(blob_store_dep)],
    content: Annotated[ContentStore, Depends(content_store_dep)],
    _auth: AuthDep,
    hard: bool = Query(default=False),
) -> None:
    """Delete a document, soft by default, hard when `hard=true`.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        blob: The blob store.
        content: The content store.
        _auth: The authenticated principal.
        hard: Cascade the delete across all stores when True.

    Returns:
        None.
    """
    await _service(session, blob, content).delete(document_id, hard=hard)
