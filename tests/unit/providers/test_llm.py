"""NVIDIA LLM JSON capability ladder, repair and fence-stripping behaviour."""

from __future__ import annotations

import json
from typing import Any

import pytest
import respx

from ipa.core.config import NvidiaSettings
from ipa.core.errors import ProviderError
from ipa.providers.nvidia import client as nvidia_client
from ipa.providers.nvidia import llm
from ipa.providers.nvidia.llm import NvidiaLlmProvider

_BASE = "https://nvidia.test/v1"


def _settings() -> NvidiaSettings:
    return NvidiaSettings(
        _env_file=None, NVIDIA_API_KEY="k", NVIDIA_BASE_URL=_BASE, NVIDIA_LLM_MODEL="llm"
    )


def _provider(*, strategy: str | None = None) -> NvidiaLlmProvider:
    settings = _settings()
    return NvidiaLlmProvider(
        settings,
        client=nvidia_client.build_client(settings),
        json_schema_strategy=strategy,
    )

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": ["invoice_number"],
            "properties": {
                "invoice_number": {
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": ["string", "null"]}},
                }
            },
        }
    },
}

_PAYLOAD = json.dumps({"fields": {"invoice_number": {"value": "ACME-001"}}})


def _completion(content: str) -> Any:
    payload = {"choices": [{"message": {"content": content}}], "model": "llm"}
    return respx.MockResponse(200, json=payload)


@respx.mock
async def test_json_schema_strategy_sends_response_format() -> None:
    provider = _provider(strategy="json_schema")
    route = respx.post(f"{_BASE}/chat/completions").mock(return_value=_completion(_PAYLOAD))

    result = await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)

    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert result.data["fields"]["invoice_number"]["value"] == "ACME-001"
    assert result.repaired is False


@respx.mock
async def test_json_object_strategy_sends_response_format_and_embeds_schema() -> None:
    provider = _provider(strategy="json_object")
    route = respx.post(f"{_BASE}/chat/completions").mock(return_value=_completion(_PAYLOAD))

    await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)

    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["type"] == "json_object"
    assert "conforming to this schema" in sent["messages"][1]["content"]


@respx.mock
async def test_prompt_only_strategy_embeds_schema_and_parses() -> None:
    provider = _provider(strategy="prompt_only")
    route = respx.post(f"{_BASE}/chat/completions").mock(
        return_value=_completion(f"```json\n{_PAYLOAD}\n```")
    )

    result = await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)

    sent = json.loads(route.calls[0].request.content)
    assert "response_format" not in sent
    assert result.data["fields"]["invoice_number"]["value"] == "ACME-001"


@respx.mock
async def test_invalid_json_triggers_one_repair() -> None:
    provider = _provider(strategy="prompt_only")
    route = respx.post(f"{_BASE}/chat/completions")

    def handler(request) -> Any:  # type: ignore[no-untyped-def]
        return respx.MockResponse(
            200,
            json={"choices": [{"message": {"content": "not json"}}], "model": "llm"},
        )

    route.side_effect = handler
    # First call invalid, repair call returns valid JSON.
    real_handler = route.side_effect

    def patched(request) -> Any:  # type: ignore[no-untyped-def]
        if len(route.calls) == 0:
            return respx.MockResponse(
                200,
                json={"choices": [{"message": {"content": "not json"}}], "model": "llm"},
            )
        return respx.MockResponse(
            200, json={"choices": [{"message": {"content": _PAYLOAD}}], "model": "llm"}
        )

    route.side_effect = patched

    result = await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)

    assert result.repaired is True
    assert result.data["fields"]["invoice_number"]["value"] == "ACME-001"
    assert len(route.calls) == 2


@respx.mock
async def test_repair_fails_raises_provider_error_with_raw_text() -> None:
    provider = _provider(strategy="prompt_only")
    route = respx.post(f"{_BASE}/chat/completions")

    def patched(request) -> Any:  # type: ignore[no-untyped-def]
        return respx.MockResponse(
            200, json={"choices": [{"message": {"content": "still not json"}}], "model": "llm"}
        )

    route.side_effect = patched

    with pytest.raises(ProviderError):
        await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)


@respx.mock
async def test_fence_stripping_parses_fenced_json() -> None:
    provider = _provider(strategy="prompt_only")
    respx.post(f"{_BASE}/chat/completions").mock(
        return_value=_completion(f"Here you go:\n```json\n{_PAYLOAD}\n```")
    )

    result = await provider.complete_json(system="s", user="u", json_schema=_SCHEMA)

    assert result.data["fields"]["invoice_number"]["value"] == "ACME-001"


def test_extract_json_handles_fences() -> None:
    assert llm._extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}
    assert llm._extract_json("prefix {\"a\": 1} suffix") == {"a": 1}


def test_strategy_defaults_to_json_schema() -> None:
    assert llm._strategy_for("unknown-model") == "json_schema"
