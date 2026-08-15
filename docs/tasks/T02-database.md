# T02 — PostgreSQL data model + Alembic migrations

- **Wave:** 1 (parallel with T03, T04, T05)
- **Depends on:** T01
- **Owns:** `src/ipa/db/`, `migrations/versions/`

## Goal

Define every PostgreSQL table, the SQLAlchemy 2.0 typed ORM layer, repositories, and
the initial migration — including the `pgvector` extension and index.

PostgreSQL is the **source of truth for state**. No file bytes and no extracted
content bodies live here.

## Tables

### `users`

`id uuid pk`, `email citext unique`, `full_name`, `password_hash`, `role`
(`admin` | `reviewer` | `viewer`), `is_active bool`, `created_at`, `updated_at`.

### `api_keys`

`id uuid pk`, `name`, `key_hash` (sha256 of the raw key), `prefix` (first 8 chars,
for display), `scopes text[]`, `last_used_at`, `expires_at`, `revoked_at`,
`created_at`. Populated by T16; table created here.

### `tags`

The document type + extraction schema.

`id uuid pk`, `slug citext unique`, `name`, `description`,
`version int not null default 1`, `is_active bool default true`,
`auto_approve_threshold numeric(4,3) null` (**null = always human review**),
`prompt_template text null`, `llm_model text null` (override),
`classification_hints text null`, `created_at`, `updated_at`.

Constraint: `auto_approve_threshold IS NULL OR (auto_approve_threshold >= 0 AND auto_approve_threshold <= 1)`.

### `tag_fields`

`id uuid pk`, `tag_id fk tags on delete cascade`, `key` (snake_case identifier),
`label`, `field_type` (`FieldType` enum), `description text`,
`is_required bool default false`, `position int`,
`enum_values text[] null`, `regex text null`,
`min_value numeric null`, `max_value numeric null`,
`min_length int null`, `max_length int null`,
`item_type text null` (for arrays), `object_schema jsonb null` (for objects),
`created_at`, `updated_at`.

Unique `(tag_id, key)`. Unique `(tag_id, position)` deferrable.

### `tag_versions`

Immutable snapshot so old extractions remain interpretable.

`id uuid pk`, `tag_id fk`, `version int`, `snapshot jsonb` (full tag + fields +
generated JSON Schema), `created_at`. Unique `(tag_id, version)`.

### `documents`

`id uuid pk`, `sha256 char(64) not null unique`, `original_filename`,
`mime_type`, `size_bytes bigint`, `blob_key text not null`,
`page_count int null`, `tag_id fk tags null`, `tag_version int null`,
`status DocumentStatus not null default 'received'`,
`current_step PipelineStep null`,
`document_confidence numeric(4,3) null`,
`needs_review bool not null default true`,
`title text null`, `source` (`ui` | `api`), `uploaded_by text null`,
`trace_id text null`, `error_code text null`, `error_detail text null`,
`metadata jsonb not null default '{}'`,
`created_at`, `updated_at`, `completed_at null`, `deleted_at null`.

Indexes: `status`, `tag_id`, `created_at desc`, `needs_review where needs_review`,
GIN on `metadata`.

### `document_steps`

One row per (document, step). This is what makes per-step reprocessing possible.

`id uuid pk`, `document_id fk documents on delete cascade`,
`step PipelineStep`, `status StepStatus not null default 'pending'`,
`attempt int not null default 0`, `max_attempts int not null default 3`,
`started_at null`, `finished_at null`, `duration_ms int null`,
`error_code text null`, `error_detail text null`,
`output_ref text null` (e.g. Mongo id or blob prefix),
`metrics jsonb not null default '{}'`,
`created_at`, `updated_at`.

Unique `(document_id, step)`. Index on `(status, step)`.

### `document_events`

Append-only audit log. Never updated, never deleted.

`id bigserial pk`, `document_id fk`, `step PipelineStep null`,
`event_type text` (e.g. `uploaded`, `step_started`, `step_succeeded`,
`step_failed`, `reprocess_requested`, `validated`, `field_corrected`),
`payload jsonb`, `actor text null`, `trace_id text null`, `created_at`.

Index `(document_id, created_at)`.

### `document_pages`

Lightweight page index (text bodies live in Mongo).

`id uuid pk`, `document_id fk on delete cascade`, `page int`,
`blob_key text` (rendered image), `thumb_key text null`,
`width int`, `height int`, `text_source text null`, `char_count int null`,
`ocr_confidence numeric(4,3) null`, `created_at`.
Unique `(document_id, page)`.

### `extraction_versions`

Pointer table; the payload lives in Mongo.

`id uuid pk`, `document_id fk on delete cascade`, `version int`,
`tag_id fk`, `tag_version int`, `source ExtractionSource`,
`model text null`, `prompt_hash text null`,
`document_confidence numeric(4,3)`, `mongo_id text`,
`is_current bool not null default false`,
`created_by text null`, `created_at`.
Unique `(document_id, version)`. Partial unique index enforcing one
`is_current = true` per document.

### `validations`

`id uuid pk`, `document_id fk`, `extraction_version_id fk`,
`decision` (`approved` | `rejected` | `corrected`),
`reviewer_id fk users null`, `notes text null`,
`corrected_fields jsonb null`, `auto bool not null default false`,
`created_at`.

### `chunks`

pgvector table.

`id uuid pk`, `document_id fk on delete cascade`, `chunk_index int`,
`text text`, `embedding vector(N)` (N from `NVIDIA_EMBED_DIM`),
`embed_model text not null`, `embed_dim int not null`,
`page_from int null`, `page_to int null`, `token_count int null`,
`tsv tsvector generated always as (to_tsvector('simple', text)) stored`,
`metadata jsonb not null default '{}'`, `created_at`.

Unique `(document_id, chunk_index, embed_model)`.
Indexes: HNSW on `embedding` with `vector_cosine_ops`, GIN on `tsv`, btree on
`document_id`.

Because the vector dimension is configurable, generate it in the migration from
settings and record the value in a `schema_config` row — reject startup if the
configured dim no longer matches.

### `webhooks`

`id uuid pk`, `url`, `secret`, `events text[]`, `is_active bool`,
`created_at`, `updated_at`. Table created here, used by T16.

### `webhook_deliveries`

`id bigserial pk`, `webhook_id fk`, `event_type`, `payload jsonb`,
`status` (`pending`|`delivered`|`failed`), `attempt int`,
`response_status int null`, `error text null`, `created_at`, `delivered_at null`.

### `idempotency_keys`

Durable backstop for the Redis claim.

`id uuid pk`, `key text unique`, `endpoint text`, `request_hash text`,
`response_status int null`, `response_body jsonb null`,
`created_at`, `expires_at`.

## Deliverables

- `src/ipa/db/base.py` — `Base(DeclarativeBase)` with `metadata` naming convention
  for constraints (so Alembic autogenerate is stable).
- `src/ipa/db/models/*.py` — one module per aggregate (`user.py`, `tag.py`,
  `document.py`, `extraction.py`, `chunk.py`, `webhook.py`, `system.py`).
- `src/ipa/db/session.py` — async engine, `async_sessionmaker`, `get_session()`
  FastAPI dependency, and a `session_scope()` context manager for Celery tasks.
- `src/ipa/db/repositories/*.py` — thin repositories returning **DTOs, not ORM
  objects**: `DocumentRepository`, `TagRepository`, `StepRepository`,
  `EventRepository`, `ExtractionRepository`, `ChunkRepository`, `UserRepository`,
  `WebhookRepository`.
  - `StepRepository` must provide `ensure_steps(document_id)`,
    `claim(document_id, step)` (atomic `pending → running` with attempt increment,
    using `UPDATE ... WHERE status='pending' RETURNING`), `mark_succeeded`,
    `mark_failed`, `reset_from(document_id, step)`.
- `migrations/versions/0001_initial.py` — creates `CREATE EXTENSION IF NOT EXISTS
  vector` and `citext`, then all tables and indexes.
- `src/ipa/db/vector.py` — the `VectorStore` protocol implementation over `chunks`
  (upsert, delete_document, `search` doing cosine KNN plus optional full-text, fused
  with Reciprocal Rank Fusion). T14 consumes this; do not put query-parsing logic here.

## Acceptance criteria

- `make migrate` applies cleanly against a fresh `pgvector/pgvector:pg16`.
- Downgrade to base works.
- `alembic check` reports no pending autogenerate diff after upgrade.
- Unit tests (against a real Postgres via compose, marked `integration_light`):
  - `ensure_steps` creates exactly six rows in `STEP_ORDER`.
  - `claim` is atomic — two concurrent claims, only one wins.
  - `reset_from(OCR)` sets ocr/extract/embed/review to `pending` and leaves
    store/decompose untouched.
  - Partial unique index rejects a second `is_current` extraction.
  - Vector round-trip: upsert 3 chunks, cosine search returns them ranked.
