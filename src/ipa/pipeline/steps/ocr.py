"""OCR step: produce clean text per page with a fallback chain.

Registered as the `ocr` step handler. For each page, resolves text with the
cheapest method that works: the native text layer when T09 flagged it, then the
NVIDIA VLM, then local Tesseract when fallback is enabled. Results persist to
MongoDB and the `document_pages` index. Blank pages are skipped; per-page
failures are tolerated up to a configurable ratio.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from ipa.contracts.models import PageText, StepContext, StepResult
from ipa.core.config import Settings
from ipa.core.enums import PipelineStep, StepStatus
from ipa.core.errors import ProviderError
from ipa.db.dtos import DocumentPageDto
from ipa.db.repositories.page import PageRepository
from ipa.pipeline.registry import register
from ipa.processing import images as image_utils
from ipa.processing import pdf as pdf_utils

logger = structlog.get_logger(__name__)

_TEXT_LAYER = "text_layer"
_VLM = "vlm"
_TESSERACT = "tesseract"
_BLANK = "blank"


@dataclass
class _PageOutcome:
    """The resolved text and provenance for one page."""

    page: int
    text: str
    source: str
    confidence: float | None = None
    raw: str | None = None
    failed: bool = False


@register(PipelineStep.OCR)
async def ocr_handler(context: StepContext) -> StepResult:
    """Run OCR for every page of a document.

    Args:
        context: The step context.

    Returns:
        A step result with per-page metrics, or a failed status when too many
        pages could not be resolved.
    """
    from ipa.core.config import get_settings
    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.session import session_scope
    from ipa.storage.factory import get_content_store

    settings = get_settings()
    content = get_content_store()

    async with session_scope() as session:
        pages_repo = PageRepository(session)
        documents = DocumentRepository(session)
        document = await documents.get(context.document_id)
        if document is None:
            return StepResult(status=StepStatus.FAILED, detail="document not found")

        page_rows = await pages_repo.list_pages(context.document_id)
        if not page_rows:
            return StepResult(status=StepStatus.SKIPPED, detail="no pages to OCR")

        outcomes = await _process_pages(context, document.blob_key, page_rows, settings)

        page_texts = [
            PageText(
                page=outcome.page,
                text=outcome.text,
                source=outcome.source,
                confidence=outcome.confidence,
                char_count=len(outcome.text),
            )
            for outcome in outcomes
        ]
        await content.put_pages(context.document_id, page_texts)

        for outcome in outcomes:
            await pages_repo.update_page(
                context.document_id,
                outcome.page,
                text_source=outcome.source,
                char_count=len(outcome.text),
                ocr_confidence=outcome.confidence,
            )

        for outcome in outcomes:
            if outcome.raw is not None and outcome.source == _VLM:
                await content.put_raw_response(
                    context.document_id,
                    "ocr",
                    model="vlm",
                    response_text=outcome.raw,
                    request_summary=f"page {outcome.page}",
                )

        metrics = _metrics(outcomes)
        failed = metrics["failed_pages"]
        if failed == metrics["pages"]:
            return StepResult(status=StepStatus.FAILED, metrics=metrics)
        ratio = failed / metrics["pages"] if metrics["pages"] else 0.0
        if ratio > settings.ocr.max_failed_page_ratio:
            return StepResult(status=StepStatus.FAILED, metrics=metrics)
        return StepResult(status=StepStatus.SUCCEEDED, metrics=metrics)


async def _process_pages(
    context: StepContext,
    blob_key: str,
    page_rows: list[DocumentPageDto],
    settings: Settings,
) -> list[_PageOutcome]:
    """Resolve text for all pages with bounded concurrency.

    Args:
        context: The step context.
        blob_key: The document's original blob key.
        page_rows: The document's page rows.
        settings: Application settings.

    Returns:
        A list of page outcomes, one per input page.
    """
    semaphore = asyncio.Semaphore(settings.ocr.page_concurrency)

    async def process(row: DocumentPageDto) -> _PageOutcome:
        async with semaphore:
            return await _process_page(context, blob_key, row, settings)

    results = await asyncio.gather(
        *(process(row) for row in page_rows), return_exceptions=True
    )
    outcomes: list[_PageOutcome] = []
    for page, result in zip(page_rows, results, strict=True):
        if isinstance(result, Exception):
            outcomes.append(
                _PageOutcome(page=page.page, text="", source=_VLM, failed=True)
            )
        elif isinstance(result, _PageOutcome):
            outcomes.append(result)
        else:  # pragma: no cover - BaseException from gather
            outcomes.append(
                _PageOutcome(page=page.page, text="", source=_VLM, failed=True)
            )
    return sorted(outcomes, key=lambda o: o.page)


async def _process_page(
    context: StepContext,
    blob_key: str,
    row: DocumentPageDto,
    settings: Settings,
) -> _PageOutcome:
    """Resolve text for a single page using the cheapest working method.

    Args:
        context: The step context.
        blob_key: The document's original blob key.
        row: The page row.
        settings: Application settings.

    Returns:
        The page outcome.
    """
    from ipa.storage.factory import get_blob_store

    raw = await get_blob_store().get(blob_key)
    image = await asyncio.to_thread(
        pdf_utils.render_page, raw, row.page - 1, settings.ocr.page_dpi
    )

    if await asyncio.to_thread(image_utils.is_probably_blank, image):
        return _PageOutcome(page=row.page, text="", source=_BLANK)

    text_layer = _extract_text_layer(raw, row)
    if text_layer is not None and pdf_utils.has_usable_text_layer(
        text_layer, settings.ocr.text_layer_min_chars
    ):
        return _PageOutcome(
            page=row.page, text=text_layer, source=_TEXT_LAYER, confidence=1.0
        )

    vlm = await _try_vlm(context, image, row.page, settings)
    if vlm is not None:
        return vlm

    if settings.ocr.fallback_enabled:
        tesseract = await _try_tesseract(image, row.page, settings)
        if tesseract is not None:
            return tesseract

    source = _VLM if not settings.ocr.fallback_enabled else _TESSERACT
    return _PageOutcome(page=row.page, text="", source=source, failed=True)


def _extract_text_layer(raw: bytes, row: DocumentPageDto) -> str | None:
    """Extract a page's native text layer if T09 flagged it usable.

    Args:
        raw: The document's original bytes.
        row: The page row.

    Returns:
        The extracted text, or None when the page was not flagged.
    """
    if getattr(row, "text_source", None) != _TEXT_LAYER:
        return None
    return pdf_utils.extract_text_layer(raw, row.page - 1)


async def _try_vlm(
    context: StepContext, image: bytes, page: int, settings: Settings
) -> _PageOutcome | None:
    """Run the NVIDIA VLM on a page image.

    Args:
        context: The step context.
        image: The rendered page PNG.
        page: The 1-based page number.
        settings: Application settings.

    Returns:
        The VLM outcome, or None if it failed or returned too little text.
    """
    if not settings.features.vlm_ocr_enabled:
        return None
    downscaled = await asyncio.to_thread(image_utils.downscale_for_vlm, image)

    from ipa.providers.factory import get_vlm_ocr_provider

    provider = get_vlm_ocr_provider()
    try:
        result = await provider.ocr_image(downscaled, "image/png")
    except ProviderError as exc:
        logger.warning("step.ocr.vlm_failed", page=page, error=str(exc))
        return None

    if result.text is None or len(result.text) < settings.ocr.min_chars_accept:
        return None
    confidence = _vlm_heuristic_confidence(result.text)
    return _PageOutcome(
        page=page, text=result.text, source=_VLM, confidence=confidence, raw=result.text
    )


async def _try_tesseract(
    image: bytes, page: int, settings: Settings
) -> _PageOutcome | None:
    """Run local Tesseract on a page image.

    Args:
        image: The rendered page PNG.
        page: The 1-based page number.
        settings: Application settings.

    Returns:
        The Tesseract outcome, or None on failure.
    """
    deskewed = await asyncio.to_thread(image_utils.deskew, image)

    from ipa.providers.factory import get_tesseract_provider

    provider = get_tesseract_provider()
    try:
        result = await provider.ocr_image(deskewed, "image/png")
    except ProviderError as exc:
        logger.warning("step.ocr.tesseract_failed", page=page, error=str(exc))
        return None
    return _PageOutcome(
        page=page, text=result.text, source=_TESSERACT, confidence=result.confidence
    )


def _vlm_heuristic_confidence(text: str) -> float:
    """Derive a heuristic "text quality" score for VLM output.

    This is not a model-reported confidence: VLMs do not provide calibrated OCR
    confidence. It proxies text quality from the alphanumeric ratio and length,
    and the API/UI present it as "text quality" rather than a model score.

    Args:
        text: The transcribed text.

    Returns:
        A score in [0, 1].
    """
    if not text:
        return 0.0
    alnum = sum(1 for c in text if c.isalnum())
    alnum_ratio = alnum / len(text)
    length_factor = min(len(text) / 400, 1.0)
    return min(1.0, alnum_ratio * 0.5 + length_factor * 0.5)


def _metrics(outcomes: list[_PageOutcome]) -> dict[str, float]:
    """Compute the step metrics from page outcomes.

    Args:
        outcomes: The per-page outcomes.

    Returns:
        A metrics dict.
    """
    total_chars = sum(len(o.text) for o in outcomes)
    return {
        "pages": float(len(outcomes)),
        "text_layer_pages": float(sum(1 for o in outcomes if o.source == _TEXT_LAYER)),
        "vlm_pages": float(sum(1 for o in outcomes if o.source == _VLM)),
        "tesseract_pages": float(sum(1 for o in outcomes if o.source == _TESSERACT)),
        "blank_pages": float(sum(1 for o in outcomes if o.source == _BLANK)),
        "failed_pages": float(sum(1 for o in outcomes if o.failed)),
        "total_chars": float(total_chars),
        "provider_ms": 0.0,
    }
