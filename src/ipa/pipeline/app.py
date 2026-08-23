"""Celery application and task configuration.

Redis is both broker and backend. Task behaviour is tuned for at-least-once,
idempotent processing: late acknowledgement, worker-loss rejection and a
prefetch multiplier of one so one slow task cannot hoard a worker's slots.
"""

from __future__ import annotations

from celery import Celery

from ipa.core.config import get_settings
from ipa.core.otel import get_tracer

_QUEUES = ("default", "ocr", "llm", "embed")


def build_celery_app(name: str = "ipa") -> Celery:
    """Build the Celery application from settings.

    Args:
        name: The Celery application name.

    Returns:
        A configured `Celery` instance.
    """
    settings = get_settings()
    app = Celery(
        name,
        broker=settings.redis.celery_broker_url,
        backend=settings.redis.celery_result_backend,
        include=["ipa.pipeline.runner"],
    )

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_time_limit=3600,
        task_soft_time_limit=3540,
        broker_connection_retry_on_startup=True,
        task_routes={
            "ipa.run_step": {"queue": "default"},
            "ipa.sweep": {"queue": "default"},
        },
    )
    return app


celery_app = build_celery_app()

_tracer = get_tracer(__name__)


def trace_context(task_id: str) -> dict[str, str]:
    """Build the trace headers to propagate on a task.

    Args:
        task_id: The Celery task id.

    Returns:
        A dict suitable for `apply_async(headers=...)`.
    """
    span = _tracer.start_span("celery.enqueue")
    context = span.get_span_context()
    return {
        "traceparent": f"00-{context.trace_id:032x}-{context.span_id:016x}-01",
        "x-celery-task-id": task_id,
    }
