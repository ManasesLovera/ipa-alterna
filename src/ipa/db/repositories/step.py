"""Step repository: the atomic state machine over `document_steps`."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import STEP_ORDER, PipelineStep, StepStatus, steps_from
from ipa.db.dtos import DocumentStepDto
from ipa.db.models import DocumentStep


def _in_step_order(rows: Any) -> list[DocumentStep]:
    """Sort step rows into canonical pipeline order.

    Args:
        rows: Iterable of `DocumentStep` entities.

    Returns:
        The rows ordered by their position in `STEP_ORDER`.
    """
    return sorted(rows, key=lambda row: STEP_ORDER.index(row.step))


class StepRepository:
    """Reads and writes per-step execution state; all mutations are atomic."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def ensure_steps(
        self, document_id: UUID, *, max_attempts: int = 3
    ) -> list[DocumentStepDto]:
        """Create the six per-step rows for a document if they do not exist.

        Idempotent: existing rows are left untouched, missing ones inserted.

        Args:
            document_id: Owning document.
            max_attempts: Retry budget recorded on each row.

        Returns:
            All six step rows in `STEP_ORDER`.
        """
        values = [
            {"document_id": document_id, "step": step, "max_attempts": max_attempts}
            for step in STEP_ORDER
        ]
        stmt = (
            pg_insert(DocumentStep)
            .values(values)
            .on_conflict_do_nothing(index_elements=["document_id", "step"])
        )
        await self._session.execute(stmt)
        return await self.list_steps(document_id)

    async def list_steps(self, document_id: UUID) -> list[DocumentStepDto]:
        """Return the document's step rows in canonical pipeline order.

        Args:
            document_id: Owning document.

        Returns:
            Step DTOs ordered by `STEP_ORDER`.
        """
        stmt = sa.select(DocumentStep).where(DocumentStep.document_id == document_id)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DocumentStepDto.model_validate(row) for row in _in_step_order(rows)]

    async def get(self, document_id: UUID, step: PipelineStep) -> DocumentStepDto | None:
        """Fetch one step row.

        Args:
            document_id: Owning document.
            step: The pipeline step to fetch.

        Returns:
            The step DTO, or None when the row does not exist.
        """
        stmt = sa.select(DocumentStep).where(
            DocumentStep.document_id == document_id, DocumentStep.step == step
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentStepDto.model_validate(row) if row else None

    async def claim(self, document_id: UUID, step: PipelineStep) -> DocumentStepDto | None:
        """Atomically transition a step from `pending` to `running`.

        Implemented as a single `UPDATE ... WHERE status = 'pending' RETURNING`
        statement, so exactly one concurrent caller can win.

        Args:
            document_id: Owning document.
            step: The pipeline step to claim.

        Returns:
            The claimed step DTO with the attempt counter incremented, or None
            when the step is missing or not pending.
        """
        stmt = (
            sa.update(DocumentStep)
            .where(
                DocumentStep.document_id == document_id,
                DocumentStep.step == step,
                DocumentStep.status == StepStatus.PENDING,
            )
            .values(
                status=StepStatus.RUNNING,
                attempt=DocumentStep.attempt + 1,
                started_at=sa.func.now(),
                finished_at=None,
                duration_ms=None,
                error_code=None,
                error_detail=None,
                updated_at=sa.func.now(),
            )
            .returning(DocumentStep)
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentStepDto.model_validate(row) if row else None

    async def mark_succeeded(
        self,
        document_id: UUID,
        step: PipelineStep,
        *,
        output_ref: str | None = None,
        duration_ms: int | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> DocumentStepDto | None:
        """Transition a running step to `succeeded`.

        Args:
            document_id: Owning document.
            step: The pipeline step completing.
            output_ref: Reference to the step's durable output, if any.
            duration_ms: Wall-clock duration; computed from `started_at` when None.
            metrics: Step metrics replacing any previous value.

        Returns:
            The updated step DTO, or None when the step is not running.
        """
        duration = (
            duration_ms
            if duration_ms is not None
            else sa.cast(
                sa.func.floor(sa.extract("epoch", sa.func.now() - DocumentStep.started_at) * 1000),
                sa.Integer,
            )
        )
        stmt = (
            sa.update(DocumentStep)
            .where(
                DocumentStep.document_id == document_id,
                DocumentStep.step == step,
                DocumentStep.status == StepStatus.RUNNING,
            )
            .values(
                status=StepStatus.SUCCEEDED,
                finished_at=sa.func.now(),
                duration_ms=duration,
                output_ref=output_ref,
                updated_at=sa.func.now(),
                **({"metrics": metrics} if metrics is not None else {}),
            )
            .returning(DocumentStep)
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentStepDto.model_validate(row) if row else None

    async def mark_failed(
        self,
        document_id: UUID,
        step: PipelineStep,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> DocumentStepDto | None:
        """Transition a running step to `failed`.

        Args:
            document_id: Owning document.
            step: The pipeline step that failed.
            error_code: Stable machine-readable failure code.
            error_detail: Human-readable failure explanation.

        Returns:
            The updated step DTO, or None when the step is not running.
        """
        stmt = (
            sa.update(DocumentStep)
            .where(
                DocumentStep.document_id == document_id,
                DocumentStep.step == step,
                DocumentStep.status == StepStatus.RUNNING,
            )
            .values(
                status=StepStatus.FAILED,
                finished_at=sa.func.now(),
                error_code=error_code,
                error_detail=error_detail,
                updated_at=sa.func.now(),
            )
            .returning(DocumentStep)
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return DocumentStepDto.model_validate(row) if row else None

    async def reset_from(self, document_id: UUID, step: PipelineStep) -> list[DocumentStepDto]:
        """Reset `step` and every downstream step to `pending`.

        Args:
            document_id: Owning document.
            step: First step to reset, inclusive.

        Returns:
            The document's step rows in `STEP_ORDER` after the reset.
        """
        stmt = (
            sa.update(DocumentStep)
            .where(
                DocumentStep.document_id == document_id,
                DocumentStep.step.in_(steps_from(step)),
            )
            .values(
                status=StepStatus.PENDING,
                attempt=0,
                started_at=None,
                finished_at=None,
                duration_ms=None,
                error_code=None,
                error_detail=None,
                output_ref=None,
                metrics=sa.text("'{}'::jsonb"),
                updated_at=sa.func.now(),
            )
        )
        await self._session.execute(stmt)
        return await self.list_steps(document_id)
