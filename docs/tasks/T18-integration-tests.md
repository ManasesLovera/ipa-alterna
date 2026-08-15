# T18 — Integration + E2E tests, seed data

- **Wave:** 5 (parallel with T17)
- **Depends on:** T01–T16
- **Owns:** `tests/integration/`, `tests/e2e/`, `scripts/seed.py`,
  `tests/fixtures/` (shared with T05, coordinate additions)

## Goal

Prove the whole system works together against real infrastructure. Unit tests live
with their owning tasks; this task covers everything that spans components.

**Scope reminder:** per the project decision, there is **no model-accuracy
evaluation**. We do not measure whether extraction got the invoice total right. We
measure that upload works, state advances, retries behave, adapters persist, and APIs
honour their contracts. Providers are faked so results are deterministic.

## Test infrastructure

- `docker-compose.test.yml` — Postgres, Mongo, Redis, MinIO on non-default ports and
  ephemeral volumes, so a test run never touches dev data.
- `conftest.py` with session-scoped fixtures: migrated database, clean stores between
  tests (truncate rather than re-migrate — much faster), an `httpx.AsyncClient` bound
  to the ASGI app, an authenticated client, and a **synchronous Celery mode**
  (`task_always_eager` with `eager_propagates=False`) so pipelines run inline and
  failures behave like production.
- Provider fakes from `src/ipa/providers/fake.py` injected via dependency overrides.
  A separate `@pytest.mark.live` suite hits real NVIDIA endpoints and is skipped
  unless `NVIDIA_API_KEY` is set — smoke only, never in CI by default.

## Integration suites

### `test_ingestion.py`

Upload → verify blob, `documents` row, six `document_steps`, `uploaded` event, one
enqueue. Dedupe on re-upload. `Idempotency-Key` replay. Oversize `413`. Bad MIME
`415`. Batch upload with one bad file.

### `test_pipeline_flow.py`

The core happy path, and the one test that must never be allowed to rot:

1. Create a tag with four fields, `auto_approve_threshold = NULL`.
2. Upload a text-layer PDF.
3. Assert steps complete in `STEP_ORDER`.
4. Assert `document_pages` rows, Mongo pages, an extraction version with
   `is_current`, chunks in pgvector.
5. Assert the document lands in `pending_review` — **because the threshold is NULL**,
   even though the fake provider returned confidence 0.99. This is the project's
   default policy and the regression that would hurt most if it broke.
6. Approve via the review API; assert `validated` → `completed`, a `validations` row,
   and a `document.completed` webhook delivery.

### `test_auto_approval.py`

Same flow with `auto_approve_threshold = 0.8`. Confidence 0.9 across the board →
auto `validated` with `validations.auto = true`. Then one field at 0.3 →
`pending_review` (weakest-link).

### `test_reprocess.py`

Run to completion, then `reprocess(from_step=ocr)`. Assert: ocr/extract/embed/review
reset to `pending`, store/decompose untouched, Mongo pages deleted, chunks deleted,
extraction versions **preserved**, event written, and a full re-run reaches
`pending_review` again with a new extraction version.

Also `reprocess(from_step=store)` → 409.

### `test_failure_paths.py`

- Provider raising `RetryableProviderError` → retries to `max_attempts`, then step
  `failed`, document `failed`, dead-letter row present.
- Encrypted PDF → `quarantined`, zero retries.
- Zip bomb → `quarantined`.
- Step stuck in `running` with an old `started_at` → sweeper resets it.
- Worker crash simulation mid-step → recoverable.

### `test_zip_children.py`

ZIP with three PDFs → three child documents linked to the parent, parent `completed`
with `child_count = 3` and skipped ocr/extract/embed. A duplicate file inside two
archives produces one child, not two.

### `test_search_rag.py`

Seed 20 documents. Exact-identifier query ranks correctly under `hybrid` and
`sparse`. Paraphrase ranks correctly under `hybrid` and `dense`. Filters restrict
correctly. RAG returns citations resolving to real pages. RAG with no hits returns
`answer: null`.

### `test_mcp.py`

Handshake, `tools/list`, `search_documents`, `get_document_text` truncation, auth
rejection, write tools absent when `IPA_MCP_ALLOW_WRITE=false`.

### `test_auth_webhooks.py`

Login/refresh rotation/reuse detection. API key scope enforcement. Webhook signature
verification against a local receiver. Retry/backoff. SSRF rejection.

### `test_deletion.py`

Hard delete removes rows from Postgres, Mongo, pgvector, and MinIO; a sibling document
sharing the SHA-256 keeps its blob.

## E2E (`tests/e2e/`)

Playwright against the compose stack: login → create a tag with fields → upload →
wait for `pending_review` → review and correct a field → approve → search for the
document → confirm it appears. One test, end to end, run in CI on a schedule rather
than every commit.

## Seed data — `scripts/seed.py`

Idempotent. Creates the bootstrap admin, the two example tags from T06, ten synthetic
documents in varied states (`completed`, `pending_review`, `failed`, `quarantined`),
and their chunks. Wired to `make seed`. Generate the sample PDFs programmatically
(ReportLab or PyMuPDF) rather than committing binaries — except the small adversarial
fixtures, which must be committed because they cannot be generated safely.

## CI

GitHub Actions workflow:

1. `lint` + `typecheck` + `test-unit` on every push (fast, no services).
2. `test-integration` with the compose stack on pull requests.
3. `e2e` nightly.
4. Coverage reported; fail under 70% on `src/ipa/`.

## Acceptance criteria

- `make test-integration` green from a cold start with one command.
- Suites are independent and can run in any order or in parallel with `pytest -n`.
- No test depends on a real NVIDIA API key except the `live`-marked smoke suite.
- Total integration runtime under five minutes on a laptop.
- `make seed` twice in a row produces the same state and no errors.
