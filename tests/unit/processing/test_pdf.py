"""PDF inspection, text-layer gating, rendering and thumbnail behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from ipa.core.errors import QuarantineError
from ipa.processing.pdf import (
    extract_text_layer,
    has_usable_text_layer,
    is_encrypted,
    make_thumbnail,
    pdf_info,
    render_page,
    render_pages,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_text_layer_pdf_has_usable_text() -> None:
    data = _read("text-layer.pdf")
    info = pdf_info(data)

    assert info.encrypted is False
    assert info.page_count == 1
    assert info.has_text_layer == [True]
    text = extract_text_layer(data, 0)
    assert "Invoice ACME-2024-001" in text
    assert has_usable_text_layer(text, min_chars=120) is True


def test_scanned_pdf_has_no_text_layer() -> None:
    data = _read("scanned.pdf")
    info = pdf_info(data)

    assert info.has_text_layer == [False]
    text = extract_text_layer(data, 0)
    assert has_usable_text_layer(text, min_chars=120) is False


def test_encrypted_pdf_is_flagged() -> None:
    data = _read("encrypted.pdf")

    assert is_encrypted(data) is True


def test_render_page_produces_png() -> None:
    data = _read("text-layer.pdf")

    png = render_page(data, 0, dpi=150)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_pages_streams_all_pages() -> None:
    data = _read("text-layer.pdf")

    pages = list(render_pages(data, dpi=150))

    assert len(pages) == 1
    assert pages[0][0] == 0
    assert pages[0][1][:8] == b"\x89PNG\r\n\x1a\n"


def test_make_thumbnail_is_jpeg() -> None:
    data = _read("text-layer.pdf")
    png = render_page(data, 0, dpi=150)

    thumb = make_thumbnail(png)

    assert thumb[:2] == b"\xff\xd8"


def test_has_usable_text_layer_rejects_garbage() -> None:
    assert has_usable_text_layer("a" * 200, min_chars=120) is True
    assert has_usable_text_layer("", min_chars=120) is False
    # Garbage font mapping: lots of non-alphanumeric symbols.
    garbage = "\ufffd\ufffd\u25a0\u25a0\u25a0\u25a0" * 50
    assert has_usable_text_layer(garbage, min_chars=120, min_alnum_ratio=0.55) is False


def test_pdf_info_raises_quarantine_for_garbage() -> None:
    with pytest.raises(QuarantineError):
        pdf_info(b"not a pdf at all")
