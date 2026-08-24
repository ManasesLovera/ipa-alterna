"""Decompose step: PDF/image rendering into a page index."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ipa.contracts.models import StepContext
from ipa.core.config import Settings
from ipa.core.enums import PipelineStep
from ipa.core.errors import QuarantineError
from ipa.pipeline.steps.decompose import _decompose_image, _decompose_pdf

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


class FakeBlob:
    """In-memory BlobStore."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_keys: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self.objects[key] = data
        self.put_keys.append(key)
        return key


class FakeDocuments:
    """In-memory DocumentRepository for the page-count patch."""

    def __init__(self) -> None:
        self.patches: list[Any] = []

    async def patch(self, document_id: Any, patch: Any) -> Any:
        self.patches.append(patch)
        return None


class FakePages:
    """In-memory PageRepository."""

    def __init__(self) -> None:
        self.replaced: list[list[dict[str, Any]]] = []

    async def replace_all(self, document_id: Any, pages: list[dict[str, Any]]) -> list[Any]:
        self.replaced.append(pages)
        return []


def _context() -> StepContext:
    return StepContext(document_id=uuid4(), step=PipelineStep.DECOMPOSE, attempt=1)


def _settings() -> Settings:
    return Settings(_env_file=None)


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def test_pdf_decompose_renders_pages_with_text_layer() -> None:
    blob = FakeBlob()
    pages = FakePages()
    context = _context()

    metrics = await _decompose_pdf(
        context, _read("text-layer.pdf"), pages, FakeDocuments(), blob, _settings()  # type: ignore[arg-type]
    )

    assert metrics["pages"] == 1.0
    assert metrics["text_layer_pages"] == 1.0
    assert len(pages.replaced[0]) == 1
    assert pages.replaced[0][0]["text_source"] == "text_layer"
    # 1 png + 1 thumbnail
    assert len(blob.put_keys) == 2
    assert blob.put_keys[0].startswith("pages/")


async def test_scanned_pdf_has_no_text_layer() -> None:
    blob = FakeBlob()
    pages = FakePages()

    metrics = await _decompose_pdf(
        _context(), _read("scanned.pdf"), pages, FakeDocuments(), blob, _settings()  # type: ignore[arg-type]
    )

    assert metrics["pages"] == 1.0
    assert metrics["text_layer_pages"] == 0.0
    assert pages.replaced[0][0]["text_source"] is None


async def test_encrypted_pdf_quarantines() -> None:
    blob = FakeBlob()
    pages = FakePages()

    with pytest.raises(QuarantineError) as excinfo:
        await _decompose_pdf(
            _context(), _read("encrypted.pdf"), pages, FakeDocuments(), blob, _settings()  # type: ignore[arg-type]
        )

    assert excinfo.value.code == "pdf_encrypted"
    assert blob.objects == {}


async def test_image_decompose_creates_single_page() -> None:
    blob = FakeBlob()
    pages = FakePages()
    context = _context()

    metrics = await _decompose_image(
        context, _read("multipage.tiff"), "image/tiff", pages, FakeDocuments(), blob, _settings()  # type: ignore[arg-type]
    )

    assert metrics["pages"] == 1.0
    assert len(pages.replaced[0]) == 1
    assert pages.replaced[0][0]["width"] > 0


async def test_page_ceiling_quarantines_large_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io

    import pymupdf

    buffer = io.BytesIO()
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), "content")
    doc.save(buffer)
    doc.close()

    monkeypatch.setenv("IPA_MAX_PAGES", "2")
    settings = Settings(_env_file=None)
    blob = FakeBlob()
    pages = FakePages()

    with pytest.raises(QuarantineError) as excinfo:
        await _decompose_pdf(
            _context(), buffer.getvalue(), pages, FakeDocuments(), blob, settings  # type: ignore[arg-type]
        )

    assert excinfo.value.code == "too_many_pages"
