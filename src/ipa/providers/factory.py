"""Cached provider singletons and the OCR fallback chain.

Both the API and Celery tasks resolve providers through these functions so a
process holds at most one client pool per backend. The OCR resolution order is
`[vlm, tesseract]`, honouring `IPA_OCR_FALLBACK_ENABLED`.
"""

from __future__ import annotations

from ipa.contracts.protocols import EmbeddingProvider, LlmProvider, OcrProvider
from ipa.core.config import get_settings
from ipa.providers.local.tesseract import TesseractOcrProvider
from ipa.providers.nvidia.embeddings import NvidiaEmbeddingProvider
from ipa.providers.nvidia.llm import NvidiaLlmProvider
from ipa.providers.nvidia.ocr import NvidiaVlmOcrProvider

_llm_provider: NvidiaLlmProvider | None = None
_ocr_vlm_provider: NvidiaVlmOcrProvider | None = None
_ocr_tesseract_provider: TesseractOcrProvider | None = None
_embedding_provider: NvidiaEmbeddingProvider | None = None


def get_llm_provider() -> LlmProvider:
    """Return the process-wide LLM provider singleton.

    Returns:
        A provider satisfying the `LlmProvider` protocol.
    """
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = NvidiaLlmProvider(get_settings().nvidia)
    return _llm_provider


def get_ocr_providers() -> list[OcrProvider]:
    """Return the ordered OCR fallback chain.

    When `IPA_OCR_FALLBACK_ENABLED` is set the chain is `[vlm, tesseract]`;
    otherwise only the VLM provider.

    Returns:
        A list of providers satisfying the `OcrProvider` protocol, best first.
    """
    providers: list[OcrProvider] = [get_vlm_ocr_provider()]
    if get_settings().ocr.fallback_enabled:
        providers.append(get_tesseract_provider())
    return providers


def get_vlm_ocr_provider() -> OcrProvider:
    """Return the process-wide VLM OCR provider singleton.

    Returns:
        A provider satisfying the `OcrProvider` protocol.
    """
    global _ocr_vlm_provider
    if _ocr_vlm_provider is None:
        _ocr_vlm_provider = NvidiaVlmOcrProvider(get_settings().nvidia)
    return _ocr_vlm_provider


def get_tesseract_provider() -> OcrProvider:
    """Return the process-wide Tesseract OCR provider singleton.

    Returns:
        A provider satisfying the `OcrProvider` protocol.
    """
    global _ocr_tesseract_provider
    if _ocr_tesseract_provider is None:
        _ocr_tesseract_provider = TesseractOcrProvider(
            languages=get_settings().ocr.tesseract_langs
        )
    return _ocr_tesseract_provider


def get_embedding_provider() -> EmbeddingProvider:
    """Return the process-wide embedding provider singleton.

    Returns:
        A provider satisfying the `EmbeddingProvider` protocol.
    """
    global _embedding_provider
    if _embedding_provider is None:
        _embedding_provider = NvidiaEmbeddingProvider(get_settings().nvidia)
    return _embedding_provider
