# T12 — Pipeline step: chunk + embed into pgvector

- **Wave:** 3 (parallel with T09, T10, T11)
- **Depends on:** T04 (embedding provider), T05 (chunking), T02 (`db/vector.py`),
  T08, T03
- **Owns:** `src/ipa/pipeline/steps/embed.py`, `tests/unit/pipeline/test_embed.py`

## Goal

Make the document retrievable: chunk its text, embed the chunks, and write them to the
`chunks` table with enough metadata that RAG answers can cite an exact page.

## Behaviour

1. Load `PageText[]` from `ContentStore.get_pages(document_id)`. If empty, mark the
   step `SKIPPED` — a document with no text is not a retrieval failure.
2. Chunk with `processing.chunking.chunk_pages` using `IPA_CHUNK_TOKENS` and
   `IPA_CHUNK_OVERLAP`.
3. **Prepend a context header to each chunk's embedded text** — the title, tag name,
   and page range:

   ```text
   [Invoice — ACME-2024-001.pdf, pages 2-3]
   <chunk text>
   ```

   Store the *bare* chunk text in `chunks.text` for display, but embed the
   header-prefixed version. Small change, large retrieval win: a chunk that says
   "Total: 4,200.00" is meaningless without knowing which invoice it belongs to.

4. Also emit **one summary chunk per document** built from the extracted fields
   (`"invoice_number: ACME-2024-001 | vendor: ACME Ltd | total: 4200.00"`) with
   `chunk_index = -1` and `metadata.kind = "extraction_summary"`. Structured queries
   hit this directly instead of hoping the number appears in prose.
5. Embed in batches with `EmbeddingProvider.embed(texts, kind="passage")`.
6. `VectorStore.upsert(chunks)` — upsert on `(document_id, chunk_index, embed_model)`.
7. Write an `embedded` event.

## Metadata on every chunk row

`tag_id`, `tag_slug`, `document_status`, `original_filename`, `title`,
`page_from`, `page_to`, `kind` (`text` | `extraction_summary`), `created_at`,
`embed_model`, `embed_dim`.

Denormalising `tag_id` and `document_status` onto the chunk is deliberate — it lets
search filter without a join, which matters at scale and keeps the MCP query path
simple. Keep them fresh: when a document's status or tag changes, T13/T07 must call
`VectorStore.refresh_metadata(document_id)`. Provide that method here.

## Idempotency and model migration

- Re-running deletes existing chunks **for the current `embed_model` only**, then
  reinserts. Chunks from a previous embedding model are left alone so the two can
  coexist during a migration.
- If `NVIDIA_EMBED_DIM` no longer matches the column dimension, fail fast at startup
  with a clear error rather than silently writing wrong-sized vectors.
- Provide a management command `python -m ipa.pipeline.reindex --model X` that
  re-embeds all documents into a new model and, on success, drops the old model's
  rows. Wire it into the Makefile as `make reindex`.

`StepResult.metrics`: `chunks`, `tokens_embedded`, `batches`, `provider_ms`,
`skipped_empty`.

## Acceptance criteria

- Ten pages of text → chunks created with correct `page_from`/`page_to`, overlap
  present, none exceeding 2× target tokens.
- Extraction summary chunk exists at `chunk_index = -1` when an extraction is present,
  and is absent when there is none.
- Embedded text includes the context header; `chunks.text` does not.
- Re-running the step twice yields the same chunk count (upsert, not duplicate).
- Chunks written by a different `embed_model` survive a re-run of the current model.
- Document with zero pages → step `SKIPPED`, no rows, no provider call.
- Batching: 100 chunks with batch size 32 → 4 provider calls.
- Dimension mismatch between provider and column raises at startup, not at write time.
- `refresh_metadata` updates `document_status` on all chunks for a document.
