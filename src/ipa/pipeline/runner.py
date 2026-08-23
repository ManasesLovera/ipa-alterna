"""The single generic Celery task that drives every pipeline step.

`run_step` claims a step atomically (pending -> running), executes its handler,
and on success advances the state machine and enqueues the next step. Retryable
failures are re-enqueued with backoff; quarantine failures stop immediately;
anything else dead-letters.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import UUID

import structlog
from celery import Task

from ipa.core.enums import DocumentStatus, PipelineStep
from ipa.core.errors import QuarantineError, RetryableProviderError
from ipa.db.dtos import DocumentPatch
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.event import EventRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.session import session_scope
from ipa.pipeline.app import celery_app
from ipa.pipeline.orchestrator import Orchestrator
from ipa.pipeline.registry import get_handler

logger = structlog.get_logger(__name__)


def _enqueue(document_id: UUID, step: PipelineStep) -> None:
    """Schedule a step task via the Celery app.

    Args:
        document_id: The document id.
        step: The step to schedule.
    """
    celery_app.send_task(
        "ipa.run_step",
        args=[str(document_id), step.value],
        queue="default",
    )


@celery_app.task(bind=True, name="ipa.run_step", max_retries=None)
def run_step(self: Task, document_id: str, step: str, attempt_hint: int = 0) -> None:
    """Execute one pipeline step for a document.

    Args:
        self: The bound Celery task.
        document_id: Identifier of the document.
        step: The pipeline step name.
        attempt_hint: Suggested attempt number; the claim computes the real one.

    Returns:
        None.
    """
    from uuid import UUID

    step_enum = PipelineStep(step)
    asyncio.run(_execute(UUID(document_id), step_enum))


async def _execute(doc_uuid: UUID, step: PipelineStep) -> None:
    """Run one step inside a fresh event loop and session.

    Args:
        doc_uuid: The document id.
        step: The step to run.

    Returns:
        None.
    """
    async with session_scope() as session:
        steps = StepRepository(session)
        documents = DocumentRepository(session)
        events = EventRepository(session)
        orchestrator = Orchestrator(documents, steps, _enqueue)

        claimed = await steps.claim(doc_uuid, step)
        if claimed is None:
            logger.info("step.claim_missed", document_id=doc_uuid, step=step.value)
            return

        await events.append(
            doc_uuid, "step_started", step=step, payload={"attempt": claimed.attempt}
        )

        handler = get_handler(step)
        from ipa.contracts.models import StepContext

        context = StepContext(document_id=doc_uuid, step=step, attempt=claimed.attempt)
        try:
            result = await handler(context)
        except RetryableProviderError as exc:
            await _handle_retryable(doc_uuid, step, claimed.attempt, steps, events, _enqueue, exc)
            return
        except QuarantineError as exc:
            await steps.mark_failed(doc_uuid, step, error_code=exc.code, error_detail=exc.detail)
            await documents.patch(doc_uuid, DocumentPatch(status=DocumentStatus.QUARANTINED))
            await events.append(
                doc_uuid, "step_failed", step=step,
                payload={"code": exc.code, "quarantined": True},
            )
            logger.warning("step.quarantined", document_id=doc_uuid, code=exc.code)
            return
        except Exception as exc:
            await _handle_failure(doc_uuid, step, claimed.attempt, steps, events, exc)
            return

        duration_ms = result.metrics.get("duration_ms")
        await steps.mark_succeeded(
            doc_uuid, step,
            duration_ms=int(duration_ms) if duration_ms is not None else None,
            metrics=result.metrics,
        )
        await events.append(
            doc_uuid, "step_succeeded", step=step, payload={"metrics": result.metrics}
        )
        await orchestrator.recompute_status(doc_uuid)

        next_step = result.next_step_override or await orchestrator.next_step(step)
        if next_step is not None:
            await orchestrator.enqueue_from(doc_uuid, next_step)


async def _handle_retryable(
    doc_uuid: UUID,
    step: PipelineStep,
    attempt: int,
    steps: StepRepository,
    events: EventRepository,
    enqueue: Callable[[UUID, PipelineStep], None],
    exc: RetryableProviderError,
) -> None:
    """Re-enqueue a step after a transient failure.

    Args:
        doc_uuid: The document id.
        step: The failed step.
        attempt: The attempt that failed.
        steps: The step repository.
        events: The event repository.
        enqueue: The enqueue callable.
        exc: The retryable error.
    """
    row = await steps.get(doc_uuid, step)
    max_attempts = row.max_attempts if row else 3
    if attempt < max_attempts:
        await steps.mark_failed(doc_uuid, step, error_code=exc.code, error_detail=exc.detail)
        await steps.reset_from(doc_uuid, step)
        enqueue(doc_uuid, step)
        await events.append(doc_uuid, "step_retried", step=step, payload={"attempt": attempt})
        logger.warning("step.retried", document_id=doc_uuid, step=step.value, attempt=attempt)
    else:
        await steps.mark_failed(doc_uuid, step, error_code=exc.code, error_detail=exc.detail)
        await events.append(doc_uuid, "step_failed", step=step, payload={"code": exc.code})


async def _handle_failure(
    doc_uuid: UUID,
    step: PipelineStep,
    attempt: int,
    steps: StepRepository,
    events: EventRepository,
    exc: Exception,
) -> None:
    """Dead-letter a permanently failed step.

    Args:
        doc_uuid: The document id.
        step: The failed step.
        attempt: The attempt that failed.
        steps: The step repository.
        events: The event repository.
        exc: The failure.
    """
    code = getattr(exc, "code", "step_failed")
    detail = str(exc)
    await steps.mark_failed(doc_uuid, step, error_code=code, error_detail=detail)
    await events.append(
        doc_uuid, "step_failed", step=step, payload={"code": code, "detail": detail}
    )
    logger.error("step.failed", document_id=doc_uuid, step=step.value, code=code)
