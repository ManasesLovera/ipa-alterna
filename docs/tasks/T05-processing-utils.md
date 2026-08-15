# T05 — Processing utilities: PDF, ZIP, images, chunking

- **Wave:** 1 (parallel with T02, T03, T04)
- **Depends on:** T01
- **Owns:** `src/ipa/processing/`

## Goal

Pure, synchronous, dependency-light functions for handling file bytes. No database,
no network, no settings singletons — everything is passed in as arguments. This makes
them trivially testable and reusable by both the pipeline and the API.

## Deliverables

### `src/ipa/processing/detect.py`

- `sniff_mime(data: bytes, filename: str) -> str` — magic-byte detection first,
  extension only as a tiebreak. **Never trust the client-supplied content type.**
- `is_allowed(mime: str, allowed: set[str]) -> bool`.
- `sha256_hex(data: bytes) -> str` and a streaming `sha256_stream(reader) -> str`.
- `classify_container(mime) -> Literal["pdf", "image", "archive", "unsupported"]`.

### `src/ipa/processing/pdf.py`

Built on PyMuPDF.

- `pdf_info(data) -> PdfInfo` — page count, encrypted flag, has-text-layer per page,
  producer metadata.
- `is_encrypted(data) -> bool` — an encrypted PDF without a password is a
  **quarantine**, not a failure.
- `extract_text_layer(data, page) -> str` — native text extraction.
- `has_usable_text_layer(text, min_chars, min_alnum_ratio=0.55) -> bool` — the
  gate that decides whether OCR is needed at all. Guard against PDFs whose "text
  layer" is a garbage font mapping: require both a minimum character count and a
  minimum ratio of alphanumeric characters.
- `render_page(data, page, dpi) -> bytes` — PNG bytes.
- `render_pages(data, dpi, pages=None) -> Iterator[tuple[int, bytes]]` — generator so
  a 500-page PDF never materialises fully in memory.
- `make_thumbnail(png: bytes, max_px=320) -> bytes` — JPEG.

### `src/ipa/processing/archive.py`

- `list_archive(data) -> list[ArchiveEntry]` — name, size, compressed size.
- `extract_archive(data, allowed_mimes, limits) -> Iterator[ExtractedFile]`.

**Zip-bomb and traversal defences are mandatory:**

- Reject entries whose normalised path escapes the root (`..`, absolute, symlinks).
- Reject when total uncompressed size exceeds `max_total_bytes` (default 1 GB).
- Reject when any entry's compression ratio exceeds `max_ratio` (default 100:1).
- Reject when entry count exceeds `max_entries` (default 500).
- Reject nested archives beyond depth 1.
- Encrypted zip → `QuarantineError`.

All rejections raise `QuarantineError` with a specific `code`.

### `src/ipa/processing/images.py`

- `normalise_image(data) -> tuple[bytes, str]` — convert TIFF/BMP/HEIC to PNG,
  strip EXIF, correct orientation from the EXIF orientation tag.
- `image_dimensions(data) -> tuple[int, int]`.
- `downscale_for_vlm(data, max_long_edge=2048) -> bytes` — keeps VLM token cost sane.
- `deskew(data) -> bytes` (best-effort, OpenCV-free implementation using PIL +
  a simple projection-profile estimate). Used only in the OCR fallback path.
- `is_probably_blank(data, threshold=0.995) -> bool` — skip blank scanned pages.

### `src/ipa/processing/chunking.py`

- `chunk_pages(pages: list[PageText], *, target_tokens, overlap_tokens) -> list[Chunk]`

Rules:

- Chunk boundaries respect page boundaries where possible; a chunk records
  `page_from`/`page_to`.
- Split on paragraph, then sentence, then hard-wrap — never mid-word.
- Overlap is applied at the sentence level, not by raw character slicing.
- Very short pages are merged forward rather than emitted as tiny chunks.
- Markdown tables produced by OCR are kept intact even if they exceed the target
  size (splitting a table destroys its meaning) — allow up to 2× target for tables.
- `estimate_tokens(text) -> int` — a cheap heuristic (chars/4 with a CJK adjustment).
  We do not have a tokenizer for the NVIDIA models; document that this is approximate.

### `src/ipa/processing/schema.py`

The tag → JSON Schema compiler. **T06 and T11 both depend on this**; it lives here
because it is pure.

- `build_json_schema(fields: list[TagFieldSpec]) -> dict` — produces a Draft 2020-12
  schema with `additionalProperties: false` and a `required` list from
  `is_required` fields.
- Every field is wrapped in the extraction envelope so the model returns confidence
  and evidence alongside the value:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["fields"],
  "properties": {
    "fields": {
      "type": "object",
      "additionalProperties": false,
      "required": ["invoice_number"],
      "properties": {
        "invoice_number": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "confidence"],
          "properties": {
            "value": { "type": ["string", "null"] },
            "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
            "page": { "type": ["integer", "null"] },
            "evidence": { "type": ["string", "null"] }
          }
        }
      }
    }
  }
}
```

- `value` is always nullable — the model must be able to say "not present" rather
  than hallucinate. A null value on a required field becomes a validation error
  downstream, which is the correct signal.
- Type mapping: `DATE`/`DATETIME` → `string` with `format`, `ENUM` → `enum`,
  `ARRAY` → `array` of `item_type`, `OBJECT` → the stored `object_schema`.
- `schema_hash(schema) -> str` — stable SHA-256 over canonical JSON (sorted keys) for
  cache keys and prompt-hash recording.

### `src/ipa/processing/validation_rules.py`

Pure field validators used by T13: `check_required`, `check_regex`, `check_range`,
`check_length`, `check_enum`, `check_date_parseable`. Each returns
`list[str]` of error messages. Keep them side-effect free.

## Acceptance criteria

- 100% of this module is unit-testable with fixture files; no service dependencies.
- Fixtures committed under `tests/fixtures/`: a text-layer PDF, a scanned PDF, an
  encrypted PDF, a multi-page TIFF, a benign ZIP, a zip bomb, a traversal ZIP.
- Zip-bomb fixture raises `QuarantineError`; traversal fixture raises
  `QuarantineError`; neither writes to disk.
- `has_usable_text_layer` returns `False` for the scanned PDF and `True` for the
  text-layer PDF.
- Chunking test: overlap present, no chunk exceeds 2× target, page ranges correct,
  a Markdown table survives intact.
- `build_json_schema` output validates as a legal JSON Schema and round-trips through
  `jsonschema.Draft202012Validator.check_schema`.
- `schema_hash` is stable across key reordering.
