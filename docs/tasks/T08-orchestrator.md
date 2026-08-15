# T08 — Pipeline orchestrator core + reprocessing

- **Wave:** 2 (parallel with T06, T07)
- **Depends on:** T01, T02, T03
- **Owns:** `src/ipa/pipeline/` **except** `src/ipa/pipeline/steps/*.py`
  (T09–T12 own those), `src/ipa/api/routers/pipeline.py`

## Goal

The engine. A Celery-backed, Postgres-state-machine orchestrator that runs each
document through `STEP_ORDER`, persists state at every transition, retries with
backoff, dead-letters, and supports reprocessing from any step.

**The state machine lives in Postgres, not in Celery.** Celery is only transport.
If Redis is wiped, a sweeper must be able to reconstruct and resume all in-flight work
from `document_steps`.

## Deliverables

### `src/ipa/pipeline/app.py`

Celery application: Redis broker/backend from settings, JSON serializer only,
`task_acks_late=True`, `task_reject_on_worker_lost=True`, `worker_prefetch_multiplier=1`,
per-queue routing (`default`, `ocr`, `llm`, `embed` — so a slow OCR backlog does not
starve extraction), OTel Celery instrumentation, and trace-context propagation via
task headers.

### `src/ipa/pipeline/registry.py`

```python
StepHandler = Callable[[StepContext], Awaitable[StepResult]]

def register(step: PipelineStep) -> Callable[[StepHandler], StepHandler]: ...
def get_handler(step: PipelineStep) -> StepHandler: ...
```

T09–T12 register themselves with `@register(PipelineStep.OCR)` etc. Import their
modules in `src/ipa/pipeline/steps/__init__.py` so registration happens on worker
boot. **T08 creates that `__init__.py` with the imports guarded by
`contextlib.suppress(ImportError)`** so the orchestrator can be tested and merged
before the step tasks land.

The `store` and `review` steps have built-in no-op-ish handlers owned by T08:
`store` is marked succeeded at ingest time; `review` computes the review decision by
calling into T13 (import lazily, tolerate absence with a default of "needs review").

### `src/ipa/pipeline/runner.py`

One generic Celery task drives every step:

```python
@celery_app.task(bind=True, name="ipa.run_step", max_retries=None)
def run_step(self, document_id: str, step: str, attempt_hint: int = 0) -> None: ...
```

Body:

1. Acquire a Redis lock on `(document_id, step)` — belt and braces against duplicate
   delivery.
2. `StepRepository.claim(document_id, step)` — atomic `pending → running` with
   attempt increment. If the claim fails (already running or succeeded), log and
   return. **This is the idempotency guarantee: a redelivered message is a no-op.**
3. Write a `step_started` event.
4. Run the handler inside a fresh async event loop with a per-step timeout
   (`IPA_STEP_TIMEOUT_S`, per-step overridable).
5. On success: `mark_succeeded` with duration and metrics, write `step_succeeded`,
   recompute `documents.status`/`current_step`, and enqueue the next step in
   `STEP_ORDER` (honouring `StepResult.next_step_override`).
6. On `RetryableProviderError` or timeout: if `attempt < max_attempts`, `mark_failed`
   *softly* by resetting to `pending` and re-enqueueing with exponential backoff plus
   jitter (`IPA_STEP_RETRY_BACKOFF_S * 2**attempt`, capped at 15 min). Otherwise fall
   through to hard failure.
7. On `QuarantineError`: set the document to `quarantined`, mark the step `failed`,
   **do not retry**, emit `step_failed` with the quarantine code.
8. On any other exception: `mark_failed`, set the document `failed` with
   `error_code`/`error_detail`, write `step_failed`, and publish to the DLQ.
9. Always: release the lock, flush telemetry, and clear log context.

Every branch must leave `document_steps` in a terminal-or-pending state. There is no
path that leaves a step stuck in `running` except a hard worker kill — which is what
the sweeper handles.

### `src/ipa/pipeline/orchestrator.py`

```python
async def enqueue_from(document_id: UUID, step: PipelineStep) -> None
async def reprocess(document_id: UUID, from_step: PipelineStep, *, actor: str | None) -> None
async def cancel(document_id: UUID) -> None
async def next_step(current: PipelineStep) -> PipelineStep | None
async def recompute_status(document_id: UUID) -> DocumentStatus
```

`reprocess(from_step)`:

1. Lock the document.
2. `StepRepository.reset_from(document_id, from_step)` → that step and every later
   step become `pending`, attempts reset to 0, errors cleared.
3. Clean up derived artefacts for the reset steps so the rerun is truly fresh:
   - from `decompose` → delete page images + thumbnails + `document_pages` rows
   - from `ocr` → delete Mongo `pages` for the document
   - from `extract` → do **not** delete prior extraction versions (append-only); just
     clear `is_current`
   - from `embed` → delete `chunks` for the document
4. Clear the Redis extraction/status cache entries.
5. Write a `reprocess_requested` event with the actor and `from_step`.
6. `enqueue_from(document_id, from_step)`.

`reprocess` from `store` is rejected — the blob is immutable and content-addressed;
re-uploading is the correct action. Return 409 with `code=cannot_reprocess_store`.

### `src/ipa/pipeline/sweeper.py`

A Celery beat task (`every 60s`) that:

- Finds `document_steps` in `running` with `started_at` older than
  `IPA_STEP_STUCK_AFTER_S` (default 30 min) and resets them to `pending` if attempts
  remain, else fails them. This is the crash-recovery path.
- Re-enqueues `pending` steps whose document has no queued task (orphan recovery
  after a Redis flush).
- Emits gauge metrics: queue depth per step, documents by status, oldest pending age.

### `src/ipa/pipeline/dlq.py`

Failed-permanently records go to a `dead_letters` Redis stream **and** are visible via
`document_steps.status = 'failed'`. Provide `list_dead_letters()` and
`replay(document_id)`.

### `src/ipa/api/routers/pipeline.py`

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/v1/documents/{id}/reprocess` | Body `{ "from_step": "ocr" }`; `202` |
| `POST` | `/v1/documents/{id}/cancel` | Best-effort cancel of pending steps |
| `POST` | `/v1/documents/{id}/steps/{step}/retry` | Retry a single failed step in place |
| `GET` | `/v1/pipeline/status` | Counts by status and by step, oldest pending age |
| `GET` | `/v1/pipeline/dead-letters` | Paginated |
| `POST` | `/v1/pipeline/dead-letters/{id}/replay` | |

### Metrics (OTel)

Counters: `ipa.step.started`, `ipa.step.succeeded`, `ipa.step.failed`,
`ipa.step.retried`, `ipa.document.quarantined`.
Histograms: `ipa.step.duration_ms` (attribute `step`).
Gauges (from the sweeper): `ipa.queue.depth`, `ipa.documents.by_status`.

## Acceptance criteria

- Fake handlers registered for all steps; a document runs end to end and every step
  lands `succeeded` in `STEP_ORDER`.
- Duplicate task delivery for the same `(document, step)` executes the handler once.
- A handler raising `RetryableProviderError` retries up to `max_attempts` then fails
  hard; backoff intervals grow.
- A handler raising `QuarantineError` fails immediately with zero retries and sets
  the document `quarantined`.
- `reprocess(from_step=OCR)` resets ocr/extract/embed/review to `pending`, leaves
  store/decompose `succeeded`, deletes Mongo pages and chunks, and writes an event.
- `reprocess(from_step=STORE)` returns 409.
- Sweeper resets a step artificially stuck in `running` past the threshold.
- Killing the worker mid-step leaves the step recoverable by the sweeper (simulated by
  writing a `running` row with an old `started_at`).
