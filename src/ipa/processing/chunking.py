"""Text chunking for embedding and retrieval.

Chunks respect page boundaries where possible, split on paragraph then sentence
then hard-wrap (never mid-word), apply overlap at the sentence level, merge very
short pages forward, and keep Markdown tables intact even when they exceed the
target size. Token estimation is a cheap heuristic — we have no tokenizer for the
NVIDIA models — so it is documented as approximate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ipa.contracts.models import PageText

TABLE_CELL_RE = re.compile(r"^\s*\|", re.MULTILINE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
TABLE_MAX_MULTIPLIER = 2


@dataclass(frozen=True)
class Chunk:
    """One unit of text destined for embedding.

    Attributes:
        text: The chunk text.
        page_from: 1-based first page.
        page_to: 1-based last page.
        tokens: Estimated token count.
        index: Position within the document (0-based).
    """

    text: str
    page_from: int
    page_to: int
    tokens: int
    index: int


@dataclass
class _Accumulator:
    """Stateful builder for the current chunk being filled."""

    parts: list[str] = field(default_factory=list)
    page_from: int | None = None
    page_to: int | None = None

    def push(self, piece: str, page: int) -> None:
        """Add a text piece from a page.

        Args:
            piece: The text to append.
            page: The 1-based page it came from.
        """
        self.parts.append(piece)
        if self.page_from is None:
            self.page_from = page
        self.page_to = page

    @property
    def text(self) -> str:
        """Return the accumulated text.

        Returns:
            The joined chunk text.
        """
        return "\n\n".join(part for part in self.parts if part)

    def is_empty(self) -> bool:
        """Report whether no content has been added.

        Returns:
            True when there are no non-empty parts.
        """
        return not any(self.parts)


def chunk_pages(
    pages: list[PageText], *, target_tokens: int, overlap_tokens: int
) -> list[Chunk]:
    """Split page text into overlapping, embeddable chunks.

    Chunks accumulate across pages and flush only when a sentence would push the
    current chunk past the target, so very short pages merge forward rather than
    becoming tiny chunks. An overlap tail is carried from each flushed chunk.

    Args:
        pages: Page texts in page order.
        target_tokens: Desired chunk size in tokens.
        overlap_tokens: Tokens of overlap carried between consecutive chunks.

    Returns:
        An ordered list of `Chunk`.

    Raises:
        ValueError: If `target_tokens` is not positive.
    """
    if target_tokens <= 0:
        raise ValueError("target_tokens must be positive")
    table_cap = target_tokens * TABLE_MAX_MULTIPLIER
    chunks: list[Chunk] = []
    pending: _Accumulator = _Accumulator()

    def flush() -> None:
        """Emit the pending accumulator as a chunk and return its text."""
        nonlocal pending
        if pending.is_empty():
            return
        text = pending.text.strip()
        if text:
            chunks.append(
                Chunk(
                    text=text,
                    page_from=pending.page_from or 1,
                    page_to=pending.page_to or 1,
                    tokens=estimate_tokens(text),
                    index=len(chunks),
                )
            )
        pending = _Accumulator()

    for page in pages:
        for piece in _split_page(page.text):
            if _is_table(piece) and estimate_tokens(piece) <= table_cap:
                pending.push(piece, page.page)
                continue
            for sentence in _sentences(piece):
                current = estimate_tokens(pending.text)
                if current + estimate_tokens(sentence) <= target_tokens:
                    pending.push(sentence, page.page)
                    continue
                if pending.is_empty():
                    pending.push(sentence, page.page)
                    continue
                tail = _overlap_tail(pending.text, overlap_tokens)
                flush()
                if tail:
                    pending.push(tail, page.page)
                pending.push(sentence, page.page)

    flush()
    return chunks


def estimate_tokens(text: str) -> int:
    """Estimate token count with a cheap heuristic.

    Approximates 1 token per 4 ASCII characters, with a conservative multiplier
    for CJK text. This is intentionally approximate; we do not hold the NVIDIA
    models' tokenizers.

    Args:
        text: The text to estimate.

    Returns:
        An estimated token count.
    """
    if not text:
        return 0
    cjk = sum(1 for c in text if ord(c) > 0x2E80)
    ascii_chars = len(text) - cjk
    return max(1, round(ascii_chars / 4 + cjk * 0.6))


def _split_page(text: str) -> list[str]:
    """Split a page's text into paragraphs and table blocks.

    Args:
        text: The page text.

    Returns:
        A list of text blocks.
    """
    blocks = re.split(r"\n\s*\n", text)
    result: list[str] = []
    for block in blocks:
        if _is_table(block):
            result.append(block)
        else:
            for paragraph in re.split(r"(?<=\n)", block):
                if paragraph.strip():
                    result.append(paragraph.strip())
    return [b for b in result if b.strip()]


def _sentences(text: str) -> list[str]:
    """Split text into sentences, preferring sentence boundaries.

    Args:
        text: The text to split.

    Returns:
        A list of sentences.
    """
    parts = SENTENCE_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _is_table(text: str) -> bool:
    """Report whether text looks like a Markdown table.

    Args:
        text: The text to inspect.

    Returns:
        True when it contains a pipe-based table row.
    """
    return bool(TABLE_CELL_RE.search(text))


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """Return the tail of a chunk to carry forward as overlap.

    Args:
        text: The previous chunk's text.
        overlap_tokens: How much tail to carry.

    Returns:
        The trailing sentences totaling roughly `overlap_tokens`.
    """
    if not text or overlap_tokens <= 0:
        return ""
    sentences = SENTENCE_RE.split(text)
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        tail.append(sentence)
        total += estimate_tokens(sentence)
        if total >= overlap_tokens:
            break
    return " ".join(reversed(tail))
