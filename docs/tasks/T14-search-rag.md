# T14 — Hybrid search + RAG API

- **Wave:** 4 (parallel with T13, T15, T16)
- **Depends on:** T12 (chunks exist), T02 (`db/vector.py`), T04, T03
- **Owns:** `src/ipa/domain/search.py`, `src/ipa/api/routers/search.py`,
  `src/ipa/api/schemas/search.py`

## Goal

Retrieval over the corpus, and a grounded question-answering endpoint on top of it.
This is also the engine behind the MCP server (T15) — build the service layer so T15
is a thin adapter, not a reimplementation.

## Why hybrid

Pure vector search underperforms badly on this corpus. Documents are full of invoice
numbers, part codes, tax IDs, and proper nouns; "find ACME-2024-001" is a lexical
query, not a semantic one. Run both and fuse.

- **Dense:** embed the query with `kind="query"` (NVIDIA retrieval models are
  asymmetric — using `"passage"` for queries measurably degrades results), cosine KNN
  over `chunks.embedding`.
- **Sparse:** PostgreSQL full-text over `chunks.tsv` with `websearch_to_tsquery`.
- **Fusion:** Reciprocal Rank Fusion, `score = Σ 1 / (k + rank_i)` with `k = 60`.
  RRF needs no score normalisation and is robust when the two systems disagree — which
  is exactly the situation here.

Expose `mode` (`hybrid` | `dense` | `sparse`) with `hybrid` as the default, so the
behaviour is inspectable when results look wrong.

## Endpoints

### `POST /v1/search`

```json
{
  "query": "total amount on the ACME invoice",
  "mode": "hybrid",
  "top_k": 10,
  "filters": { "tag_ids": ["..."], "statuses": ["validated"], "created_after": "..." },
  "group_by_document": true
}
```

Response: hits with `document_id`, `chunk_index`, `text`, `score`, `page_from`,
`page_to`, `document_title`, `tag_slug`, and a presigned `page_image_url` for the
first page of the range.

`group_by_document: true` collapses multiple chunks from one document into a single
result with its best chunks nested — what a UI actually wants to render.

### `POST /v1/search/documents`

Structured search over extracted fields, not text. Body:
`{ "tag_id": "...", "where": { "vendor": "ACME", "total": { "gte": 1000 } }, "limit": 50 }`.
Queries Mongo's `extractions` collection (current versions only) with the operators
`eq`, `neq`, `gte`, `lte`, `contains`, `in`, `exists`. This answers "show me every
invoice over €1000 from ACME" — which vector search will never do reliably.

### `POST /v1/rag/query`

```json
{
  "question": "What was the total on the ACME invoice from March?",
  "top_k": 8,
  "filters": { "tag_ids": ["..."] },
  "include_sources": true
}
```

Pipeline: retrieve → assemble context with explicit source markers → call
`LlmProvider` → return `{ answer, sources[], confidence, used_chunks }`.

**Citations are mandatory.** The system prompt requires the model to cite
`[doc:{document_id} p:{page}]` for every factual claim, and the response parses those
markers into a structured `sources[]` array with document title, page, and a presigned
image URL. An answer with no retrievable sources returns
`{"answer": null, "reason": "no_relevant_documents"}` rather than a confabulated
answer. Do not let this endpoint guess.

Stream the answer via SSE when `Accept: text/event-stream`.

### `GET /v1/search/similar/{document_id}`

Nearest neighbours to a document's summary chunk — "find documents like this one",
useful for duplicate detection beyond exact SHA-256 matching.

## Service layer — `src/ipa/domain/search.py`

```python
class SearchService:
    async def search(self, req: SearchRequest) -> SearchResponse
    async def search_documents(self, req: FieldQuery) -> list[DocumentRead]
    async def rag(self, req: RagRequest) -> RagResponse
    async def similar(self, document_id: UUID, top_k: int) -> list[ChunkHit]
```

Keep it free of FastAPI types — T15 imports this directly.

## Performance

- Cache search results in Redis under `search(hash(query + filters + mode + top_k))`
  with a short TTL (default 300 s). Invalidate nothing on write — a five-minute stale
  window is acceptable for search and the TTL handles it.
- Cap `top_k` at 50 and context at `IPA_RAG_MAX_CONTEXT_TOKENS`; truncate by dropping
  the lowest-ranked chunks, never by cutting a chunk mid-way.
- Always apply a status filter by default (`validated`, `completed`) unless the caller
  explicitly asks for others — surfacing unreviewed extractions to a question-answering
  endpoint is how wrong answers get into reports.

## Acceptance criteria

- Seeded corpus: an exact invoice-number query ranks the correct chunk first in
  `hybrid` and in `sparse`, but not necessarily in `dense` — assert this explicitly,
  it is the whole justification for hybrid.
- A paraphrased semantic query ranks correctly in `hybrid` and `dense`.
- RRF fusion is unit-tested against hand-computed ranks.
- Query embedding is requested with `kind="query"` (spy assertion).
- Filters restrict results by tag and status.
- `group_by_document` returns one entry per document with nested chunks.
- RAG answer contains citation markers and a parsed `sources[]` with valid page
  numbers.
- RAG with no retrieval hits returns `answer: null` and `reason` — never a fabricated
  answer.
- Field search: `{"total": {"gte": 1000}}` returns only matching extractions.
- Repeated identical search hits the Redis cache (assert one vector query, two calls).
- `top_k = 500` clamps to 50.
