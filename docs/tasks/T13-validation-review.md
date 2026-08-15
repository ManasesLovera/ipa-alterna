# T13 — Validation rules + human review service and API

- **Wave:** 4 (parallel with T14, T15, T16)
- **Depends on:** T11 (`domain/extraction.py`), T06, T08, T02, T03
- **Owns:** `src/ipa/domain/validation.py`, `src/ipa/api/routers/review.py`,
  `src/ipa/api/schemas/review.py`

## Goal

Decide whether a document can auto-approve or must be checked by a human, and give
reviewers an API to inspect, correct, approve, or reject extractions.

**Default posture: every document requires human validation.** Auto-approval is an
opt-in per tag, configured as `tags.auto_approve_threshold`.

## The review decision

Implemented as the `review` step handler registered with T08's registry
(`@register(PipelineStep.REVIEW)`), calling into `ValidationService.decide`.

```text
threshold = tag.auto_approve_threshold

if threshold is None:                      -> pending_review   (project default)
elif any required field missing/invalid:   -> pending_review
elif any field has valid == False:         -> pending_review
elif document_confidence < threshold:      -> pending_review
elif any field.confidence < threshold:     -> pending_review   (weakest-link rule)
else:                                      -> validated (auto)
```

The weakest-link rule matters: a document averaging 0.95 that contains one field at
0.3 is not safe to auto-approve. Both the aggregate **and** the minimum must clear
the bar.

Auto-approval writes a `validations` row with `auto = true`, `decision = 'approved'`,
`reviewer_id = NULL`, and a `validated` event. It is fully auditable — you can always
answer "who approved this" with "the rule, at this threshold, on this date".

After the decision:

- `pending_review` → `documents.status = 'pending_review'`, `needs_review = true`.
- `validated` → `status = 'validated'`, `needs_review = false`, then immediately
  `completed` with `completed_at` set (there is nothing after review).

Call `VectorStore.refresh_metadata(document_id)` after any status change so search
filters stay accurate.

## Human corrections

A reviewer edits field values and submits. This creates a **new extraction version**
with `source = HUMAN` — never an in-place update.

- Corrected fields get `confidence = 1.0` and `valid = true` (a human said so).
- Uncorrected fields carry over from the previous version unchanged.
- The new version becomes `is_current`; the previous one stays readable.
- A `validations` row records `decision = 'corrected'`, `reviewer_id`,
  `corrected_fields` (a JSON diff of key → `{from, to}`), and optional notes.
- Emit one `field_corrected` event per changed field so the audit log is granular.
- Re-run the validation rules on the submitted values and reject the submission with
  422 if a correction itself violates the schema — reviewers make typos too.

Corrections are also the raw material for future accuracy work. We are **not**
building accuracy evaluation in this project, but capture the data cleanly so it is
available later.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/v1/review/queue` | Paginated `pending_review` documents. Filters: `tag_id`, `min_confidence`, `max_confidence`, `has_invalid_fields`, `older_than`. Sort: `confidence asc` (worst first, the useful default), `created_at asc/desc` |
| `GET` | `/v1/review/queue/stats` | Counts by tag, by confidence band, oldest waiting item |
| `GET` | `/v1/review/{document_id}` | Review payload: current extraction with per-field value, confidence, evidence, page, validation errors, plus page image URLs and the tag schema for rendering the form |
| `POST` | `/v1/review/{document_id}/approve` | Approve as-is. Body: `{ "notes": "..." }` |
| `POST` | `/v1/review/{document_id}/correct` | Body: `{ "fields": { "key": value, ... }, "notes": "..." }`. Creates a new HUMAN version and approves |
| `POST` | `/v1/review/{document_id}/reject` | Body: `{ "reason": "...", "action": "quarantine" \| "reprocess" }`. `reprocess` calls T08 from `extract` |
| `POST` | `/v1/review/{document_id}/assign-tag` | Body: `{ "tag_id": "..." }` for documents parked with `needs_tag`; triggers reprocess from `extract` |
| `POST` | `/v1/review/bulk/approve` | Body: `{ "document_ids": [...] }`, capped at 100 per call |
| `GET` | `/v1/documents/{id}/validations` | Validation history |

`GET /v1/review/{document_id}` should return everything the review screen needs in one
round trip — extraction, schema, page images, and neighbouring queue IDs for
prev/next navigation. Reviewers work fast; do not make the UI stitch four calls.

## Concurrency

Two reviewers must not both submit corrections for the same document. Take a Redis
lock on the document for the duration of a submit, and include the extraction version
the reviewer was looking at in the request body — if it is no longer `is_current`,
return `409 code=stale_extraction` so the UI can reload. Optimistic concurrency, not
pessimistic checkout.

## Acceptance criteria

- Tag with `auto_approve_threshold = NULL` → every document goes to
  `pending_review`, regardless of confidence. Assert with confidence 0.99.
- Tag with threshold 0.8 and document confidence 0.9, all fields ≥ 0.8 → auto
  `validated` + `completed`, `validations` row with `auto = true`.
- Same tag, document confidence 0.9 but one field at 0.3 → `pending_review`
  (weakest-link).
- Missing required field with confidence 1.0 on everything else → `pending_review`.
- Correction creates version N+1 with `source = HUMAN`, corrected fields at
  confidence 1.0, untouched fields carried over, previous version still readable.
- Correction violating the tag schema → 422, no new version written.
- Stale-version submit → 409 `stale_extraction`.
- Reject with `action = reprocess` resets steps from `extract` and re-enqueues.
- Bulk approve of 101 IDs → 422.
- Queue sorted `confidence asc` returns the worst document first.
