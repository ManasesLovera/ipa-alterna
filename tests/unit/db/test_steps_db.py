"""Step repository behaviour against a real PostgreSQL."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ipa.core.enums import STEP_ORDER, PipelineStep, StepStatus

pytestmark = pytest.mark.integration_light


async def test_ensure_steps_creates_six_rows_in_order(
    db_session: AsyncSession, document_factory: Any
) -> None:
    from ipa.db.repositories.step import StepRepository

    document = await document_factory()
    repository = StepRepository(db_session)
    steps = await repository.ensure_steps(document.id)

    assert [step.step for step in steps] == list(STEP_ORDER)
    assert all(step.status == StepStatus.PENDING for step in steps)
    assert all(step.attempt == 0 for step in steps)
    assert all(step.max_attempts == 3 for step in steps)

    again = await repository.ensure_steps(document.id)
    assert len(again) == 6


async def test_claim_is_atomic_only_one_concurrent_caller_wins(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from ipa.db.enums import DocumentSource
    from ipa.db.models import Document
    from ipa.db.repositories.step import StepRepository

    async with db_sessionmaker() as setup:
        document = Document(
            sha256="0" * 64,
            original_filename="race.pdf",
            mime_type="application/pdf",
            size_bytes=1,
            blob_key="originals/00/" + "0" * 64,
            source=DocumentSource.UI,
        )
        setup.add(document)
        await setup.flush()
        repository = StepRepository(setup)
        await repository.ensure_steps(document.id)
        await setup.commit()
        document_id = document.id

    async def claim_once(session: AsyncSession, *, commit: bool) -> object | None:
        repository = StepRepository(session)
        claimed = await repository.claim(document_id, PipelineStep.OCR)
        if commit:
            await session.commit()
        return claimed

    async with db_sessionmaker() as first, db_sessionmaker() as second:
        first_claim = await claim_once(first, commit=False)

        second_task = asyncio.create_task(claim_once(second, commit=True))
        await asyncio.sleep(0.25)
        await first.commit()
        second_claim = await second_task

    assert first_claim is not None
    assert first_claim.status == StepStatus.RUNNING
    assert first_claim.attempt == 1
    assert first_claim.started_at is not None
    assert second_claim is None


async def test_claim_then_mark_succeeded_and_failed(
    db_session: AsyncSession, document_factory: Any
) -> None:
    from ipa.db.repositories.step import StepRepository

    document = await document_factory(sha256="b" * 64)
    repository = StepRepository(db_session)
    await repository.ensure_steps(document.id)

    claimed = await repository.claim(document.id, PipelineStep.STORE)
    assert claimed is not None
    assert await repository.claim(document.id, PipelineStep.STORE) is None

    succeeded = await repository.mark_succeeded(
        document.id,
        PipelineStep.STORE,
        output_ref="originals/aa/aaa",
        duration_ms=42,
        metrics={"bytes": 1234},
    )
    assert succeeded is not None
    assert succeeded.status == StepStatus.SUCCEEDED
    assert succeeded.duration_ms == 42
    assert succeeded.metrics == {"bytes": 1234}
    assert await repository.mark_succeeded(document.id, PipelineStep.STORE) is None

    running = await repository.claim(document.id, PipelineStep.DECOMPOSE)
    assert running is not None
    failed = await repository.mark_failed(
        document.id, PipelineStep.DECOMPOSE, error_code="bad_pdf", error_detail="nope"
    )
    assert failed is not None
    assert failed.status == StepStatus.FAILED
    assert failed.error_code == "bad_pdf"


async def test_reset_from_resets_target_and_downstream_only(
    db_session: AsyncSession, document_factory: Any
) -> None:
    from ipa.db.repositories.step import StepRepository

    document = await document_factory(sha256="c" * 64)
    repository = StepRepository(db_session)
    await repository.ensure_steps(document.id)

    for step in (PipelineStep.STORE, PipelineStep.DECOMPOSE, PipelineStep.OCR):
        claimed = await repository.claim(document.id, step)
        assert claimed is not None
        marked = await repository.mark_succeeded(document.id, step, duration_ms=1)
        assert marked is not None
    for step in (PipelineStep.EXTRACT, PipelineStep.EMBED):
        claimed = await repository.claim(document.id, step)
        assert claimed is not None
        marked = await repository.mark_failed(document.id, step, error_code="x", error_detail="y")
        assert marked is not None

    steps = await repository.reset_from(document.id, PipelineStep.OCR)
    by_step = {step.step: step for step in steps}

    assert by_step[PipelineStep.STORE].status == StepStatus.SUCCEEDED
    assert by_step[PipelineStep.DECOMPOSE].status == StepStatus.SUCCEEDED
    for step in STEP_ORDER[2:]:
        assert by_step[step].status == StepStatus.PENDING, step
        assert by_step[step].attempt == 0, step
        assert by_step[step].error_code is None, step
        assert by_step[step].output_ref is None, step
    assert by_step[PipelineStep.OCR].metrics == {}


async def test_claim_missing_document_returns_none(db_session: AsyncSession) -> None:
    from ipa.db.repositories.step import StepRepository

    repository = StepRepository(db_session)
    assert await repository.claim(uuid4(), PipelineStep.OCR) is None
