"""MCP server module entrypoint for the HTTP transport.

Run with `python -m ipa.mcp` to serve over Streamable HTTP on the configured
port.
"""

from __future__ import annotations

from ipa.mcp.server import run_http

if __name__ == "__main__":
    run_http()
