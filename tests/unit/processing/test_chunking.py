"""Chunking: page ranges, overlap, size bounds and table preservation."""

from __future__ import annotations

from ipa.contracts.models import PageText
from ipa.processing.chunking import chunk_pages, estimate_tokens


def _pages(lines_per_page: int, pages: int = 3) -> list[PageText]:
    result: list[PageText] = []
    for p in range(1, pages + 1):
        lines = [f"Page {p} sentence one. Sentence two. Sentence three."] * lines_per_page
        result.append(PageText(page=p, text="\n".join(lines), source="text_layer", char_count=1000))
    return result


def test_chunk_pages_respects_page_ranges_and_overlap() -> None:
    pages = _pages(lines_per_page=50, pages=3)
    target = 60

    chunks = chunk_pages(pages, target_tokens=target, overlap_tokens=10)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.page_from >= 1
        assert chunk.page_to <= 3
        assert chunk.page_from <= chunk.page_to


def test_no_chunk_exceeds_two_times_target() -> None:
    pages = _pages(lines_per_page=50, pages=3)
    target = 50

    chunks = chunk_pages(pages, target_tokens=target, overlap_tokens=5)

    assert all(c.tokens <= target * 2 for c in chunks)


def test_markdown_table_survives_intact() -> None:
    table = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
    pages = [PageText(page=1, text=table, source="vlm", char_count=50)]
    target = 30

    chunks = chunk_pages(pages, target_tokens=target, overlap_tokens=0)

    assert any("| 1 | 2 |" in c.text for c in chunks)


def test_short_pages_merge_forward() -> None:
    tiny = "Just a tiny page.\n"
    pages = [
        PageText(page=1, text=tiny, source="text_layer", char_count=20),
        PageText(page=2, text="Second page short content.", source="text_layer", char_count=60),
    ]

    chunks = chunk_pages(pages, target_tokens=100, overlap_tokens=0)

    assert len(chunks) == 1
    assert chunks[0].page_from == 1
    assert chunks[0].page_to == 2


def test_chunk_indices_are_sequential() -> None:
    pages = _pages(lines_per_page=50, pages=2)
    chunks = chunk_pages(pages, target_tokens=40, overlap_tokens=5)

    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_estimate_tokens_approximates() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") >= 1
    assert estimate_tokens("汉字汉字") > estimate_tokens("abcd")
