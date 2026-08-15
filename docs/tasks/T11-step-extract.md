# T11 — Pipeline step: structured extraction + confidence

- **Wave:** 3 (parallel with T09, T10, T12)
- **Depends on:** T04 (LLM provider), T05 (`processing/schema.py`), T06 (tag
  service + prompts), T08, T03, T02
- **Owns:** `src/ipa/pipeline/steps/extract.py`, `src/ipa/domain/extraction.py`,
  `tests/unit/pipeline/test_extract.py`

## Goal

Turn page text (and, where useful, page images) into a validated JSON object matching
the document's tag schema, with a confidence and evidence snippet per field.

## Preconditions

- The document must have a `tag_id`. If it does not:
  1. If exactly one active tag exists, use it.
  2. Otherwise run **classification** (below) to pick one.
  3. If classification is inconclusive, mark the step `SKIPPED`, set the document to
     `pending_review` with `metadata.needs_tag = true`, and stop. A human assigns the
     tag in the UI, which triggers a reprocess from `extract`.

Never guess silently — an unassigned tag is a review item, not an error.

## Classification

`src/ipa/domain/extraction.py::classify(document_id) -> tuple[UUID, float] | None`

Send the first `IPA_CLASSIFY_PAGES` (default 2) pages of text plus each active tag's
`name`, `description`, and `classification_hints` to the LLM with a small enum schema
(`{"tag_slug": ..., "confidence": ...}`). Accept the result only when
`confidence >= IPA_CLASSIFY_MIN_CONFIDENCE` (default 0.7). Record the decision as a
`classified` event.

Per the project decision, **do not build accuracy evaluation for classification** —
just make it observable (event + metric) and correctable by a human.

## Extraction

1. Fetch `CompiledSchema` from `TagService.compiled_schema(tag_id)` (Redis-cached).
2. Assemble the user message:
   - Document metadata (filename, page count).
   - Page text, delimited with explicit `<page n="1">…</page>` markers so the model
     can populate the `page` field of each extracted value.
   - When `IPA_EXTRACT_INCLUDE_IMAGES` and the tag has visual fields (or OCR
     confidence is low), attach up to `IPA_EXTRACT_MAX_IMAGES` page images. Vision
     materially helps on tables and forms — but it costs, so gate it.
3. **Long documents:** if estimated tokens exceed `IPA_EXTRACT_MAX_INPUT_TOKENS`,
   do not truncate blindly. Run a per-field retrieval pass: embed nothing, just
   select the top pages by keyword overlap with the field labels/descriptions, and
   send those pages plus the first and last page. Record `metrics.pages_sent` and
   `metrics.pages_total` so the reduction is visible.
4. Call `LlmProvider.complete_json(system=prompt, user=..., json_schema=..., images=...)`.
5. Parse into `list[ExtractedField]`.

## Post-processing

For each field, in order:

1. **Coerce** `value` to the declared type — parse dates (ISO first, then locale
   hints from tag metadata), strip currency symbols and thousands separators for
   numbers, normalise booleans. Coercion failure sets `value = None` and adds a
   validation error; it never raises.
2. **Validate** with `processing/validation_rules.py` — required, regex, range,
   length, enum, date-parseable. Populate `valid` and `validation_errors`.
3. **Adjust confidence.** A field that fails validation is clamped to
   `min(confidence, 0.4)`. A field with `value is None` and `is_required` gets
   `confidence = 0.0`. Never let the model self-report high confidence on something we
   can prove is wrong.

## Document confidence

```text
document_confidence = weighted_mean(field.confidence)
    weight = 2.0 if field.is_required else 1.0
```

If any required field is missing or invalid, cap `document_confidence` at `0.5`.
With no fields defined, `document_confidence = 1.0`. Implement this in
`src/ipa/domain/extraction.py::score_document` — **T13 imports it**, so keep it pure
and separately testable.

## Persistence

- Append a new `ExtractionRecord` to Mongo with `version = max + 1`,
  `source = MODEL`, `model`, `prompt_hash = schema_hash + prompt hash`.
- Insert an `extraction_versions` row; set `is_current = true` and clear the flag on
  the previous current row **in one transaction**.
- Update `documents.document_confidence`, `tag_id`, `tag_version`.
- Invalidate the Redis extraction cache for the document.
- Store the raw model response via `put_raw_response`.
- Write an `extracted` event.

**Never update an existing extraction.** Human corrections in T13 create a new
version with `source = HUMAN`.

`StepResult.metrics`: `fields_total`, `fields_populated`, `fields_invalid`,
`document_confidence`, `input_tokens`, `output_tokens`, `repairs`, `pages_sent`,
`images_sent`, `latency_ms`.

## Acceptance criteria

- With `FakeLlmProvider` returning a well-formed object: one extraction version
  written, `is_current` correct, document confidence computed, cache invalidated.
- Second run creates version 2 and flips `is_current`; version 1 remains readable.
- Field failing its regex → `valid == false`, error message present, confidence
  clamped to ≤ 0.4.
- Missing required field → `confidence == 0.0` and document confidence ≤ 0.5.
- Date coercion: `"31/12/2024"`, `"2024-12-31"`, and `"Dec 31, 2024"` all normalise;
  `"not a date"` sets `value = None` plus an error, no exception.
- Number coercion: `"$1,234.56"` → `1234.56`.
- Model returning JSON with an unexpected extra key → rejected by schema validation,
  one repair attempt made, then `ProviderError` with the raw text persisted.
- Document with no tag and two active tags → classification runs; below-threshold
  confidence leaves the step `SKIPPED` and the document `pending_review` with
  `needs_tag`.
- Oversized document triggers page selection; `pages_sent < pages_total`.
