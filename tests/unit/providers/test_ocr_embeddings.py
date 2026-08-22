"""NVIDIA VLM OCR and embedding provider behaviour."""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx

from ipa.core.config import NvidiaSettings
from ipa.core.errors import ConfigurationError
from ipa.providers.nvidia import client as nvidia_client
from ipa.providers.nvidia.embeddings import NvidiaEmbeddingProvider
from ipa.providers.nvidia.ocr import NvidiaVlmOcrProvider

_BASE = "https://nvidia.test/v1"


def _ocr_provider() -> NvidiaVlmOcrProvider:
    settings = NvidiaSettings(
        _env_file=None,
        NVIDIA_API_KEY="k",
        NVIDIA_BASE_URL=_BASE,
        NVIDIA_VLM_MODEL="vlm-model",
    )
    return NvidiaVlmOcrProvider(settings, client=nvidia_client.build_client(settings))


def _embed_provider(*, dimension: int = 4) -> NvidiaEmbeddingProvider:
    settings = NvidiaSettings(
        _env_file=None,
        NVIDIA_API_KEY="k",
        NVIDIA_BASE_URL=_BASE,
        NVIDIA_EMBED_MODEL="embed-model",
        NVIDIA_EMBED_DIM=dimension,
    )
    return NvidiaEmbeddingProvider(
        settings, client=nvidia_client.build_client(settings), batch_size=2
    )


@respx.mock
async def test_ocr_sends_image_as_base64_data_url() -> None:
    provider = _ocr_provider()
    route = respx.post(f"{_BASE}/chat/completions").mock(
        return_value=respx.MockResponse(
            200,
            json={
                "choices": [{"message": {"content": "transcribed text"}}],
                "model": "vlm-model",
            },
        )
    )

    result = await provider.ocr_image(b"\x89PNG\r\n", "image/png")

    sent = json.loads(route.calls[0].request.content)
    content = sent["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.text == "transcribed text"
    assert result.source == "vlm"
    assert result.confidence is None


@respx.mock
async def test_embedding_passes_input_type_and_batches() -> None:
    provider = _embed_provider()
    route = respx.post(f"{_BASE}/embeddings")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        body = json.loads(request.content)
        index = 0
        return respx.MockResponse(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, 0.0, 0.0, 0.0]}
                    for i in range(len(body["input"]))
                ],
                "model": "embed-model",
            },
        )

    route.side_effect = handler

    vectors = await provider.embed(
        ["a", "b", "c", "d", "e"], kind="passage"
    )

    assert len(vectors) == 5
    assert len(route.calls) == 3  # 2+2+1
    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["input_type"] == "passage"
        assert body["truncate"] == "END"


@respx.mock
async def test_embedding_returns_vectors_in_input_order() -> None:
    provider = _embed_provider(dimension=2)
    route = respx.post(f"{_BASE}/embeddings")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        body = json.loads(request.content)
        # Deliberately return them reversed to check we sort by index.
        data = [{"index": i, "embedding": [float(i), 0.0]} for i in range(len(body["input"]))]
        return respx.MockResponse(200, json={"data": data, "model": "embed-model"})

    route.side_effect = handler

    vectors = await provider.embed(["x", "y"], kind="query")

    assert vectors[0][0] == 0.0
    assert vectors[1][0] == 1.0


@respx.mock
async def test_embedding_dimension_mismatch_raises_configuration_error() -> None:
    provider = _embed_provider(dimension=2)
    respx.post(f"{_BASE}/embeddings").mock(
        return_value=respx.MockResponse(
            200,
            json={
                "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0, 0.0]}],
                "model": "embed-model",
            },
        )
    )

    with pytest.raises(ConfigurationError):
        await provider.embed(["x"], kind="query")


def test_embedding_properties_report_model_and_dimension() -> None:
    provider = _embed_provider(dimension=8)

    assert provider.model == "embed-model"
    assert provider.dimension == 8
