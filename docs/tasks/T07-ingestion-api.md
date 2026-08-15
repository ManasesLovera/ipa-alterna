# T07 — Ingestion API: upload, dedupe, status, download

- **Wave:** 2 (parallel with T06, T08)
- **Depends on:** T01, T02, T03, T05
- **Owns:** `src/ipa/domain/documents.py`, `src/ipa/api/routers/documents.py`,
  `src/ipa/api/schemas/documents.py`

## Goal

The front door. Accept uploads from the UI and from other applications, deduplicate
by SHA-256, honour `Idempotency-Key`, persist metadata + blob, initialise pipeline
steps, enqueue processing, and expose status/content/download.

## Upload semantics

`POST /v1/documents` — `multipart/form-data`.

Parts: `file` (required), `tag_id` or `tag_slug` (optional), `title` (optional),
`metadata` (optional JSON string).

Header: `Idempotency-Key` (optional but recommended).

Flow:

1. **Idempotency claim.** If the header is present, `claim_idempotency` in Redis. On
   a lost claim, look up `idempotency_keys` in Postgres and replay the stored
   response verbatim (`200` with the original body, not a new upload).
2. **Stream to a temp buffer while hashing.** Enforce `IPA_MAX_UPLOAD_MB` during the
   stream — reject with `413` as soon as the limit is passed, do not read the rest.
3. **Sniff MIME from magic bytes** (`processing.detect.sniff_mime`). Reject
   disallowed types with `415`. The client-declared content type is ignored.
4. **Dedupe on SHA-256.** If a document with that hash exists and is not soft-deleted:
   - Return `200` with the existing document and `"deduplicated": true`.
   - If the caller supplied a `tag_id` and the existing document has none, attach it
     and enqueue from the `extract` step. Otherwise do nothing else.
5. **Store the blob** at `originals/{sha[:2]}/{sha}`. `BlobStore.put` skips the write
   if the key already exists.
6. **Insert the `documents` row**, `ensure_steps()` to create the six
   `document_steps` rows, mark `store` as `succeeded` (we just did it), write an
   `uploaded` event, and generate a `trace_id`.
7. **Enqueue** the pipeline from `decompose` (T08's `enqueue_from`).
8. Return `202` with the document resource. Persist the response into
   `idempotency_keys` for replay.

Return `202` for new documents, `200` for deduplicated ones — the distinction is
useful to clients and must be documented in the OpenAPI description.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/documents` | Upload (above) |
| `POST` | `/v1/documents/batch` | Multiple files in one multipart request; returns per-file results with individual statuses, never fails the whole batch for one bad file |
| `GET` | `/v1/documents` | List with filters: `status`, `tag_id`, `needs_review`, `q` (filename/title ILIKE), `created_after`, `created_before`, `limit`, `cursor`; sort by `created_at desc` |
| `GET` | `/v1/documents/{id}` | Full resource incl. per-step status array |
| `GET` | `/v1/documents/{id}/steps` | Step detail: status, attempt, timings, error, metrics |
| `GET` | `/v1/documents/{id}/events` | Paginated audit log |
| `GET` | `/v1/documents/{id}/pages` | Page index with presigned image + thumbnail URLs |
| `GET` | `/v1/documents/{id}/content` | OCR text from Mongo; `?page=` for one page |
| `GET` | `/v1/documents/{id}/extraction` | Current extraction; `?version=` for a specific one |
| `GET` | `/v1/documents/{id}/extractions` | Version list |
| `GET` | `/v1/documents/{id}/download` | `302` to a presigned MinIO URL (`?inline=true` for `Content-Disposition: inline`) |
| `PATCH` | `/v1/documents/{id}` | Update `title`, `tag_id`, `metadata`. Changing `tag_id` resets steps from `extract` and re-enqueues |
| `DELETE` | `/v1/documents/{id}` | Soft delete by default; `?hard=true` cascades to Mongo, chunks, and blob |

`POST /v1/documents/{id}/reprocess` is owned by **T08** — do not implement it here.

## Deliverables

### `src/ipa/domain/documents.py` — `DocumentService`

Constructor takes repositories and the three stores by protocol — no global lookups,
so tests inject fakes.

```python
async def ingest(payload: UploadPayload) -> IngestResult
async def ingest_batch(payloads: list[UploadPayload]) -> list[IngestResult]
async def get(document_id: UUID) -> DocumentRead
async def list(filters: DocumentFilters, page: PageParams) -> Page[DocumentRead]
async def steps(document_id: UUID) -> list[StepRead]
async def events(document_id: UUID, page: PageParams) -> Page[EventRead]
async def pages(document_id: UUID) -> list[PageRead]
async def content(document_id: UUID, page: int | None) -> ContentRead
async def extraction(document_id: UUID, version: int | None) -> ExtractionRead
async def download_url(document_id: UUID, inline: bool) -> str
async def update(document_id: UUID, patch: DocumentPatch) -> DocumentRead
async def delete(document_id: UUID, hard: bool) -> None
```

`IngestResult` carries `document, deduplicated: bool, http_status: int`.

Cache the extraction read in Redis (`extraction(document_id, version)`), invalidated
whenever a new extraction version is written. That is the "Redis for caching
structured content" requirement — implement it here.

### Hard delete

Must remove, in order: chunks → Mongo pages + extractions → page images and
thumbnails in MinIO → the original blob **only if no other document shares the
SHA-256** → the Postgres row. Write a final `deleted` event before removing the row
(events cascade, so also emit an application log line). Wrap in a Redis lock on the
document ID.

### Streaming and memory

Uploads must never be fully buffered twice. Use `SpooledTemporaryFile`, hash while
streaming, then hand the file object to the blob store. A 200 MB PDF must not use
400 MB of RSS.

## Acceptance criteria

- Same file uploaded twice → one `documents` row, second response `200` with
  `deduplicated: true`, blob written once (spy on `BlobStore.put`).
- Same `Idempotency-Key` replayed → identical response body and status, no second
  document, no second enqueue.
- A `.pdf` extension containing PNG bytes is stored with `image/png`.
- A `.exe` renamed to `.pdf` is rejected `415` and no blob is written.
- Oversize upload rejected `413` without reading the whole body.
- After a successful upload: six `document_steps` rows exist, `store` is `succeeded`,
  `decompose` is `pending`, an `uploaded` event exists, and one enqueue call was made.
- `GET /documents/{id}/download` returns `302` with a working presigned URL.
- Hard delete removes rows from all four stores; a shared-hash sibling keeps its blob.
- Batch upload with one bad file returns `207`-style per-item results, the good files
  ingested.
