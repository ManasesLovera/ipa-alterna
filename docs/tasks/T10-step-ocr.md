# T10 — Pipeline step: OCR with fallback chain

- **Wave:** 3 (parallel with T09, T11, T12)
- **Depends on:** T04 (providers), T05 (pdf/images), T08, T03, T02
- **Owns:** `src/ipa/pipeline/steps/ocr.py`, `tests/unit/pipeline/test_ocr.py`

## Goal

Produce clean text for every page and persist it to MongoDB, using the cheapest
method that works.

## Resolution order per page

1. **Native text layer** — if `document_pages.text_source == "text_layer"` (set by
   T09) and `has_usable_text_layer()` confirms it, extract with PyMuPDF.
   **Zero API cost, zero latency.** This is the single biggest cost lever in the
   platform; do not skip it.
2. **NVIDIA VLM OCR** — send the page image (downscaled via
   `downscale_for_vlm`) to `NvidiaVlmOcrProvider`.
3. **Local Tesseract** — only when `IPA_OCR_FALLBACK_ENABLED` and step 2 raised
   `ProviderError`/`RetryableProviderError` after its own retries, or returned text
   below `IPA_OCR_MIN_CHARS_ACCEPT`. Apply `deskew` before OCR here — it materially
   helps Tesseract and is wasted effort for the VLM.

Record which one won in `PageText.source` and `document_pages.text_source`. That
field is the audit trail for "why is this page's text bad".

If all three fail for a page, do **not** fail the whole document. Record the page with
empty text, `source` of the last attempt, and `metrics.failed_pages += 1`. Fail the
step only if **every** page failed, or if failed pages exceed
`IPA_OCR_MAX_FAILED_PAGE_RATIO` (default 0.5).

Blank pages (flagged by T09) are skipped entirely and stored with empty text and
`source = "blank"`.

## Confidence

- `text_layer` → `confidence = 1.0` (it is the authored text, not a guess).
- `tesseract` → mean word confidence from `image_to_data`, scaled to 0–1.
- `vlm` → no calibrated score exists. Derive a heuristic in
  `src/ipa/pipeline/steps/ocr.py`:
  `min(1.0, alnum_ratio * 0.5 + min(char_count / 400, 1.0) * 0.5)`, and document
  clearly in the docstring that this is a heuristic proxy, not a model confidence.
  Never present it to users as a model-reported score — the API field is named
  `ocr_confidence` and the UI labels it "text quality".

## Persistence

- `ContentStore.put_pages(document_id, pages)` — bulk upsert, idempotent.
- Update each `document_pages` row with `text_source`, `char_count`,
  `ocr_confidence`.
- Store the raw VLM response via `put_raw_response` when the VLM path is used (TTL'd,
  for debugging).

`StepResult.metrics`: `pages`, `text_layer_pages`, `vlm_pages`, `tesseract_pages`,
`blank_pages`, `failed_pages`, `total_chars`, `provider_ms`.

## Concurrency and cost control

- Process pages with a bounded `asyncio.Semaphore` (`IPA_OCR_PAGE_CONCURRENCY`,
  default 4), sharing the provider-level semaphore from T04.
- Pages are independent — a failure on page 7 must not cancel pages 8–20. Use
  `asyncio.gather(..., return_exceptions=True)` and handle results individually.
- Emit an OTel span per page with `page`, `source`, `chars`, `latency_ms`.

## Idempotency

Re-running OCR replaces all Mongo pages for the document (the upsert is keyed on
`(document_id, page)`), so a retry never duplicates. If the step is retried after a
partial success, only re-OCR pages that are missing or empty unless
`force = true` is passed in `StepContext` metadata — cheap resume for a step that
died at page 300 of 500.

## Acceptance criteria

- Text-layer PDF: zero provider calls made (assert with a spy), all pages
  `source == "text_layer"`, `confidence == 1.0`.
- Scanned PDF with a working fake VLM: all pages `source == "vlm"`.
- VLM raising `ProviderError` with fallback enabled → pages `source == "tesseract"`.
- VLM failing with fallback **disabled** → those pages recorded as failed; step fails
  only when the failed ratio exceeds the threshold.
- One page of ten failing → step succeeds, `failed_pages == 1`.
- Six of ten failing → step fails.
- Blank page recorded with empty text and `source == "blank"`, no provider call.
- Re-running the step twice yields the same page count in Mongo (no duplicates).
- Partial-resume test: pre-seed pages 1–5, re-run, only pages 6–10 hit the provider.
- Heuristic confidence is monotonic in character count for fixed alnum ratio.
