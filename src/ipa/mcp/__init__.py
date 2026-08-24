"""MCP server exposing the document corpus to external AI agents.

A thin adapter over the service layer (`SearchService`, `DocumentService`,
`TagService`): zero query logic lives here. Serves over Streamable HTTP on its
own port, plus a stdio entrypoint for local desktop clients.
"""

from __future__ import annotations
