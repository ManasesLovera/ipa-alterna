"""Embed step: context headers, summary chunks and chunking."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ipa.contracts.models import PageText
from ipa.core.enums import PipelineStep
from ipa.pipeline.steps.embed import _context_header
from ipa.processing.chunking import chunk_pages


def _document(title: str = "invoice.pdf") -> object:
    return type("D", (), {"title": title, "original_filename": "invoice.pdf"})()


def test_context_header_uses_title_and_tag() -> None:
    header = _context_header(_document(), "Invoice", None)

    assert "invoice.pdf" in header
    assert "Invoice" in header


def test_context_header_includes_page() -> None:
    header = _context_header(_document(), None, 3)

    assert "page 3" in header


def test_chunking_respects_page_ranges_and_overlap() -> None:
    pages = [
        PageText(
            page=p,
            text="\n".join(["Sentence one here. Sentence two here."] * 30),
            source="text_layer",
            char_count=1000,
        )
        for p in range(1, 4)
    ]

    chunks = chunk_pages(pages, target_tokens=60, overlap_tokens=10)

    assert len(chunks) > 1
    for chunk in chunks:
        assert 1 <= chunk.page_from <= chunk.page_to <= 3
        assert chunk.tokens <= 60 * 2


def test_summary_chunk_returns_none_when_no_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    from ipa.contracts.models import StepContext
    from ipa.pipeline.steps.embed import _summary_chunk
    from ipa.storage import factory as storage_factory

    class FakeContent:
        async def get_extraction(self, document_id: object, version: object = None) -> None:
            return None

    monkeypatch.setattr(storage_factory, "_content_store", FakeContent())
    context = StepContext(document_id=uuid4(), step=PipelineStep.EMBED, attempt=1)

    import asyncio

    result = asyncio.run(_summary_chunk(context, _document()))

    assert result is None
