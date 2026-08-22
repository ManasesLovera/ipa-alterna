"""Deterministic fake providers: schemas, embedding determinism, fallback chain."""

from __future__ import annotations

from typing import Any

import pytest

from ipa.contracts.models import ImageRef, OcrResult
from ipa.core.config import Settings
from ipa.core.errors import ProviderError
from ipa.providers import factory
from ipa.providers.fake import (
    FailingLlmProvider,
    FakeEmbeddingProvider,
    FakeLlmProvider,
    FakeOcrProvider,
)

_SAMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "object",
            "properties": {
                "invoice_number": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                        "page": {"type": ["integer", "null"]},
                        "evidence": {"type": ["string", "null"]},
                    },
                },
                "total": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["number", "null"]},
                        "confidence": {"type": "number"},
                    },
                },
            },
        }
    },
}


async def test_fake_llm_fills_every_declared_field() -> None:
    provider = FakeLlmProvider()

    result = await provider.complete_json(
        system="sys", user="usr", json_schema=_SAMPLE_SCHEMA
    )

    assert result.repaired is False
    assert result.data["fields"]["invoice_number"]["value"] == "placeholder"
    assert result.data["fields"]["invoice_number"]["confidence"] == 0.9
    assert result.data["fields"]["total"]["value"] == 0.0
    assert result.model == "fake-llm"


async def test_fake_llm_records_call_and_overrides_model() -> None:
    provider = FakeLlmProvider(model="base")

    result = await provider.complete_json(
        system="sys", user="usr", json_schema=_SAMPLE_SCHEMA, model="override"
    )

    assert result.model == "override"
    assert provider.calls == [{"system": "sys", "user": "usr", "images": None}]


async def test_fake_llm_records_images() -> None:
    provider = FakeLlmProvider()
    image = ImageRef(page=1, blob_key="pages/x.png", mime="image/png")

    await provider.complete_json(
        system="s", user="u", json_schema=_SAMPLE_SCHEMA, images=[image]
    )

    assert provider.calls[0]["images"] == [image]


async def test_failing_llm_raises_provider_error() -> None:
    provider = FailingLlmProvider()

    with pytest.raises(ProviderError):
        await provider.complete_json(system="s", user="u", json_schema=_SAMPLE_SCHEMA)


async def test_fake_ocr_returns_fixed_text() -> None:
    provider = FakeOcrProvider(text="hello world", confidence=0.85)

    result = await provider.ocr_image(b"png", "image/png")

    assert isinstance(result, OcrResult)
    assert result.text == "hello world"
    assert result.confidence == 0.85
    assert result.source == "vlm"
    assert provider.calls == [(b"png", "image/png")]


async def test_fake_embedding_is_deterministic_unit_vector() -> None:
    provider = FakeEmbeddingProvider(dimension=8)

    a = (await provider.embed(["same text"], kind="passage"))[0]
    b = (await provider.embed(["same text"], kind="query"))[0]
    c = (await provider.embed(["different"], kind="passage"))[0]

    assert a == b
    assert a != c
    assert abs(sum(v * v for v in a) - 1.0) < 1e-6
    assert len(a) == 8


async def test_fake_embedding_records_kind() -> None:
    provider = FakeEmbeddingProvider()

    await provider.embed(["one", "two"], kind="query")

    assert provider.calls == [(["one", "two"], "query")]


def test_fake_embedding_properties_match_protocol() -> None:
    provider = FakeEmbeddingProvider(model="m", dimension=16)

    assert provider.model == "m"
    assert provider.dimension == 16


def test_get_ocr_providers_includes_fallback_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPA_OCR_FALLBACK_ENABLED", "true")

    providers = factory.get_ocr_providers()

    assert len(providers) == 2
    assert providers[0].__class__.__name__ == "NvidiaVlmOcrProvider"
    assert providers[1].__class__.__name__ == "TesseractOcrProvider"


def test_get_ocr_providers_excludes_fallback_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPA_OCR_FALLBACK_ENABLED", "false")

    providers = factory.get_ocr_providers()

    assert len(providers) == 1
    assert providers[0].__class__.__name__ == "NvidiaVlmOcrProvider"


def test_factory_singletons_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "key")
    monkeypatch.setenv("NVIDIA_LLM_MODEL", "llm")
    monkeypatch.setenv("NVIDIA_VLM_MODEL", "vlm")
    monkeypatch.setenv("NVIDIA_EMBED_MODEL", "embed")

    Settings(_env_file=None)
    factory._llm_provider = None
    factory._embedding_provider = None
    factory._ocr_vlm_provider = None

    assert factory.get_llm_provider() is factory.get_llm_provider()
    assert factory.get_embedding_provider() is factory.get_embedding_provider()
    assert factory.get_vlm_ocr_provider() is factory.get_vlm_ocr_provider()
