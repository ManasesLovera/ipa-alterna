"""Structured logging.

`structlog` emits one JSON object per line. Every record carries the active
OpenTelemetry `trace_id`/`span_id` when there is one, plus any `document_id` and
`step` bound for the current task or request, so a whole document journey can be
reconstructed from logs alone.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from uuid import UUID

import structlog
from opentelemetry import trace

_configured = False


def _add_otel_context(
    logger: Any, method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Bind the active OTel trace and span IDs onto the log record.

    Args:
        logger: The wrapped logger (unused).
        method_name: The log method name (unused).
        event_dict: The event dictionary being processed.

    Returns:
        The event dictionary, with `trace_id`/`span_id` added when a span is active.
    """
    span = trace.get_current_span()
    context = span.get_span_context()
    if context.is_valid:
        event_dict["trace_id"] = format(context.trace_id, "032x")
        event_dict["span_id"] = format(context.span_id, "016x")
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog and the stdlib logging bridge.

    Safe to call more than once; only the first call takes effect.

    Args:
        level: Minimum level name, e.g. "INFO" or "DEBUG".
        json_output: Emit JSON when True, human-readable console output otherwise.

    Returns:
        None.
    """
    global _configured
    if _configured:
        return

    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_otel_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Logger name, conventionally `__name__`.

    Returns:
        A bound logger ready for keyword-style structured logging.
    """
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def bind_document_context(
    *, document_id: UUID | str | None = None, step: str | None = None, **extra: Any
) -> None:
    """Bind document-scoped fields onto every subsequent log record.

    Args:
        document_id: The document being processed.
        step: The pipeline step currently executing.
        **extra: Any additional fields to bind.

    Returns:
        None.
    """
    bindings: dict[str, Any] = dict(extra)
    if document_id is not None:
        bindings["document_id"] = str(document_id)
    if step is not None:
        bindings["step"] = step
    structlog.contextvars.bind_contextvars(**bindings)


def clear_context() -> None:
    """Clear all context-local log bindings.

    Returns:
        None.
    """
    structlog.contextvars.clear_contextvars()
