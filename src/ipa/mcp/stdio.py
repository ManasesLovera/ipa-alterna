"""MCP stdio entrypoint for desktop clients.

Run with `python -m ipa.mcp.stdio`. Serves the same tools as the HTTP server
over standard in/out, so Claude Desktop and similar local clients can connect.
"""

from __future__ import annotations

from ipa.mcp.server import run_stdio

if __name__ == "__main__":
    run_stdio()
