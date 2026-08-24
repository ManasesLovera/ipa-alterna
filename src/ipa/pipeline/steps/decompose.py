"""Decompose step: turn an upload into normalised page images + a page index.

Registered as the `decompose` step handler. Dispatches by container type: PDFs
render every page to PNG and record text-layer hints; images normalise to a
single page; ZIP archives expand into child documents. Renders are CPU-bound and
run in a thread executor, streaming page by page so a large scan never buffers.
"""

from __future__ import annotations

import asyncio
import time
from uuid import UUID

import structlog

from ipa.contracts.models import StepContext, StepResult
from ipa.contracts.protocols import BlobStore
from ipa.core.config import Settings
from ipa.core.enums import PipelineStep, StepStatus
from ipa.core.errors import QuarantineError, UnsupportedMediaError
from ipa.core.ids import page_image_key, thumbnail_key
from ipa.db.dtos import DocumentDto
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.page import PageRepository
from ipa.pipeline.registry import register
from ipa.processing import detect
from ipa.processing import images as image_utils
from ipa.processing import pdf as pdf_utils
from ipa.storage.factory import get_blob_store

logger = structlog.get_logger(__name__)


@register(PipelineStep.DECOMPOSE)
async def decompose_handler(context: StepContext) -> StepResult:
    """Decompose a document's blob into page images and a page index.

    Args:
        context: The step context.

    Returns:
        A succeeded result with `pages`, `render_ms`, `bytes_written`,
        `text_layer_pages` and `children_created` metrics, or a quarantine/error
        status.

    Raises:
        QuarantineError: For encrypted PDFs, zip bombs or page-ceiling breaches.
        UnsupportedMediaError: For container types that cannot be processed.
    """
    from ipa.core.config import get_settings
    from ipa.db.session import session_scope

    settings = get_settings()
    blob = get_blob_store()

    async with session_scope() as session:
        documents = DocumentRepository(session)
        pages = PageRepository(session)
        document = await documents.get(context.document_id)
        if document is None:
            return StepResult(status=StepStatus.FAILED, detail="document not found")

        raw = await blob.get(document.blob_key)
        mime = detect.sniff_mime(raw, document.original_filename)
        kind = detect.classify_container(mime)

        started = time.monotonic()
        if kind == "pdf":
            metrics = await _decompose_pdf(context, raw, pages, documents, blob, settings)
        elif kind == "image":
            metrics = await _decompose_image(context, raw, mime, pages, documents, blob, settings)
        elif kind == "archive":
            metrics = await _decompose_archive(
                context, document, raw, documents, pages, blob
            )
        else:
            raise UnsupportedMediaError(
                f"Cannot decompose container of type {mime}.", code="unsupported_media"
            )

        metrics["render_ms"] = int((time.monotonic() - started) * 1000)
        return StepResult(status=StepStatus.SUCCEEDED, metrics=metrics)


async def _decompose_pdf(
    context: StepContext,
    raw: bytes,
    pages: PageRepository,
    documents: DocumentRepository,
    blob: BlobStore,
    settings: Settings,
) -> dict[str, float]:
    """Render a PDF's pages to PNG and build the page index.

    Args:
        context: The step context.
        raw: The PDF bytes.
        pages: The page repository.
        documents: The document repository.
        blob: The blob store.
        settings: Application settings (for DPI and page ceiling).

    Returns:
        A metrics dict.

    Raises:
        QuarantineError: If the PDF is encrypted or exceeds the page ceiling.
    """
    info = pdf_utils.pdf_info(raw)
    if info.encrypted:
        raise QuarantineError("The PDF is encrypted.", code="pdf_encrypted")
    if info.page_count > settings.pipeline.max_pages:
        raise QuarantineError(
            f"PDF has {info.page_count} pages, exceeding the ceiling "
            f"of {settings.pipeline.max_pages}.",
            code="too_many_pages",
        )

    dpi = settings.ocr.page_dpi
    min_chars = settings.ocr.text_layer_min_chars
    rows: list[dict] = []
    bytes_written = 0
    text_layer_pages = 0

    for page in range(info.page_count):
        png = await asyncio.to_thread(pdf_utils.render_page, raw, page, dpi)
        thumb = await asyncio.to_thread(pdf_utils.make_thumbnail, png)
        text = await asyncio.to_thread(pdf_utils.extract_text_layer, raw, page)
        has_text = pdf_utils.has_usable_text_layer(text, min_chars)

        blank = await asyncio.to_thread(image_utils.is_probably_blank, png)
        width, height = await asyncio.to_thread(image_utils.image_dimensions, png)

        key = page_image_key(context.document_id, page + 1)
        thumb_key = thumbnail_key(context.document_id, page + 1)
        await blob.put(key, png, "image/png")
        await blob.put(thumb_key, thumb, "image/jpeg")
        bytes_written += len(png) + len(thumb)

        row = {
            "page": page + 1,
            "blob_key": key,
            "thumb_key": thumb_key,
            "width": width,
            "height": height,
            "text_source": "text_layer" if has_text else None,
            "char_count": len(text) if has_text else None,
            "ocr_confidence": None,
        }
        if blank:
            row["text_source"] = None
        rows.append(row)
        if has_text and not blank:
            text_layer_pages += 1

    await pages.replace_all(context.document_id, rows)
    await _patch_page_count(documents, context.document_id, len(rows))
    return {
        "pages": float(len(rows)),
        "bytes_written": float(bytes_written),
        "text_layer_pages": float(text_layer_pages),
        "children_created": 0.0,
    }


async def _decompose_image(
    context: StepContext,
    raw: bytes,
    mime: str,
    pages: PageRepository,
    documents: DocumentRepository,
    blob: BlobStore,
    settings: Settings,
) -> dict[str, float]:
    """Normalise an image to a single PNG page.

    Args:
        context: The step context.
        raw: The image bytes.
        mime: The detected MIME type.
        pages: The page repository.
        documents: The document repository.
        blob: The blob store.
        settings: Application settings.

    Returns:
        A metrics dict.
    """
    png, _ = await asyncio.to_thread(image_utils.normalise_image, raw)
    thumb = await asyncio.to_thread(pdf_utils.make_thumbnail, png)
    width, height = await asyncio.to_thread(image_utils.image_dimensions, png)

    key = page_image_key(context.document_id, 1)
    thumb_key = thumbnail_key(context.document_id, 1)
    await blob.put(key, png, "image/png")
    await blob.put(thumb_key, thumb, "image/jpeg")

    row = {
        "page": 1,
        "blob_key": key,
        "thumb_key": thumb_key,
        "width": width,
        "height": height,
        "text_source": None,
        "char_count": None,
        "ocr_confidence": None,
    }
    await pages.replace_all(context.document_id, [row])
    await _patch_page_count(documents, context.document_id, 1)
    return {
        "pages": 1.0,
        "bytes_written": float(len(png) + len(thumb)),
        "text_layer_pages": 0.0,
        "children_created": 0.0,
    }


async def _decompose_archive(
    context: StepContext,
    document: DocumentDto,
    raw: bytes,
    documents: DocumentRepository,
    pages: PageRepository,
    blob: BlobStore,
) -> dict[str, float]:
    """Expand a ZIP into child documents.

    Args:
        context: The step context.
        document: The parent document DTO.
        raw: The ZIP bytes.
        documents: The document repository.
        pages: The page repository.
        blob: The blob store.

    Returns:
        A metrics dict with the number of children created.

    Raises:
        QuarantineError: If the archive is unsafe.
    """
    from ipa.core.config import get_settings
    from ipa.processing.archive import extract_archive

    settings = get_settings()
    allowed = set(settings.allowed_mime_types)
    files = extract_archive(raw, allowed)

    children_created = 0
    for entry in files:
        child_id = await _ingest_child(
            context, document, entry.data, entry.name, documents
        )
        if child_id is not None:
            children_created += 1

    return {
        "pages": 0.0,
        "bytes_written": 0.0,
        "text_layer_pages": 0.0,
        "children_created": float(children_created),
    }


async def _ingest_child(
    context: StepContext,
    parent: DocumentDto,
    data: bytes,
    name: str,
    documents: DocumentRepository,
) -> UUID | None:
    """Ingest one archive entry as a child document.

    Args:
        context: The parent step context.
        parent: The parent document DTO.
        data: The child's bytes.
        name: The archive entry name.
        documents: The document repository.

    Returns:
        The child's document id, or None if it deduplicated to an existing one.
    """
    from ipa.api.schemas.documents import UploadPayload
    from ipa.db.repositories.event import EventRepository
    from ipa.db.repositories.step import StepRepository
    from ipa.db.session import session_scope
    from ipa.domain.documents import DocumentService
    from ipa.pipeline.runner import _enqueue
    from ipa.storage.factory import get_content_store

    parent_id = parent.id
    parent_tag = parent.tag_id
    meta = {"parent_document_id": str(parent_id), "archive_entry_name": name}

    payload = UploadPayload(
        filename=name,
        data=data,
        mime_type=None,
        tag_id=parent_tag,
        metadata=meta,
        source="api",
    )

    async with session_scope() as session:
        service = DocumentService(
            documents=DocumentRepository(session),
            steps=StepRepository(session),
            events=EventRepository(session),
            blob=get_blob_store(),
            content=get_content_store(),
        )
        result = await service.ingest(payload)
        if result.deduplicated:
            return None
        from ipa.core.enums import PipelineStep

        _enqueue(result.document.id, PipelineStep.DECOMPOSE)
        return result.document.id


async def _patch_page_count(
    documents: DocumentRepository, document_id: UUID, count: int
) -> None:
    """Update a document's page count on the open session.

    Args:
        documents: The document repository.
        document_id: Identifier of the document.
        count: The new page count.
    """
    from ipa.core.enums import DocumentStatus
    from ipa.db.dtos import DocumentPatch

    await documents.patch(
        document_id, DocumentPatch(page_count=count, status=DocumentStatus.PROCESSING)
    )
