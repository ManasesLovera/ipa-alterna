"""MCP server: tool registration and allow-write gating."""

from __future__ import annotations

import asyncio

from ipa.mcp.server import build_mcp_app

READ_TOOLS = {
    "search_documents",
    "find_documents_by_field",
    "get_document",
    "get_extraction",
    "get_document_text",
    "list_tags",
    "get_page_image",
}

WRITE_TOOLS = {"assign_tag", "request_reprocess"}


def _names() -> list[str]:
    async def run() -> list[str]:
        app = build_mcp_app()
        return [tool.name for tool in await app.list_tools()]

    return asyncio.run(run())


def test_all_seven_read_tools_present() -> None:
    names = set(_names())

    assert READ_TOOLS.issubset(names)


def test_write_tools_absent_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("IPA_MCP_ALLOW_WRITE", "false")
    from ipa.core.config import get_settings

    get_settings.cache_clear()
    try:
        names = set(_names())
        assert not names.intersection(WRITE_TOOLS)
    finally:
        get_settings.cache_clear()


def test_write_tools_present_when_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("IPA_MCP_ALLOW_WRITE", "true")
    from ipa.core.config import get_settings

    get_settings.cache_clear()
    try:
        names = set(_names())
        assert WRITE_TOOLS.issubset(names)
    finally:
        get_settings.cache_clear()


def test_descriptions_are_non_empty_and_prescriptive() -> None:
    async def run() -> None:
        app = build_mcp_app()
        tools = await app.list_tools()
        for tool in tools:
            assert tool.description and len(tool.description) > 40

    asyncio.run(run())
