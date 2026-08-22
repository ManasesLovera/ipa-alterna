"""Deterministic fake providers for unit and integration tests.

These fakes are a shipping artifact, not test scaffolding: every other task's
tests depend on them existing. They implement the same protocols as the NVIDIA
providers but never touch the network and return values derived purely from
their inputs, so tests are deterministic and run offline.

- `FakeLlmProvider` returns a schema-shaped object filled with placeholder
  values derived from the requested schema.
- `FakeOcrProvider` returns fixed text with a fixed confidence.
- `FakeEmbeddingProvider` returns a hashed pseudo-random unit vector of the
  configured dimension.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Literal
from uuid import UUID

from ipa.contracts.models import (
    ImageRef,
    LlmJsonResult,
    OcrResult,
    PageText,
)
from ipa.contracts.protocols import EmbeddingProvider, LlmProvider, OcrProvider


class FakeLlmProvider(LlmProvider):
    """A chat model returning schema-shaped placeholder JSON."""

    def __init__(self, *, model: str = "fake-llm", fallback_value: str = "placeholder") -> None:
        """Initialise the fake with a model ID and placeholder text.

        Args:
            model: The model ID reported on results.
            fallback_value: The string placed in every `value` field.
        """
        self.model = model
        self.fallback_value = fallback_value
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        images: list[ImageRef] | None = None,
        model: str | None = None,
    ) -> LlmJsonResult:
        """Return JSON matching the requested schema, filling each `value`.

        Every field whose type is declared nullable (all of them in the compiled
        schema) receives `self.fallback_value` so downstream parsing exercises
        the real path. Non-null primitive fields receive a plausible value.

        Args:
            system: System prompt (recorded, unused).
            user: User prompt (recorded, unused).
            json_schema: The JSON Schema the response must satisfy.
            images: Page images (recorded, unused).
            model: Overrides the reported model ID.

        Returns:
            A well-formed `LlmJsonResult` with the schema-shaped payload.

        Raises:
            Never; this fake is always successful.
        """
        self.calls.append({"system": system, "user": user, "images": images})
        data = _fill_schema(json_schema, self.fallback_value)
        return LlmJsonResult(
            data=data,
            raw_text="fake-llm-output",
            model=model or self.model,
            prompt_tokens=12,
            completion_tokens=34,
            latency_ms=5,
            repaired=False,
        )


class FailingLlmProvider(LlmProvider):
    """An LLM provider whose every call raises a configured exception.

    Lets pipeline tests exercise the repair, retry and failure paths without a
    real model.

    Attributes:
        exception: The exception raised on every call, defaulting to
            `ipa.core.errors.ProviderError`.
    """

    def __init__(self, exception: Exception | None = None) -> None:
        """Initialise the failing fake.

        Args:
            exception: Exception to raise on every call; a fresh
                `ProviderError` is created when None.
        """
        from ipa.core.errors import ProviderError

        self.exception = exception or ProviderError("fake provider failure")
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        images: list[ImageRef] | None = None,
        model: str | None = None,
    ) -> LlmJsonResult:
        """Raise the configured exception.

        Args:
            system: Ignored.
            user: Ignored.
            json_schema: Ignored.
            images: Ignored.
            model: Ignored.

        Returns:
            Never returns.

        Raises:
            Exception: The configured exception, every call.
        """
        self.calls.append({"system": system, "user": user, "images": images})
        raise self.exception


class FakeOcrProvider(OcrProvider):
    """An OCR provider returning fixed text and confidence."""

    def __init__(
        self,
        *,
        text: str = "fake ocr text",
        confidence: float = 0.9,
        source: Literal["vlm", "tesseract"] = "vlm",
    ) -> None:
        """Initialise the fake.

        Args:
            text: The text returned for every image.
            confidence: The confidence returned for every image.
            source: The OCR source reported on the result.
        """
        self.text = text
        self.confidence = confidence
        self.source = source
        self.calls: list[tuple[bytes, str]] = []

    async def ocr_image(self, image: bytes, mime: str) -> OcrResult:
        """Record the call and return the fixed result.

        Args:
            image: Raw image bytes (recorded, unused).
            mime: Image MIME type (recorded, unused).

        Returns:
            An `OcrResult` carrying the fixed text and confidence.
        """
        self.calls.append((image, mime))
        return OcrResult(text=self.text, confidence=self.confidence, source=self.source)


class FakeEmbeddingProvider(EmbeddingProvider):
    """An embedding provider returning hashed pseudo-random unit vectors."""

    def __init__(self, *, model: str = "fake-embed", dimension: int = 8) -> None:
        """Initialise the fake.

        Args:
            model: The model ID reported on results.
            dimension: The vector dimensionality returned.
        """
        self._model = model
        self._dimension = dimension
        self.calls: list[tuple[list[str], str]] = []

    @property
    def model(self) -> str:
        """Return the fake's model ID.

        Returns:
            The model ID passed at construction.
        """
        return self._model

    @property
    def dimension(self) -> int:
        """Return the fake's vector dimensionality.

        Returns:
            The dimension passed at construction.
        """
        return self._dimension

    async def embed(
        self, texts: list[str], *, kind: Literal["query", "passage"]
    ) -> list[list[float]]:
        """Return a deterministic unit vector per input text.

        The vector is derived from a hash of the text so equal texts embed
        equally and different texts differ, while staying a unit vector of the
        configured dimension.

        Args:
            texts: The texts to embed.
            kind: Embedding role (recorded, unused).

        Returns:
            One unit vector per input, in input order.
        """
        self.calls.append((list(texts), kind))
        vectors: list[list[float]] = []
        for text in texts:
            raw = hashlib.sha256(text.encode("utf-8")).digest()
            components = list(raw[: self.dimension])
            norm = math.sqrt(sum(c * c for c in components)) or 1.0
            vectors.append([round(c / norm, 6) for c in components])
        return vectors


class FakePageStore:
    """An in-memory stand-in for `ContentStore` page reads used by fakes.

    Not a protocol implementation; only a convenience for tests that seed page
    text for a fake OCR or extraction step.
    """

    def __init__(self, pages: list[PageText] | None = None) -> None:
        """Initialise with an optional page list.

        Args:
            pages: Pages to serve; empty when None.
        """
        self._pages = list(pages or [])

    async def get_pages(self, document_id: UUID) -> list[PageText]:
        """Return the configured pages.

        Args:
            document_id: Ignored.

        Returns:
            The seeded pages.
        """
        return list(self._pages)


def _fill_schema(schema: dict[str, Any], fallback: str) -> dict[str, Any]:
    """Build a payload that satisfies a compiled tag schema.

    Walks the `fields` object of the envelope schema and produces a `value` for
    every declared field, coercing to the declared type.

    Args:
        schema: The JSON Schema (envelope form, per `processing.schema`).
        fallback: The placeholder string for nullable value fields.

    Returns:
        A dict with a `fields` object mapping field keys to envelope values.
    """
    properties = schema.get("properties", {}).get("fields", {}).get("properties", {})
    fields: dict[str, Any] = {}
    for key, spec in properties.items():
        value_schema = spec.get("properties", {}).get("value", {})
        types = value_schema.get("type")
        enum_values = value_schema.get("enum")
        fields[key] = {
            "value": _placeholder_value(types, enum_values, fallback),
            "confidence": 0.9,
            "page": 1,
            "evidence": "fake evidence",
        }
    return {"fields": fields}


def _placeholder_value(types: Any, enum_values: Any, fallback: str) -> Any:
    """Coerce the placeholder to a plausible value for the declared type.

    Args:
        types: JSON Schema `type` (string or list of strings).
        enum_values: JSON Schema `enum` list, when present.
        fallback: The placeholder string.

    Returns:
        A value of the declared type, or the first enum member when constrained.
    """
    type_list = [types] if isinstance(types, str) else list(types or [])
    if enum_values:
        return enum_values[0]
    if "integer" in type_list:
        return 0
    if "number" in type_list:
        return 0.0
    if "boolean" in type_list:
        return False
    if "array" in type_list:
        return []
    if "string" in type_list:
        return fallback
    return fallback
