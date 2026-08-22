"""PDF inspection, text-layer extraction and page rendering.

Built on PyMuPDF. PDFs are the most common container and the hardest to handle
correctly: encrypted files are quarantined, scanned files have no text layer and
must be OCR'd, and a garbage font mapping can produce a "text layer" that is
actually noise. This module provides the gate that decides which pages need OCR.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass

import pymupdf

from ipa.core.errors import QuarantineError

DEFAULT_ALNUM_RATIO = 0.55


@dataclass(frozen=True)
class PdfInfo:
    """Metadata about a PDF that the pipeline needs up front.

    Attributes:
        page_count: Number of pages.
        encrypted: Whether the file is encrypted without a usable password.
        has_text_layer: Per-page flag, True when the page has extractable text.
        producer: Producer metadata string, when present.
    """

    page_count: int
    encrypted: bool
    has_text_layer: list[bool]
    producer: str | None


def pdf_info(data: bytes) -> PdfInfo:
    """Inspect a PDF's structure.

    Args:
        data: The PDF bytes.

    Returns:
        A `PdfInfo` describing the file.

    Raises:
        QuarantineError: If the file cannot be opened as a PDF.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise QuarantineError(
            "The file is not a readable PDF.", code="invalid_pdf"
        ) from exc
    try:
        encrypted = bool(document.needs_pass)
        page_count = document.page_count
        has_text_layer = [_page_has_text(document, page) for page in range(page_count)]
        producer = document.metadata.get("producer") if document.metadata else None
        return PdfInfo(
            page_count=page_count,
            encrypted=encrypted,
            has_text_layer=has_text_layer,
            producer=producer,
        )
    finally:
        document.close()


def is_encrypted(data: bytes) -> bool:
    """Report whether a PDF is encrypted without a usable password.

    Args:
        data: The PDF bytes.

    Returns:
        True when the file requires a password to open.
    """
    return pdf_info(data).encrypted


def extract_text_layer(data: bytes, page: int) -> str:
    """Extract the native text layer of a page.

    Args:
        data: The PDF bytes.
        page: 0-based page index.

    Returns:
        The page's extracted text.

    Raises:
        QuarantineError: If the PDF cannot be opened.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise QuarantineError(
            "The file is not a readable PDF.", code="invalid_pdf"
        ) from exc
    try:
        return document[page].get_text()
    finally:
        document.close()


def has_usable_text_layer(
    text: str, min_chars: int, min_alnum_ratio: float = DEFAULT_ALNUM_RATIO
) -> bool:
    """Decide whether an extracted text layer is usable or garbage.

    A "text layer" from a mis-mapped font can contain characters that are not
    real text. Guard against that by requiring both a minimum character count
    and a minimum ratio of alphanumeric characters.

    Args:
        text: The extracted page text.
        min_chars: Minimum number of characters required.
        min_alnum_ratio: Minimum fraction of characters that are alphanumeric.

    Returns:
        True when the text is substantial and mostly real words.
    """
    if len(text) < min_chars:
        return False
    if not text:
        return False
    alnum = sum(1 for c in text if c.isalnum())
    return alnum / len(text) >= min_alnum_ratio


def render_page(data: bytes, page: int, dpi: int) -> bytes:
    """Render a single PDF page to PNG bytes.

    Args:
        data: The PDF bytes.
        page: 0-based page index.
        dpi: Render resolution in dots per inch.

    Returns:
        PNG-encoded image bytes.

    Raises:
        QuarantineError: If the PDF cannot be opened.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise QuarantineError(
            "The file is not a readable PDF.", code="invalid_pdf"
        ) from exc
    try:
        page_obj = document[page]
        zoom = dpi / 72.0
        pix = page_obj.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        document.close()


def render_pages(
    data: bytes, dpi: int, pages: list[int] | None = None
) -> Iterator[tuple[int, bytes]]:
    """Yield rendered pages as a generator so large PDFs stream, not buffer.

    Args:
        data: The PDF bytes.
        dpi: Render resolution.
        pages: 0-based page indices to render; all pages when None.

    Returns:
        An iterator of `(page_index, png_bytes)`.

    Raises:
        QuarantineError: If the PDF cannot be opened.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise QuarantineError(
            "The file is not a readable PDF.", code="invalid_pdf"
        ) from exc
    try:
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        indices = pages if pages is not None else list(range(document.page_count))
        for index in indices:
            yield index, document[index].get_pixmap(matrix=matrix).tobytes("png")
    finally:
        document.close()


def make_thumbnail(png: bytes, max_px: int = 320) -> bytes:
    """Downscale a PNG to a JPEG thumbnail.

    Args:
        png: Source PNG bytes.
        max_px: Maximum thumbnail dimension in pixels.

    Returns:
        JPEG-encoded thumbnail bytes.
    """
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    image.thumbnail((max_px, max_px))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=80)
    return output.getvalue()


def _page_has_text(document: pymupdf.Document, page: int) -> bool:
    """Report whether a page has any extractable text.

    Args:
        document: The open PDF document.
        page: 0-based page index.

    Returns:
        True when the page's extracted text is non-empty.
    """
    try:
        return bool(document[page].get_text().strip())
    except Exception:
        return False
