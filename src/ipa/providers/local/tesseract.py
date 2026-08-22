"""Local Tesseract OCR provider.

`TesseractOcrProvider` implements the `OcrProvider` protocol using the
`pytesseract` wrapper around the `tesseract-ocr` binary. It runs the blocking
OCR call in a thread executor so it never blocks the event loop, and uses
`image_to_data` to compute a real mean word confidence.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ipa.contracts.models import OcrResult
from ipa.core.errors import ProviderError

# pytesseract types are stubbed only in mypy overrides; the import is guarded so
# an environment without the binary still imports the module.
try:  # pragma: no cover - exercised only when tesseract is installed
    import pytesseract
    from PIL import Image

    _TESSERACT_AVAILABLE = True
except Exception:  # pragma: no cover - import-time environment guard
    _TESSERACT_AVAILABLE = False

_WORD_RE = re.compile(r"\w+", re.UNICODE)


class TesseractOcrProvider:
    """A local OCR fallback using the Tesseract engine."""

    def __init__(
        self, *, languages: str = "eng", binary: str | None = None
    ) -> None:
        """Initialise the provider.

        Args:
            languages: `+`-separated Tesseract language codes, e.g. `eng+spa`.
            binary: Override the tesseract binary path; uses pytesseract's
                default when None.
        """
        self._languages = languages
        self._binary = binary

    @property
    def available(self) -> bool:
        """Report whether the Tesseract binary is importable/usable.

        Returns:
            True when pytesseract loaded and a tesseract binary is reachable.
        """
        if not _TESSERACT_AVAILABLE:
            return False
        if self._binary:
            return True
        try:
            return bool(pytesseract.get_tesseract_version())
        except Exception:
            return False

    async def ocr_image(self, image: bytes, mime: str) -> OcrResult:
        """Transcribe an image with Tesseract.

        Args:
            image: Raw image bytes (PNG preferred).
            mime: Image MIME type.

        Returns:
            The transcription, `source="tesseract"`, with a mean word confidence
            in `[0, 1]`.

        Raises:
            ProviderError: If the binary is unavailable or OCR fails.
        """
        if not self.available:
            raise ProviderError(
                "Tesseract is not installed; cannot run the local OCR fallback.",
                code="provider_unavailable",
            )
        if not _TESSERACT_AVAILABLE:  # pragma: no cover - mypy narrow
            raise ProviderError("Tesseract library unavailable.")

        image_obj = Image.open(__import__("io").BytesIO(image)).convert("RGB")
        lang = self._languages

        def run() -> tuple[str, float]:
            data = pytesseract.image_to_data(
                image_obj, lang=lang, output_type=pytesseract.Output.DICT
            )
            text = _assemble_text(data)
            confidence = _mean_confidence(data)
            return text, confidence

        text, confidence = await asyncio.to_thread(run)
        return OcrResult(text=text, confidence=confidence, source="tesseract")


def _assemble_text(data: dict[str, Any]) -> str:
    """Rebuild a text block from `image_to_data` output.

    Words are joined per line; lines are joined per block, preserving vertical
    layout as paragraphs.

    Args:
        data: The `image_to_data` dict with `text`, `block_num` and `line_num`.

    Returns:
        The assembled transcription.
    """
    text = data.get("text") or []
    block_nums = data.get("block_num") or []
    line_nums = data.get("line_num") or []

    lines: list[str] = []
    current_block: int | None = None
    current_line: int | None = None
    parts: list[str] = []
    for word, block, line in zip(text, block_nums, line_nums, strict=True):
        word = (word or "").strip()
        if block != current_block:
            if parts:
                lines.append(" ".join(parts))
            parts = []
            current_block = block
            current_line = line
            if word:
                parts.append(word)
            continue
        if line != current_line:
            lines.append(" ".join(parts))
            parts = []
            current_line = line
        if word:
            parts.append(word)
    if parts:
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _mean_confidence(data: dict[str, Any]) -> float:
    """Compute the mean word confidence scaled to `[0, 1]`.

    `image_to_data` returns confidence per word on a 0-100 scale; words Tesseract
    could not recognise get `-1`.

    Args:
        data: The `image_to_data` dict.

    Returns:
        The mean confidence of recognised words as a 0-1 float.
    """
    confidences = [float(c) for c in (data.get("conf") or []) if float(c) >= 0]
    if not confidences:
        return 0.0
    return round(sum(confidences) / len(confidences) / 100.0, 4)
