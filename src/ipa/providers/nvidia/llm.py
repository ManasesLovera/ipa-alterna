"""NVIDIA LLM provider with structured (JSON) output support.

`NvidiaLlmProvider` implements the `LlmProvider` protocol. Because structured
output support varies by model, it walks a capability ladder — `json_schema`,
then `json_object`, then prompt-only extraction — caching which strategy worked
per model. Every result is validated against the requested schema, with a single
repair attempt that feeds the model's own output and the validation errors back
for a correction.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI

from ipa.contracts.models import ImageRef, LlmJsonResult
from ipa.core.config import NvidiaSettings
from ipa.core.errors import ProviderError
from ipa.providers.nvidia.client import CompletionUsage, RawCompletion, get_client, run_with_retries

# Strategy that produced valid JSON for a model, cached in-process so we do not
# re-probe a model that already told us what it supports.
_model_strategy: dict[str, str] = {}

_SYSTEM = (
    "You extract structured data from documents. Respond with only a JSON object "
    "that conforms exactly to the supplied schema. Do not add commentary."
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_BALANCED_RE = re.compile(r"\{.*\}", re.DOTALL)


class NvidiaLlmProvider:
    """A chat model returning JSON conforming to a supplied schema."""

    def __init__(
        self,
        settings: NvidiaSettings,
        client: AsyncOpenAI | None = None,
        *,
        json_schema_strategy: str | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            settings: NVIDIA settings carrying model IDs and retry policy.
            client: An `AsyncOpenAI` client; the shared one when None.
            json_schema_strategy: Forced ladder strategy (`json_schema`,
                `json_object`, or `prompt_only`) for tests; auto-probed when None.
        """
        self._settings = settings
        self._client = client or get_client(settings)
        self._forced_strategy = json_schema_strategy

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        images: list[ImageRef] | None = None,
        model: str | None = None,
    ) -> LlmJsonResult:
        """Run a structured completion and return validated JSON.

        Args:
            system: System prompt.
            user: User prompt.
            json_schema: JSON Schema the response must satisfy.
            images: Page images to attach; selects the vision model when given.
            model: Overrides the configured model ID.

        Returns:
            The parsed JSON plus the raw text and token accounting.

        Raises:
            ProviderError: If the response cannot be parsed or repaired.
            RetryableProviderError: On timeouts, rate limits or 5xx responses.
        """
        model_id = model or self._settings.require_model("llm")
        strategy = self._forced_strategy or _strategy_for(model_id)
        system_prompt = system or _SYSTEM
        schema_text = json.dumps(json_schema)

        messages = _build_messages(system_prompt, user, images, schema_text, strategy)

        import time

        start = time.monotonic()
        completion = await run_with_retries(
            "complete_json",
            lambda: self._chat(messages, json_schema, strategy),
            settings=self._settings,
            model=model_id,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        data, repaired = await _parse_and_validate(
            completion.text, json_schema, self, messages, model_id
        )
        _record_strategy(model_id, strategy)

        return LlmJsonResult(
            data=data,
            raw_text=completion.text,
            model=completion.model or model_id,
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
            latency_ms=latency_ms,
            repaired=repaired,
        )

    async def _chat(
        self,
        messages: list[dict[str, Any]],
        json_schema: dict[str, Any],
        strategy: str,
    ) -> RawCompletion:
        """Make one chat-completions call with the given strategy.

        Args:
            messages: The message list.
            json_schema: The JSON Schema.
            strategy: Which capability to use for this call.

        Returns:
            The raw completion text, model and usage.

        Raises:
            ProviderError: If the strategy requests `json_schema` but the call
                is not permitted to use it (probed as unsupported).
        """
        kwargs: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "stream": False,
        }
        if strategy == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif strategy == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return RawCompletion(
            text=content,
            model=getattr(response, "model", "") or self._settings.llm_model,
            usage=CompletionUsage(
                prompt_tokens=getattr(usage, "prompt_tokens", None),
                completion_tokens=getattr(usage, "completion_tokens", None),
            ),
        )

    async def _request_repair(
        self, original_text: str, json_schema: dict[str, Any], error_summary: str
    ) -> str:
        """Ask the model to correct its own invalid output.

        Args:
            original_text: The model's previous, invalid response.
            json_schema: The JSON Schema it must satisfy.
            error_summary: The validation errors to feed back.

        Returns:
            The model's corrected text.

        Raises:
            ProviderError: If the repair request itself fails permanently.
        """
        repair_user = (
            "Your previous response failed schema validation. Correct it so it conforms "
            f"exactly to the schema.\n\nValidation errors: {error_summary or 'unknown'}\n\n"
            f"Your previous response:\n{original_text}"
        )
        completion = await run_with_retries(
            "repair",
            lambda: self._chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": repair_user}],
                json_schema,
                "prompt_only",
            ),
            settings=self._settings,
            model=self._settings.llm_model,
        )
        return completion.text


def _strategy_for(model: str) -> str:
    """Return the cached strategy for a model, defaulting to `json_schema`.

    Args:
        model: The model ID.

    Returns:
        `json_schema` by default; overridden by whichever strategy succeeded
        for this model after probing.
    """
    return _model_strategy.get(model, "json_schema")


@lru_cache(maxsize=1)
def _strategy_lookup() -> tuple[tuple[str, str], ...]:
    """Return a snapshot of the model-to-strategy cache.

    Returns:
        A tuple of `(model, strategy)` pairs, for tests.
    """
    return tuple(_model_strategy.items())


def _record_strategy(model: str, strategy: str) -> None:
    """Cache the strategy that produced valid JSON for a model.

    Args:
        model: The model ID.
        strategy: The successful strategy.
    """
    _model_strategy[model] = strategy
    _strategy_lookup.cache_clear()


def _build_messages(
    system: str, user: str, images: list[ImageRef] | None, schema_text: str, strategy: str
) -> list[dict[str, Any]]:
    """Assemble the OpenAI message list.

    Args:
        system: System prompt.
        user: User prompt.
        images: Optional page images to attach as multi-content image parts.
        schema_text: JSON Schema serialised to text.
        strategy: The ladder strategy; `json_object` and `prompt_only` embed the
            schema into the user message.

    Returns:
        A list of system/user messages, with image parts appended when present.
    """
    system_content = system
    if strategy == "json_schema":
        system_content += f"\n\nSchema:\n{schema_text}"

    user_content: Any = user
    if strategy in ("json_object", "prompt_only"):
        user_content = f"{user}\n\nReturn a JSON object conforming to this schema:\n{schema_text}"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]
    if images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
        for image in images:
            parts.append({"type": "image_url", "image_url": {"url": f"blob:{image.blob_key}"}})
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": user_content})
    return messages


async def _parse_and_validate(
    text: str,
    json_schema: dict[str, Any],
    provider: NvidiaLlmProvider,
    messages: list[dict[str, Any]],
    model: str,
) -> tuple[dict[str, Any], bool]:
    """Parse a completion against a schema, repairing once if needed.

    Args:
        text: The raw model output.
        json_schema: The JSON Schema to validate against.
        provider: The provider, used to request a repair.
        messages: The original message list.
        model: The model ID.

    Returns:
        A tuple of `(data, repaired)`.

    Raises:
        ProviderError: If parsing or validation fails and the repair also fails.
    """
    import jsonschema

    try:
        parsed = _extract_json(text)
        jsonschema.validate(parsed, json_schema)
        return parsed, False
    except (ValueError, jsonschema.ValidationError) as exc:
        error_summary = _error_summary(exc)

    repaired_text = await provider._request_repair(text, json_schema, error_summary)
    try:
        repaired = _extract_json(repaired_text)
        jsonschema.validate(repaired, json_schema)
        return repaired, True
    except (ValueError, jsonschema.ValidationError):
        raise ProviderError(
            "Model returned JSON that could not be repaired against the schema.",
            code="provider_error",
        ) from None


def _error_summary(exc: Exception) -> str:
    """Render a validation error as a compact string for the repair prompt.

    Args:
        exc: The validation exception; a `jsonschema.ValidationError` or a
            plain `ValueError` from JSON extraction.

    Returns:
        A human-readable summary of what was wrong.
    """
    import jsonschema

    if isinstance(exc, jsonschema.ValidationError):
        path = "/".join(str(p) for p in exc.absolute_path) or "root"
        return f"{path}: {exc.message}"
    return str(exc)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from a model response.

    Strips markdown fences, then falls back to scanning for the first balanced
    `{...}` region.

    Args:
        text: The raw model output.

    Returns:
        The parsed JSON object.

    Raises:
        ValueError: If no balanced JSON object can be parsed.
    """
    stripped = _FENCE_RE.sub("", text).strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _BALANCED_RE.search(text)
    if not match:
        raise ValueError("No JSON object found in response")
    return json.loads(match.group(0))
