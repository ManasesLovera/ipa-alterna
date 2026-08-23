"""Step handler registry."""

from __future__ import annotations

from ipa.core.enums import PipelineStep
from ipa.core.errors import ConfigurationError
from ipa.pipeline.registry import get_handler, register


def test_register_and_get_handler() -> None:
    async def handler(ctx: object) -> object:
        return None

    register(PipelineStep.EXTRACT)(handler)

    assert get_handler(PipelineStep.EXTRACT) is handler


def test_get_handler_unknown_raises() -> None:
    try:
        get_handler(PipelineStep.EMBED)
    except ConfigurationError as exc:
        assert "no handler" in str(exc)
    else:
        raise AssertionError("expected ConfigurationError")
