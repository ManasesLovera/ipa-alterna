"""NVIDIA VLM OCR provider.

`NvidiaVlmOcrProvider` implements the `OcrProvider` protocol by sending a page
image to a vision-language model with a transcription prompt. VLMs do not return
calibrated OCR confidence, so the result reports `confidence=None` and the OCR
pipeline step derives a heuristic confidence instead.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from ipa.contracts.models import OcrResult
from ipa.core.config import NvidiaSettings
from ipa.providers.nvidia.client import CompletionUsage, RawCompletion, get_client, run_with_retries

_TRANSCRIBE_PROMPT = (
    "Transcribe the text in this page image verbatim. "
    "Preserve reading order; transcribe tables as Markdown. "
    "Do not summarise, do not add commentary. Output only the transcription."
)


class NvidiaVlmOcrProvider:
    """A vision-language model that transcribes page images to text."""

    def __init__(
        self, settings: NvidiaSettings, client: AsyncOpenAI | None = None
    ) -> None:
        """Initialise the provider.

        Args:
            settings: NVIDIA settings carrying the VLM model ID and retry policy.
            client: An `AsyncOpenAI` client; the shared one when None.
        """
        self._settings = settings
        self._client = client or get_client(settings)

    async def ocr_image(self, image: bytes, mime: str) -> OcrResult:
        """Transcribe a single page image.

        The image is passed inline as a base64 data URL in a multi-content
        message, using the configured vision model.

        Args:
            image: Raw image bytes.
            mime: MIME type of the image.

        Returns:
            The transcription with `source="vlm"` and `confidence=None`.

        Raises:
            ProviderError: If transcription fails permanently.
            RetryableProviderError: On transient upstream failures.
        """
        model = self._settings.require_model("vlm")
        data_url = f"data:{mime};base64,{_b64(image)}"

        async def call() -> RawCompletion:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _TRANSCRIBE_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Transcribe this page:"},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                stream=False,
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            return RawCompletion(
                text=content,
                model=getattr(response, "model", "") or model,
                usage=CompletionUsage(
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                ),
            )

        completion = await run_with_retries(
            "ocr",
            call,
            settings=self._settings,
            model=model,
        )
        return OcrResult(text=completion.text, confidence=None, source="vlm")


def _b64(data: bytes) -> str:
    """Return base64-encoded bytes.

    Args:
        data: The bytes to encode.

    Returns:
        A base64 string.
    """
    import base64

    return base64.b64encode(data).decode("ascii")
