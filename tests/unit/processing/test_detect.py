"""MIME detection, hashing and container classification."""

from __future__ import annotations

import pytest

from ipa.processing.detect import (
    classify_container,
    is_allowed,
    sha256_hex,
    sniff_mime,
)


def test_sniff_mime_by_magic_bytes_ignores_extension() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest"
    assert sniff_mime(png_bytes, "innocent.pdf") == "image/png"
    assert sniff_mime(b"%PDF-1.4", "notes.zip") == "application/pdf"
    assert sniff_mime(b"PK\x03\x04", "data.png") == "application/zip"


def test_sniff_mime_uses_extension_as_tiebreak() -> None:
    assert sniff_mime(b"no magic here", "document.pdf") == "application/pdf"
    assert sniff_mime(b"no magic here", "image.jpg") == "image/jpeg"
    assert sniff_mime(b"no magic here", "random.bin") == "application/octet-stream"


def test_sniff_mime_detects_tiff_variants() -> None:
    assert sniff_mime(b"II*\x00", "x") == "image/tiff"
    assert sniff_mime(b"MM\x00*", "x") == "image/tiff"


def test_is_allowed() -> None:
    allowed = {"application/pdf", "image/png"}
    assert is_allowed("application/pdf", allowed) is True
    assert is_allowed("application/zip", allowed) is False


def test_sha256_hex_deterministic() -> None:
    assert sha256_hex(b"hello") == sha256_hex(b"hello")
    assert sha256_hex(b"hello") != sha256_hex(b"world")
    assert len(sha256_hex(b"hello")) == 64


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/pdf", "pdf"),
        ("application/zip", "archive"),
        ("image/png", "image"),
        ("image/tiff", "image"),
        ("text/plain", "unsupported"),
    ],
)
def test_classify_container(mime: str, expected: str) -> None:
    assert classify_container(mime) == expected
