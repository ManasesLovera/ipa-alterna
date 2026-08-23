"""Pipeline orchestration: enqueue, reprocess and status recomputation.

`enqueue_from` schedules the next pending step; `reprocess` resets a step and
everything after it and re-enqueues; `recompute_status` derives the coarse
document status from the per-step state machine.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog

from ipa.core.enums import STEP_ORDER, DocumentStatus, PipelineStep, StepStatus
from ipa.core.errors import ConflictError, NotFoundError
from ipa.db.dtos import DocumentDto, DocumentStepDto
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.step import StepRepository

logger = structlog.get_logger(__name__)

EnqueueCallable = Callable[[UUID, PipelineStep], None]


class Orchestrator:
    """Coordinates step execution and reprocessing for documents."""

    def __init__(
        self, documents: DocumentRepository, steps: StepRepository, enqueue: EnqueueCallable
    ) -> None:
        """Initialise the orchestrator.

        Args:
            documents: Document repository.
            steps: Step repository.
            enqueue: Callable to schedule a step task (injected to avoid the
                Celery import cycle).
        """
        self._documents = documents
        self._steps = steps
        self._enqueue = enqueue

    async def enqueue_from(self, document_id: UUID, step: PipelineStep) -> None:
        """Enqueue the given step for a document.

        Args:
            document_id: Identifier of the document.
            step: The step to schedule.
        """
        self._enqueue(document_id, step)

    async def reprocess(
        self, document_id: UUID, from_step: PipelineStep, *, actor: str | None = None
    ) -> None:
        """Reset a step and everything after it, then re-enqueue.

        Args:
            document_id: Identifier of the document.
            from_step: The first step to reset (inclusive).
            actor: Who requested the reprocess.

        Returns:
            None.

        Raises:
            ConflictError: If `from_step` is `store`, whose blob is immutable.
            NotFoundError: If the document does not exist.
        """
        if from_step == PipelineStep.STORE:
            raise ConflictError(
                "The store step cannot be reprocessed; re-upload the file.",
                code="cannot_reprocess_store",
            )
        await self._must_get(document_id)
        await self._steps.reset_from(document_id, from_step)
        self._enqueue(document_id, from_step)

    async def recompute_status(self, document_id: UUID) -> DocumentStatus:
        """Derive the coarse document status from its per-step state.

        Args:
            document_id: Identifier of the document.

        Returns:
            The derived `DocumentStatus`.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._must_get(document_id)
        steps = await self._steps.list_steps(document_id)
        status = _derive_status(steps, document)
        if status != document.status:
            from ipa.db.dtos import DocumentPatch

            await self._documents.patch(
                document_id, DocumentPatch(status=status, current_step=_current_step(steps))
            )
        return status

    async def next_step(self, current: PipelineStep) -> PipelineStep | None:
        """Return the step that follows `current`, or None.

        Args:
            current: The current step.

        Returns:
            The next step in `STEP_ORDER`, or None for the final step.
        """
        try:
            index = STEP_ORDER.index(current)
        except ValueError:
            return None
        return STEP_ORDER[index + 1] if index + 1 < len(STEP_ORDER) else None

    async def _must_get(self, document_id: UUID) -> DocumentDto:
        """Return a document or raise NotFoundError.

        Args:
            document_id: Identifier of the document.

        Returns:
            The document DTO.

        Raises:
            NotFoundError: If the document does not exist.
        """
        document = await self._documents.get(document_id)
        if document is None:
            raise NotFoundError(f"no document with id {document_id}")
        return document


def _derive_status(steps: list[DocumentStepDto], document: DocumentDto) -> DocumentStatus:
    """Derive a document status from its step states.

    Args:
        steps: The document's step rows in pipeline order.
        document: The document row (for its quarantine/flag state).

    Returns:
        The derived status.
    """
    if document.status == DocumentStatus.QUARANTINED:
        return DocumentStatus.QUARANTINED
    if any(step.status == StepStatus.FAILED for step in steps):
        return DocumentStatus.FAILED
    if all(step.status == StepStatus.SUCCEEDED for step in steps):
        return DocumentStatus.COMPLETED
    if any(step.status == StepStatus.RUNNING for step in steps):
        return DocumentStatus.PROCESSING
    return DocumentStatus.PROCESSING


def _current_step(steps: list[DocumentStepDto]) -> PipelineStep | None:
    """Return the step currently in flight, if any.

    Args:
        steps: The document's step rows.

    Returns:
        The running step, or None.
    """
    for step in steps:
        if step.status == StepStatus.RUNNING:
            return step.step
    return None
