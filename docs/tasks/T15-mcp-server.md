# T15 — MCP server for agent access

- **Wave:** 4 (parallel with T13, T14, T16)
- **Depends on:** T14 (`domain/search.py`), T06, T07, T02
- **Owns:** `src/ipa/mcp/`

## Goal

Expose the document corpus to external AI agents over the Model Context Protocol, so
Claude Code, Claude Desktop, or any MCP-capable agent can search, read, and cite
documents without touching the REST API.

## Transport and deployment

- Use the official Python `mcp` SDK (`FastMCP`).
- Serve over **Streamable HTTP** at `/mcp`, mounted on its own ASGI app and run as a
  separate compose service (`mcp`) on its own port. Do not mount it inside the main
  FastAPI app — different lifecycle, different auth surface, and you want to be able
  to scale or disable it independently.
- Also provide a stdio entrypoint (`python -m ipa.mcp.stdio`) for local desktop
  clients.

## Tools

Descriptions must be **prescriptive about when to call**, not just what the tool does.
That is what drives correct triggering. Write 3–5 sentences each.

| Tool | Signature | Purpose |
| --- | --- | --- |
| `search_documents` | `(query: str, top_k: int = 8, tag_slug: str \| None = None, status: str \| None = None) -> list[Hit]` | Semantic + keyword search over document content. Call when the user asks about information that might be in stored documents. |
| `find_documents_by_field` | `(tag_slug: str, where: dict) -> list[DocumentSummary]` | Structured query over extracted fields. Call for filters like "invoices over 1000 from ACME" — do not use `search_documents` for numeric or exact-value filtering. |
| `get_document` | `(document_id: str) -> DocumentDetail` | Metadata, status, tag, page count, confidence. Call after a search to get context before reading content. |
| `get_extraction` | `(document_id: str, version: int \| None = None) -> Extraction` | The structured JSON extracted from a document, with per-field confidence. Prefer this over reading raw text when the answer is a known field. |
| `get_document_text` | `(document_id: str, page_from: int = 1, page_to: int \| None = None) -> str` | Raw OCR text for a page range. Call only when the extraction does not contain the answer. Bounded to 20 pages per call. |
| `list_tags` | `() -> list[TagSummary]` | Available document types and their fields. Call first when you do not know what kinds of documents exist. |
| `get_page_image` | `(document_id: str, page: int) -> ImageContent` | Page image for visual inspection. Call when the text is ambiguous or the layout matters. |

## Resources

- `ipa://documents/{document_id}` — document detail as JSON.
- `ipa://documents/{document_id}/extraction` — current extraction.
- `ipa://tags` — the tag catalogue.

## Prompts

- `summarise_document(document_id)` — canned prompt that fetches extraction + text.
- `compare_documents(ids)` — side-by-side field comparison.

## Hard rules

1. **Every result carries provenance.** `document_id`, `page`, and a title.
   Never return bare text an agent cannot attribute.
2. **Bound every payload.** `search_documents` returns snippets, not documents;
   `get_document_text` caps at 20 pages and truncates with an explicit
   `"...[truncated, N pages remaining, call again with page_from=X]"` marker. Dumping
   whole documents into an agent's context is how this server becomes useless.
3. **Reuse the service layer.** Import `SearchService`, `DocumentService`, and
   `TagService` directly. Zero query logic in `src/ipa/mcp/` — it is an adapter.
4. **Same authorisation as REST.** Even single-tenant, the MCP server authenticates
   (bearer token mapped to an `api_keys` row via T16's verifier) and respects scopes.
   Do not build a second, more permissive access path — that is how "internal tool"
   becomes "data leak".
5. **Read-only by default.** No tool mutates state unless
   `IPA_MCP_ALLOW_WRITE=true`, which additionally enables `assign_tag` and
   `request_reprocess`. Default false.
6. **Default status filter** of `validated`/`completed` — agents should not cite
   extractions no human has checked. Overridable per call with an explicit `status`
   argument so the behaviour is chosen, not accidental.

## Deliverables

- `src/ipa/mcp/server.py` — FastMCP app, tool registration.
- `src/ipa/mcp/tools.py` — tool implementations.
- `src/ipa/mcp/schemas.py` — Pydantic models for tool inputs/outputs.
- `src/ipa/mcp/auth.py` — bearer verification against `api_keys`.
- `src/ipa/mcp/stdio.py` — stdio entrypoint.
- `docs/mcp.md` — connection instructions for Claude Code
  (`claude mcp add --transport http ipa http://localhost:8090/mcp`), Claude Desktop
  config JSON, and the tool catalogue.
- Compose service `mcp` + the env vars `IPA_MCP_PORT`, `IPA_MCP_ALLOW_WRITE`.

## Acceptance criteria

- `mcp` service starts and responds to an MCP `initialize` handshake.
- `tools/list` returns all seven tools with non-empty, prescriptive descriptions.
- `search_documents` returns hits carrying `document_id` and `page`.
- `get_document_text` on a 60-page document returns ≤ 20 pages plus a truncation
  marker naming the next `page_from`.
- Unauthenticated request rejected; a valid `api_keys` bearer accepted.
- With `IPA_MCP_ALLOW_WRITE=false`, mutating tools are absent from `tools/list`.
- Default search excludes `pending_review` documents; passing `status="pending_review"`
  includes them.
- Tool implementations contain no SQL and no direct store access — assert by import
  inspection in the test.
