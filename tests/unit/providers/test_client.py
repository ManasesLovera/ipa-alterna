"""NVIDIA client retry wrapper: backoff, status classification, retry-once-200."""

from __future__ import annotations

from typing import Any

import openai
import pytest
import respx

from ipa.core.config import NvidiaSettings
from ipa.core.errors import ProviderError, RetryableProviderError
from ipa.providers.nvidia import client

_BASE = "https://nvidia.test/v1"


def _settings(**overrides: Any) -> NvidiaSettings:
    values: dict[str, Any] = {
        "NVIDIA_API_KEY": "k",
        "NVIDIA_BASE_URL": _BASE,
        "NVIDIA_LLM_MODEL": "llm",
        "NVIDIA_MAX_RETRIES": 2,
    }
    values.update(overrides)
    return NvidiaSettings(_env_file=None, **values)


async def _counting_call():
    pass


def test_retryable_status_covers_429_and_5xx() -> None:
    assert client.is_retryable_status(429)
    assert client.is_retryable_status(500)
    assert client.is_retryable_status(503)
    assert not client.is_retryable_status(400)
    assert not client.is_retryable_status(200)


@respx.mock
async def test_retry_429_twice_then_200() -> None:
    settings = _settings(NVIDIA_MAX_RETRIES=2)
    calls: list[int] = []
    router = respx.post(f"{_BASE}/chat/completions")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) < 3:
            return respx.MockResponse(429, headers={"retry-after": "0"})
        return respx.MockResponse(
            200, json={"choices": [{"message": {"content": "{}"}}], "model": "llm"}
        )

    router.side_effect = handler

    async def call() -> str:
        response = await client.build_client(settings).chat.completions.create(
            model="llm", messages=[{"role": "user", "content": "hi"}]
        )
        return response.choices[0].message.content or ""

    result = await client.run_with_retries(
        "test", call, settings=settings, model="llm"
    )

    assert result == "{}"
    assert len(calls) == 3


@respx.mock
async def test_retry_429_then_exhaust_raises_retryable() -> None:
    settings = _settings(NVIDIA_MAX_RETRIES=1)
    calls: list[int] = []
    router = respx.post(f"{_BASE}/chat/completions")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        calls.append(1)
        return respx.MockResponse(429)

    router.side_effect = handler

    async def call() -> str:
        await client.build_client(settings).chat.completions.create(
            model="llm", messages=[{"role": "user", "content": "hi"}]
        )
        return "x"

    with pytest.raises(RetryableProviderError):
        await client.run_with_retries("test", call, settings=settings, model="llm")
    assert len(calls) == 2  # 1 initial + 1 retry


@respx.mock
async def test_permanent_4xx_raises_provider_error_without_retry() -> None:
    settings = _settings(NVIDIA_MAX_RETRIES=3)
    calls: list[int] = []
    router = respx.post(f"{_BASE}/chat/completions")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        calls.append(1)
        return respx.MockResponse(400, json={"error": {"message": "bad"}})

    router.side_effect = handler

    async def call() -> str:
        await client.build_client(settings).chat.completions.create(
            model="llm", messages=[{"role": "user", "content": "hi"}]
        )
        return "x"

    with pytest.raises(ProviderError):
        await client.run_with_retries("test", call, settings=settings, model="llm")
    assert len(calls) == 1


def test_classify_connects_timeout_as_retryable() -> None:
    assert client._classify(openai.APITimeoutError("t"))[0] is True
    assert client._classify(openai.APIConnectionError(request=object()))[0] is True
    assert client._classify(ValueError("boom"))[0] is False
