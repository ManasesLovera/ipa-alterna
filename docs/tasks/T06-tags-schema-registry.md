# T06 — Tags + schema registry service and API

- **Wave:** 2 (parallel with T07, T08)
- **Depends on:** T01, T02, T05 (`processing/schema.py`), T03 (cache)
- **Owns:** `src/ipa/domain/tags.py`, `src/ipa/api/routers/tags.py`,
  `src/ipa/api/schemas/tags.py`

## Goal

Let a user create a **tag** (document type) and define its **fields**, which together
produce the JSON Schema used for extraction and the rules used for validation. This
is the feature that makes the platform configurable without code changes.

## Domain rules

1. A tag owns an ordered list of typed fields. `slug` is immutable after creation.
2. **Changing fields bumps `tags.version`** and writes a `tag_versions` snapshot
   containing the full tag, its fields, and the compiled JSON Schema. Old extractions
   keep pointing at their original version and stay interpretable forever.
   - Version-bumping changes: add/remove/rename a field, change `field_type`,
     `is_required`, `enum_values`, or any constraint.
   - Non-bumping changes: `label`, `description`, `position`, `prompt_template`,
     `auto_approve_threshold`, `llm_model`, `is_active`.
3. `auto_approve_threshold` is **nullable and defaults to NULL = every document of
   this tag requires human review.** This is the project default and must be the
   behaviour of a freshly created tag. Setting it to a value in `[0, 1]` enables
   auto-approval when document confidence ≥ threshold and validation passes.
4. A tag with documents attached cannot be hard-deleted — only deactivated
   (`is_active = false`). Deactivated tags are hidden from pickers but existing
   documents keep working.
5. `key` must be a valid snake_case identifier and unique within the tag; it is the
   JSON property name.

## Deliverables

### `src/ipa/domain/tags.py` — `TagService`

```python
async def create_tag(spec: TagCreate) -> TagRead
async def update_tag(tag_id: UUID, spec: TagUpdate) -> TagRead
async def get_tag(tag_id: UUID, version: int | None = None) -> TagRead
async def get_tag_by_slug(slug: str) -> TagRead
async def list_tags(*, include_inactive: bool = False, limit, cursor) -> Page[TagRead]
async def deactivate_tag(tag_id: UUID) -> None
async def delete_tag(tag_id: UUID) -> None            # 409 if documents exist

async def add_field(tag_id: UUID, spec: TagFieldCreate) -> TagFieldRead
async def update_field(field_id: UUID, spec: TagFieldUpdate) -> TagFieldRead
async def delete_field(field_id: UUID) -> None
async def reorder_fields(tag_id: UUID, ordered_ids: list[UUID]) -> list[TagFieldRead]
async def replace_fields(tag_id: UUID, specs: list[TagFieldCreate]) -> TagRead

async def compiled_schema(tag_id: UUID, version: int | None = None) -> CompiledSchema
async def preview_schema(specs: list[TagFieldCreate]) -> dict   # no persistence
async def list_versions(tag_id: UUID) -> list[TagVersionRead]
```

`CompiledSchema` carries `json_schema`, `schema_hash`, `prompt`, `tag_version`.

- `compiled_schema` is the hot path — cache it in Redis under
  `tag_schema(tag_id, version)` with the settings TTL, invalidated on any version bump.
- Prompt assembly: `tags.prompt_template` if set, otherwise a default template
  rendered from the tag name/description and per-field labels + descriptions. Put the
  default template in `src/ipa/domain/prompts.py` (this task creates that file) so
  T11 imports rather than duplicates it.

### `src/ipa/api/routers/tags.py`

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/v1/tags` | Create; body may include `fields[]` for one-shot creation |
| `GET` | `/v1/tags` | List, `?include_inactive=&limit=&cursor=` |
| `GET` | `/v1/tags/{tag_id}` | `?version=` to fetch a historical snapshot |
| `PATCH` | `/v1/tags/{tag_id}` | Partial update |
| `DELETE` | `/v1/tags/{tag_id}` | 409 when documents reference it |
| `POST` | `/v1/tags/{tag_id}/deactivate` | |
| `GET` | `/v1/tags/{tag_id}/versions` | |
| `GET` | `/v1/tags/{tag_id}/schema` | Compiled JSON Schema + prompt + hash |
| `POST` | `/v1/tags/{tag_id}/fields` | Add field |
| `PUT` | `/v1/tags/{tag_id}/fields` | Replace all fields (atomic, one version bump) |
| `PATCH` | `/v1/tags/{tag_id}/fields/{field_id}` | |
| `DELETE` | `/v1/tags/{tag_id}/fields/{field_id}` | |
| `POST` | `/v1/tags/{tag_id}/fields/reorder` | Body: `{ "field_ids": [...] }` |
| `POST` | `/v1/tags/schema/preview` | Compile a candidate field list without saving |

`PUT /fields` matters: the UI's schema editor sends the whole list on save, and we
must not emit one version bump per field.

Register the router by appending to `ROUTERS` in `src/ipa/api/routers/__init__.py`
(append only — never reorder or remove other entries).

### `src/ipa/api/schemas/tags.py`

Pydantic request/response models. `TagFieldCreate` validation:

- `key` matches `^[a-z][a-z0-9_]{0,62}$` and is not a reserved word
  (`fields`, `value`, `confidence`, `evidence`, `page`).
- `enum_values` required and non-empty when `field_type == ENUM`.
- `item_type` required when `field_type == ARRAY`.
- `object_schema` required and a legal JSON Schema when `field_type == OBJECT`.
- `regex` must compile.
- `min_value <= max_value`, `min_length <= max_length`.

### Seed data

`src/ipa/domain/seed_tags.py` — two ready-to-use example tags (`invoice`,
`identity_document`) with sensible fields, installed by `make seed`. Gives the UI and
T18 something real to work with immediately.

## Acceptance criteria

- Creating a tag with no `auto_approve_threshold` yields `null`, and a document with
  that tag lands in `pending_review` (verified in T13/T18, asserted here at the
  service level via the returned tag).
- Adding a field bumps version 1 → 2 and writes a `tag_versions` snapshot.
- Changing only `label` does **not** bump the version.
- `PUT /fields` with five changes bumps the version exactly once.
- `GET /tags/{id}?version=1` returns the original field set after a bump.
- `GET /tags/{id}/schema` output passes `Draft202012Validator.check_schema` and has
  `additionalProperties: false` at every object level.
- Cache invalidation: schema fetched, field added, schema re-fetched → new hash.
- Deleting a tag referenced by a document returns 409 with `code=tag_in_use`.
- Reserved-word key and duplicate key both return 422 with a useful message.
