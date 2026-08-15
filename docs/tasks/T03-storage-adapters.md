# T03 — Storage adapters: MinIO, MongoDB, Redis

- **Wave:** 1 (parallel with T02, T04, T05)
- **Depends on:** T01
- **Owns:** `src/ipa/storage/`

## Goal

Concrete implementations of `BlobStore`, `ContentStore`, and `CacheStore` from
`src/ipa/contracts/protocols.py`. No domain logic — these are dumb, well-tested
adapters.

## Deliverables

### `src/ipa/storage/blob.py` — MinIO / S3

`MinioBlobStore(BlobStore)`.

- Client built from `S3Settings`; run in a thread executor (the `minio` SDK is sync)
  or use `aioboto3` — pick one and document it.
- `ensure_bucket()` called once at startup; idempotent.
- `put(key, data, content_type)` returns the key. **Never overwrite an existing key
  whose content differs** — for content-addressed keys, if `exists(key)` is true,
  skip the write and return (this is what makes re-upload cheap).
- `presigned_url(key, expires_s=900)` for browser downloads. Must return a URL
  reachable from the browser, not the docker-internal hostname — support an optional
  `IPA_S3_PUBLIC_ENDPOINT` override for this.
- `put_stream` / `get_stream` for large files so we never hold a 200 MB PDF fully in
  memory more than once.
- Key helpers in `src/ipa/storage/keys.py`:
  `original_key(sha256)`, `page_key(document_id, page)`, `thumb_key(document_id, page)`.

### `src/ipa/storage/content.py` — MongoDB

`MongoContentStore(ContentStore)` using `motor`.

Collections:

| Collection | Document shape | Indexes |
| --- | --- | --- |
| `pages` | `{_id, document_id, page, text, source, confidence, char_count, created_at}` | unique `(document_id, page)` |
| `extractions` | `{_id, document_id, version, tag_id, tag_version, source, model, prompt_hash, fields[], document_confidence, created_at, created_by}` | unique `(document_id, version)`, index `document_id` |
| `raw_responses` | `{_id, document_id, step, model, request_summary, response_text, created_at}` | index `(document_id, step)`, TTL 30 days |

`raw_responses` stores the verbatim model output for debugging — it is the only
place a malformed response survives, and it is TTL'd so it does not grow forever.

Methods per the protocol, plus `put_raw_response(...)`. `put_pages` must be an
idempotent bulk upsert keyed on `(document_id, page)`.

`ensure_indexes()` called at startup.

### `src/ipa/storage/cache.py` — Redis

`RedisCacheStore(CacheStore)` using `redis.asyncio`.

- `get_json` / `set_json` / `delete` with a namespaced key builder
  (`ipa:{env}:{kind}:{id}`).
- `claim_idempotency(key, ttl_s)` → `SET key NX EX ttl`, returns whether we won.
- `lock(key, ttl_s)` → async context manager over a Redis lock with token-checked
  release (never release someone else's lock).
- Cache key helpers in `src/ipa/storage/cache_keys.py`:
  `extraction(document_id, version)`, `tag_schema(tag_id, version)`,
  `search(query_hash)`, `document_status(document_id)`.
- A `cached_json(key_fn, ttl)` decorator for service methods.
- **Graceful degradation:** a Redis outage must not fail a request. `get_json`
  returns `None` and logs a warning on connection error; `set_json` swallows.
  Idempotency and locks do *not* degrade — those must raise.

### `src/ipa/storage/factory.py`

`get_blob_store()`, `get_content_store()`, `get_cache_store()` — cached singletons
plus FastAPI dependencies. Celery tasks use the same functions.

### Health checks

`src/ipa/storage/health.py` — `check_blob()`, `check_content()`, `check_cache()`
returning `(ok: bool, detail: str)`. T01's `/readyz` will import these; add them to
`ROUTERS`-free helper module so there is no circular import.

## Acceptance criteria

- Unit tests with `fakeredis` and `mongomock-motor`; MinIO tested against the compose
  service (mark `integration_light`).
- Round-trip tests for each adapter.
- `put` of an already-present content-addressed key performs no network write
  (assert with a spy).
- Redis outage simulation: `get_json`/`set_json` degrade quietly, `claim_idempotency`
  raises.
- Lock test: two concurrent `lock()` calls, only one enters.
- Presigned URL uses the public endpoint override when set.
