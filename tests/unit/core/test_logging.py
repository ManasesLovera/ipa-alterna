"""Log records carry trace context and document-scoped bindings."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from ipa.core.logging import _add_otel_context, bind_document_context, clear_context, get_logger


def test_no_trace_ids_without_an_active_span() -> None:
    event: dict[str, Any] = {"event": "test"}

    assert _add_otel_context(None, "info", event) == {"event": "test"}


def test_trace_and_span_ids_are_bound_from_the_active_span() -> None:
    tracer = TracerProvider().get_tracer(__name__)

    with tracer.start_as_current_span("unit") as span:
        enriched = _add_otel_context(None, "info", {"event": "test"})
        context = span.get_span_context()

    assert enriched["trace_id"] == format(context.trace_id, "032x")
    assert enriched["span_id"] == format(context.span_id, "016x")
    assert len(enriched["trace_id"]) == 32


def test_document_context_is_bound_and_cleared() -> None:
    document_id = uuid4()
    try:
        bind_document_context(document_id=document_id, step="ocr", attempt=2)
        bound = structlog.contextvars.get_contextvars()

        assert bound["document_id"] == str(document_id)
        assert bound["step"] == "ocr"
        assert bound["attempt"] == 2
    finally:
        clear_context()

    assert structlog.contextvars.get_contextvars() == {}


def test_get_logger_returns_a_bound_logger() -> None:
    logger = get_logger(__name__)

    assert hasattr(logger, "info")
    assert hasattr(logger.bind(document_id="x"), "warning")


def test_tracer_provider_is_available_to_the_api() -> None:
    assert trace.get_tracer("test") is not None
