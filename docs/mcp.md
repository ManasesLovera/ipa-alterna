# MCP Server — Agent Access to IPA

The IPA MCP server exposes the document corpus to external AI agents over the
Model Context Protocol. Agents can search, read, and cite documents without
touching the REST API.

## Connect from Claude Code

```bash
claude mcp add --transport http ipa http://localhost:8090/mcp
```

## Connect from Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "ipa": {
      "command": "uv",
      "args": ["run", "python", "-m", "ipa.mcp.stdio"],
      "env": { "IPA_MCP_ALLOW_WRITE": "false" }
    }
  }
}
```

## Run the server

```bash
# HTTP transport (Streamable HTTP on IPA_MCP_PORT)
python -m ipa.mcp

# stdio transport for desktop clients
python -m ipa.mcp.stdio
```

## Tools

| Tool | Purpose |
| --- | --- |
| `search_documents` | Semantic + keyword search over document content |
| `find_documents_by_field` | Structured query over extracted fields |
| `get_document` | Document metadata, status, tag, confidence |
| `get_extraction` | Structured JSON extracted from a document |
| `get_document_text` | Raw OCR text for a page range (max 20 pages) |
| `list_tags` | Available document types and their fields |
| `get_page_image` | Presigned URL for a page image |

When `IPA_MCP_ALLOW_WRITE=true`, `assign_tag` and `request_reprocess` are also
exposed. The default is read-only.

By default search only surfaces `validated` and `completed` documents — agents
should not cite extractions no human has checked.

## Configuration

- `IPA_MCP_PORT` (default `8090`): the HTTP transport port.
- `IPA_MCP_ALLOW_WRITE` (default `false`): enable mutating tools.

## Environment

The MCP server reads the same `.env` as the API, so the embedding model ID
(`NVIDIA_EMBED_MODEL`) and the storage endpoints must be configured for search
and read tools to work.
