"""MCP FastMCP application: tool registration and HTTP/stdio entrypoints.

Serves the document corpus over Streamable HTTP on its own port and over stdio
for desktop clients. Mutating tools are registered only when `IPA_MCP_ALLOW_WRITE`
is set.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ipa.core.config import get_settings
from ipa.mcp import tools


def build_mcp_app() -> FastMCP:
    """Build and register the MCP server.

    Returns:
        A configured `FastMCP` instance.
    """
    settings = get_settings()
    server = FastMCP("ipa", instructions=_INSTRUCTIONS)

    server.add_tool(
        tools.search_documents,
        name="search_documents",
        description=(
            "Search stored documents semantically and by keyword. Call when the user "
            "asks about information that might be in the corpus, such as invoice totals, "
            "vendor names, or policy clauses. Returns attributed snippets, not whole "
            "documents. By default only validated and completed documents are searched; "
            "pass an explicit status to include unreviewed ones."
        ),
    )
    server.add_tool(
        tools.find_documents_by_field,
        name="find_documents_by_field",
        description=(
            "Query documents by exact extracted-field filters, e.g. invoices over 1000 "
            "from ACME. Call for numeric, exact-value or structured filters. Do not use "
            "search_documents for these — it is lexical and semantic, not structured."
        ),
    )
    server.add_tool(
        tools.get_document,
        name="get_document",
        description=(
            "Fetch a document's metadata, status, tag, page count and confidence. Call "
            "after a search to get context about a document before reading its content."
        ),
    )
    server.add_tool(
        tools.get_extraction,
        name="get_extraction",
        description=(
            "Fetch the structured JSON extracted from a document with per-field "
            "confidence. Prefer this over reading raw text when the answer is a known "
            "field, since it is cleaner and cites evidence."
        ),
    )
    server.add_tool(
        tools.get_document_text,
        name="get_document_text",
        description=(
            "Fetch raw OCR text for a page range of a document. Call only when the "
            "extraction does not contain the answer and you need the verbatim text. "
            "Bounded to 20 pages per call."
        ),
    )
    server.add_tool(
        tools.list_tags,
        name="list_tags",
        description=(
            "List the available document types (tags) and their fields. Call first when "
            "you do not know what kinds of documents exist in the corpus."
        ),
    )
    server.add_tool(
        tools.get_page_image,
        name="get_page_image",
        description=(
            "Return a presigned URL for a page image. Call when the text is ambiguous, "
            "the layout matters, or you need to inspect a page visually."
        ),
    )

    if settings.mcp.allow_write:
        server.add_tool(
            tools.assign_tag,
            name="assign_tag",
            description="Assign a tag to a document and request reprocessing from extract.",
        )
        server.add_tool(
            tools.request_reprocess,
            name="request_reprocess",
            description="Request that a document be reprocessed from a given step.",
        )

    return server


_INSTRUCTIONS = (
    "You have access to a corpus of processed documents. Search first, then read "
    "extractions, and only fall back to raw text when the structured data is "
    "insufficient. Cite documents by their id and page. By default search only "
    "surfaces validated and completed documents."
)


def create_streamable_app() -> Any:
    """Return the Streamable HTTP ASGI application for uvicorn.

    Returns:
        The ASGI app.
    """
    server = build_mcp_app()
    return server.streamable_http_app()


def run_http() -> None:
    """Run the MCP server over Streamable HTTP on its configured port.

    Returns:
        None.
    """
    import uvicorn

    app = create_streamable_app()
    uvicorn.run(app, host="0.0.0.0", port=get_settings().mcp.port, log_level="info")


def run_stdio() -> None:
    """Run the MCP server over stdio for desktop clients.

    Returns:
        None.
    """
    import anyio

    async def main() -> None:
        server = build_mcp_app()
        await server.run_stdio_async()

    anyio.run(main)
