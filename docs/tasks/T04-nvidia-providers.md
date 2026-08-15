# T04 — NVIDIA providers + local OCR fallback

- **Wave:** 1 (parallel with T02, T03, T05)
- **Depends on:** T01
- **Owns:** `src/ipa/providers/`

## Goal

Implement `LlmProvider`, `OcrProvider`, and `EmbeddingProvider` against NVIDIA NIM,
plus a local OCR fallback. Everything behind the protocols so the provider can be
swapped without touching pipeline code.

## NVIDIA NIM notes

NVIDIA's hosted inference API is **OpenAI-compatible**. Use the `openai` Python SDK:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=settings.nvidia.api_key,
    base_url=settings.nvidia.base_url,   # https://integrate.api.nvidia.com/v1
    timeout=settings.nvidia.timeout_s,
    max_retries=0,                       # we do our own retry with jitter
)
```

Model IDs are **configuration, not constants** — `NVIDIA_LLM_MODEL`,
`NVIDIA_VLM_MODEL`, `NVIDIA_EMBED_MODEL` come from env and are blank in
`.env.example`. Validate at startup that a required one is non-empty and fail with a
clear message naming the env var.

Two known behavioural differences from OpenAI proper — handle both:

1. **Embeddings require an `input_type` parameter** (`"query"` or `"passage"`) on
   NVIDIA retrieval-embedding models. Pass it via `extra_body={"input_type": ...}`.
   Also send `truncate: "END"` in `extra_body` so over-long inputs do not 400.
2. **Structured output support varies by model.** Do not assume
   `response_format={"type": "json_schema", ...}` works. Implement a capability
   ladder (below).

## Deliverables

### `src/ipa/providers/nvidia/client.py`

Shared `AsyncOpenAI` client factory + a retry wrapper:

- Retries on 429, 5xx, and connection/timeout errors — exponential backoff with
  full jitter, `NVIDIA_MAX_RETRIES` attempts, honouring `Retry-After` when present.
- Raises `RetryableProviderError` when attempts are exhausted on a retryable class,
  `ProviderError` otherwise. The pipeline distinguishes these.
- Emits an OTel span per call with attributes: `provider`, `model`, `operation`,
  `prompt_tokens`, `completion_tokens`, `attempt`, `latency_ms`.
- A shared `asyncio.Semaphore` for concurrency limiting (`IPA_NVIDIA_MAX_CONCURRENCY`,
  default 4) so a burst of pages does not blow the rate limit.

### `src/ipa/providers/nvidia/llm.py`

`NvidiaLlmProvider(LlmProvider)` with `complete_json(...)`.

**JSON capability ladder** — try in order, remember what worked per model in an
in-process cache:

1. `response_format={"type": "json_schema", "json_schema": {...}}`
2. `response_format={"type": "json_object"}` + schema embedded in the system prompt
3. Plain completion + schema in the prompt, then extract the first balanced JSON
   object from the response (strip ```` ```json ```` fences).

After parsing, **validate against the schema** (`jsonschema` or a dynamically-built
Pydantic model). On validation failure, make **one** repair attempt: send the model
its own output plus the validation errors and ask for a corrected object. If that
fails, raise `ProviderError` with the raw text attached — the raw text is persisted
to `raw_responses` by the caller.

`images: list[ImageRef]` support: when present, use the VLM model and build
multi-content messages with `image_url` entries. Images are passed as
`data:{mime};base64,{...}` — the provider is responsible for fetching bytes via the
`BlobStore` handed to it at construction (inject, do not import).

### `src/ipa/providers/nvidia/ocr.py`

`NvidiaVlmOcrProvider(OcrProvider)` — `ocr_image(image, mime)` sends the page image
to the VLM with a transcription prompt and returns `OcrResult(source="vlm")`.

Prompt requirements: preserve reading order, transcribe tables as Markdown, do not
summarise, do not add commentary, output only the transcription. Return
`confidence=None` (VLMs do not give calibrated OCR confidence) — the OCR step derives
a heuristic confidence instead.

### `src/ipa/providers/local/tesseract.py`

`TesseractOcrProvider(OcrProvider)` — `pytesseract` with `IPA_TESSERACT_LANGS`.
Use `image_to_data` to compute a real mean word confidence and return it in
`OcrResult(source="tesseract", confidence=...)`. Runs in a thread executor.
Add `tesseract-ocr` + language packs to the Dockerfile (coordinate with T01 by
documenting the required apt packages in this task's notes — T01 owns the Dockerfile,
so leave a `TODO(T04)` comment and open a follow-up line in `docs/tasks/T04-*.md`).

### `src/ipa/providers/nvidia/embeddings.py`

`NvidiaEmbeddingProvider(EmbeddingProvider)`.

- `embed(texts, kind)` batches (default 32 per request, configurable), passes
  `input_type` from `kind`, and returns vectors in input order.
- Asserts the returned dimension equals `NVIDIA_EMBED_DIM`; mismatch is a startup-class
  error, not a silent truncation.
- Exposes `model` and `dimension` properties (recorded on every chunk row).

### `src/ipa/providers/factory.py`

`get_llm_provider()`, `get_ocr_providers()` (returns the ordered fallback chain),
`get_embedding_provider()` — cached singletons honouring
`IPA_OCR_FALLBACK_ENABLED`.

### `src/ipa/providers/fake.py`

Deterministic fakes for every protocol, used by all other tasks' unit tests.
`FakeLlmProvider` returns a schema-shaped object filled with placeholder values;
`FakeOcrProvider` returns fixed text; `FakeEmbeddingProvider` returns a hashed
pseudo-random unit vector of the configured dimension. **Other tasks depend on these
existing** — ship them.

## Acceptance criteria

- Unit tests use `respx`/`httpx` mock transport — no real network.
- Retry test: 429 twice then 200 → success, 3 calls made, backoff applied.
- Ladder test: model that rejects `json_schema` falls through to `json_object`, then
  to prompt-only; each path parses correctly.
- Repair test: invalid JSON on attempt 1, valid after repair → returns data, records
  that a repair happened in `LlmJsonResult` metrics.
- Fence-stripping test: response wrapped in a ```` ```json ```` block parses.
- Embedding test: `input_type` present in the request body; dimension asserted;
  batching splits a 100-item input into 4 requests.
- Tesseract test: skipped if the binary is absent, otherwise OCRs a generated image.
- `get_ocr_providers()` returns `[vlm, tesseract]` when fallback enabled, `[vlm]`
  when disabled.
