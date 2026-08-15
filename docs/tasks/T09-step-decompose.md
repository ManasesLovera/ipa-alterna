# T09 — Pipeline step: decompose (PDF / ZIP → PNG / JPEG)

- **Wave:** 3 (parallel with T10, T11, T12)
- **Depends on:** T05 (`processing/pdf.py`, `archive.py`, `images.py`), T08, T03, T02
- **Owns:** `src/ipa/pipeline/steps/decompose.py`, `tests/unit/pipeline/test_decompose.py`

## Goal

Turn whatever was uploaded into a normalised set of page images plus a
`document_pages` index, so every downstream step sees the same shape regardless of
input format.

## Behaviour by input type

| Input | Action |
| --- | --- |
| PDF (unencrypted) | Render every page at `IPA_OCR_PAGE_DPI` to PNG; record whether each page has a usable text layer |
| PDF (encrypted) | `QuarantineError(code="pdf_encrypted")` |
| Image (PNG/JPEG/TIFF/BMP) | Normalise to PNG, strip EXIF, apply orientation; multi-page TIFF becomes multiple pages |
| ZIP | Expand under the T05 safety limits; each contained file becomes a **child document** (see below) |
| Anything else | `UnsupportedMediaError` |

## ZIP handling — child documents

A ZIP is a container, not a document. Expanding it creates one new `documents` row per
contained file:

- Each child is ingested through the same content-addressed path (SHA-256 dedupe
  applies — the same invoice inside two archives is one document).
- Child rows carry `metadata.parent_document_id` and `metadata.archive_entry_name`.
- The child inherits the parent's `tag_id` if the parent had one.
- Each child gets its own six `document_steps` rows and is enqueued from `decompose`.
- The **parent** document skips `ocr`, `extract`, and `embed`
  (`StepResult.status = SKIPPED` for those, set via `next_step_override = REVIEW`
  or by marking them skipped directly) and reaches a terminal `completed` status with
  `metadata.child_count`.

Do not recurse into nested archives — T05 already rejects depth > 1.

Reuse `DocumentService.ingest` for children rather than writing a second ingest path.
Import it lazily inside the function to avoid a circular import.

## Outputs

For each page:

1. Upload the PNG to `pages/{document_id}/{page:05d}.png`.
2. Upload a JPEG thumbnail to `thumbs/{document_id}/{page:05d}.jpg`.
3. Insert a `document_pages` row with `blob_key`, `thumb_key`, `width`, `height`,
   and `text_source = "text_layer"` when a usable text layer was detected (this is a
   hint; T10 makes the final call).

Update `documents.page_count`.

`StepResult.metrics`: `pages`, `render_ms`, `bytes_written`, `text_layer_pages`,
`children_created`.

## Implementation notes

- **Stream page by page.** Use `render_pages` as a generator and upload each page
  before rendering the next. A 500-page scan must not accumulate in memory.
- **Idempotent.** Re-running deletes existing `document_pages` rows and page blobs
  for the document first, then regenerates. Use the same blob keys so storage does not
  grow on retries.
- **Blank-page handling.** Pages detected as blank still get a `document_pages` row
  (page numbering must stay faithful to the original) but are flagged
  `metadata.blank = true` so T10 can skip OCR on them.
- Rendering is CPU-bound — run it in a thread executor, and cap concurrent renders
  per worker.
- Enforce a page ceiling (`IPA_MAX_PAGES`, default 1000). Exceeding it is a
  `QuarantineError(code="too_many_pages")`, not a crash.

## Acceptance criteria

- Text-layer PDF fixture → N page rows, N page blobs, N thumbnails,
  `text_layer_pages == N`.
- Scanned PDF fixture → pages created, `text_layer_pages == 0`.
- Encrypted PDF fixture → document `quarantined`, code `pdf_encrypted`, zero blobs.
- Multi-page TIFF → one page row per frame.
- Single PNG upload → exactly one page, dimensions correct, EXIF stripped.
- ZIP fixture with 3 PDFs → 3 child documents created and enqueued; parent reaches
  `completed` with `child_count == 3` and skipped ocr/extract/embed steps.
- Zip bomb fixture → parent `quarantined`, no children.
- Re-running decompose twice produces the same page count and does not duplicate rows
  or leak blobs.
- Blank page is flagged and still occupies its page number.
