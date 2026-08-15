"""OpenTelemetry bootstrap for the API and the Celery workers.

`setup_telemetry()` is idempotent and safe to call from any entrypoint. When no
OTLP endpoint is configured it installs nothing and returns quietly, so tests and
local runs without a collector behave exactly like production minus the export.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from ipa.core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI

logger = structlog.get_logger(__name__)

_initialised = False


def _instrument_libraries() -> None:
    """Instrument SQLAlchemy, Redis, httpx and Celery when those packages exist.

    Each instrumentation is optional: a missing or already-instrumented library
    is logged and skipped rather than failing the process.

    Returns:
        None.
    """
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    instrumentors: list[Any] = [
        SQLAlchemyInstrumentor(),
        RedisInstrumentor(),
        HTTPXClientInstrumentor(),
        CeleryInstrumentor(),
    ]
    for instrumentor in instrumentors:
        try:
            instrumentor.instrument()
        except Exception as exc:
            logger.warning(
                "otel.instrumentation_failed",
                instrumentor=type(instrumentor).__name__,
                error=str(exc),
            )


def setup_telemetry(service_name: str, app: FastAPI | None = None) -> bool:
    """Configure tracing and metrics, and instrument the supported libraries.

    Args:
        service_name: Value reported as `service.name`, e.g. "ipa-api".
        app: FastAPI application to instrument, when called from the API.

    Returns:
        True when telemetry was configured, False when it was a no-op.
    """
    global _initialised
    settings = get_settings()

    if app is not None:
        _instrument_fastapi(app)

    if _initialised:
        return True

    if not settings.otel.enabled and not settings.otel.console_export:
        logger.info("otel.disabled", reason="OTEL_EXPORTER_OTLP_ENDPOINT is unset")
        return False

    resource = Resource.create(
        {
            "service.name": service_name or settings.otel.service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.env,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    meter_readers: list[PeriodicExportingMetricReader] = []

    if settings.otel.enabled:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        endpoint = settings.otel.exporter_endpoint
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
        meter_readers.append(
            PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint, insecure=True))
        )

    if settings.otel.console_export:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        meter_readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))

    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=meter_readers))

    _instrument_libraries()
    _initialised = True
    logger.info(
        "otel.configured",
        service_name=service_name,
        endpoint=settings.otel.exporter_endpoint or None,
    )
    return True


def _instrument_fastapi(app: FastAPI) -> None:
    """Instrument a FastAPI application, ignoring health endpoints.

    Args:
        app: The application to instrument.

    Returns:
        None.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    try:
        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:
        logger.warning("otel.fastapi_instrumentation_failed", error=str(exc))


def shutdown_telemetry() -> None:
    """Flush and shut down the tracer provider, if one was installed.

    Returns:
        None.
    """
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for a module.

    Args:
        name: Instrumentation scope, conventionally `__name__`.

    Returns:
        An OpenTelemetry tracer.
    """
    return trace.get_tracer(name)
