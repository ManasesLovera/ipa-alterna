"""NVIDIA embedding provider.

`NvidiaEmbeddingProvider` implements the `EmbeddingProvider` protocol. NVIDIA
retrieval-embedding models are asymmetric and require an `input_type` parameter
(`query` or `passage`) which is derived from the `kind` argument; over-long
inputs are truncated rather than rejected.
"""

from __future__ import annotations

from typing import Literal

from openai import AsyncOpenAI

from ipa.core.config import NvidiaSettings
from ipa.core.errors import ConfigurationError
from ipa.providers.nvidia.client import get_client, run_with_retries


class NvidiaEmbeddingProvider:
    """Produces embedding vectors for text using an NVIDIA retrieval model."""

    def __init__(
        self,
        settings: NvidiaSettings,
        client: AsyncOpenAI | None = None,
        *,
        batch_size: int = 32,
    ) -> None:
        """Initialise the provider.

        Args:
            settings: NVIDIA settings carrying the embedding model ID and dim.
            client: An `AsyncOpenAI` client; the shared one when None.
            batch_size: Maximum texts per embedding request.
        """
        self._settings = settings
        self._client = client or get_client(settings)
        self._batch_size = batch_size

    @property
    def model(self) -> str:
        """Return the configured embedding model ID.

        Returns:
            The model ID.

        Raises:
            ConfigurationError: If the model ID is blank.
        """
        return self._settings.require_model("embed")

    @property
    def dimension(self) -> int:
        """Return the expected vector dimensionality.

        Returns:
            The configured `NVIDIA_EMBED_DIM`.
        """
        return self._settings.embed_dim

    async def embed(
        self, texts: list[str], *, kind: Literal["query", "passage"]
    ) -> list[list[float]]:
        """Embed a batch of texts, preserving input order.

        Texts are embedded in batches of `self._batch_size`; each request passes
        `input_type` derived from `kind` and `truncate: "END"`.

        Args:
            texts: The texts to embed.
            kind: Asymmetric embedding role; `query` vs `passage` differ.

        Returns:
            One vector per input, in the same order.

        Raises:
            ConfigurationError: If the returned dimension does not match the
                configured `NVIDIA_EMBED_DIM`.
            ProviderError: On a permanent provider failure.
            RetryableProviderError: On transient upstream failures.
        """
        model = self._settings.require_model("embed")
        input_type = kind
        vectors: list[list[float]] = []

        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]

            async def embed_batch(
                _batch: list[str] = batch,
                _model: str = model,
                _input_type: Literal["query", "passage"] = input_type,
            ) -> list[list[float]]:
                return await self._embed_batch(_batch, _model, _input_type)

            result = await run_with_retries(
                "embed",
                embed_batch,
                settings=self._settings,
                model=model,
            )
            vectors.extend(result)

        self._check_dimension(vectors)
        return vectors

    async def _embed_batch(
        self, texts: list[str], model: str, input_type: Literal["query", "passage"]
    ) -> list[list[float]]:
        """Embed one batch through the OpenAI-compatible endpoint.

        Args:
            texts: The texts in this batch.
            model: The model ID.
            input_type: The NVIDIA `input_type` value.

        Returns:
            One vector per text.

        Raises:
            ProviderError: If the batch request fails permanently.
        """
        response = await self._client.embeddings.create(
            model=model,
            input=texts,
            extra_body={"input_type": input_type, "truncate": "END"},
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]

    def _check_dimension(self, vectors: list[list[float]]) -> None:
        """Fail fast if the returned vectors have the wrong dimensionality.

        Args:
            vectors: The embedded vectors.

        Raises:
            ConfigurationError: If any vector's length differs from the
                configured dimension.
        """
        expected = self._settings.embed_dim
        if vectors and any(len(v) != expected for v in vectors):
            raise ConfigurationError(
                f"NVIDIA_EMBED_MODEL returned vectors of dimension {len(vectors[0])} "
                f"but NVIDIA_EMBED_DIM is {expected}. Update the configuration."
            )
