"""Orchestrator: reprocessing, next-step and status recomputation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from ipa.core.enums import (
    STEP_ORDER,
    DocumentStatus,
    PipelineStep,
    StepStatus,
)
from ipa.core.errors import ConflictError, NotFoundError
from ipa.db.dtos import DocumentDto, DocumentStepDto
from ipa.db.enums import DocumentSource
from ipa.pipeline.orchestrator import Orchestrator


class FakeDocRepo:
    """In-memory DocumentRepository."""

    def __init__(self) -> None:
        self._docs: dict[UUID, DocumentDto] = {}

    def _seed(self, **overrides: object) -> DocumentDto:
        now = datetime.now(UTC)
        doc = DocumentDto(
            id=uuid4(),
            sha256="a" * 64,
            original_filename="x.pdf",
            mime_type="application/pdf",
            size_bytes=10,
            blob_key="originals/aa/" + "a" * 64,
            page_count=None,
            tag_id=None,
            tag_version=None,
            status="processing",
            current_step=None,
            document_confidence=None,
            needs_review=False,
            title=None,
            source=DocumentSource.API,
            uploaded_by=None,
            trace_id=None,
            error_code=None,
            error_detail=None,
            metadata={},
            created_at=now,
            updated_at=now,
            completed_at=None,
            deleted_at=None,
        )
        data = doc.model_dump()
        data.update(overrides)
        return DocumentDto(**data)

    async def get(self, document_id: UUID) -> DocumentDto | None:
        return self._docs.get(document_id)

    async def create(self, **overrides: object) -> DocumentDto:
        doc = self._seed(**overrides)
        self._docs[doc.id] = doc
        return doc

    async def patch(self, document_id: UUID, patch: object) -> DocumentDto | None:
        doc = self._docs.get(document_id)
        if doc is None:
            return None
        data = doc.model_dump()
        data.update(patch.model_dump(exclude_unset=True))  # type: ignore[union-attr]
        self._docs[document_id] = DocumentDto(**data)
        return self._docs[document_id]


class FakeStepRepo:
    """In-memory StepRepository."""

    def __init__(self) -> None:
        self._steps: dict[tuple[UUID, PipelineStep], DocumentStepDto] = {}
        self.reset_calls: list[tuple[UUID, PipelineStep]] = []

    def seed_steps(self, document_id: UUID) -> None:
        for step in STEP_ORDER:
            self._steps[(document_id, step)] = DocumentStepDto(
                id=uuid4(),
                document_id=document_id,
                step=step,
                status=StepStatus.PENDING,
                attempt=0,
                max_attempts=3,
                started_at=None,
                finished_at=None,
                duration_ms=None,
                error_code=None,
                error_detail=None,
                output_ref=None,
                metrics={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

    async def list_steps(self, document_id: UUID) -> list[DocumentStepDto]:
        return [s for (did, _), s in self._steps.items() if did == document_id]

    async def reset_from(self, document_id: UUID, step: PipelineStep) -> list[DocumentStepDto]:
        self.reset_calls.append((document_id, step))
        started = STEP_ORDER.index(step)
        for pipeline_step in STEP_ORDER[started:]:
            key = (document_id, pipeline_step)
            if key in self._steps:
                self._steps[key] = DocumentStepDto(
                    **{
                        **self._steps[key].model_dump(),
                        "status": StepStatus.PENDING,
                        "attempt": 0,
                    }
                )
        return await self.list_steps(document_id)


def _service() -> tuple[Orchestrator, FakeDocRepo, FakeStepRepo, list]:
    doc_repo = FakeDocRepo()
    step_repo = FakeStepRepo()
    enqueued: list = []

    def enqueue(document_id: UUID, step: PipelineStep) -> None:
        enqueued.append((document_id, step))

    orchestrator = Orchestrator(doc_repo, step_repo, enqueue)
    return orchestrator, doc_repo, step_repo, enqueued


async def test_reprocess_from_ocr_resets_downstream() -> None:
    orchestrator, doc_repo, step_repo, enqueued = _service()
    doc = await doc_repo.create()
    step_repo.seed_steps(doc.id)
    from ipa.core.enums import PipelineStep

    await orchestrator.reprocess(doc.id, PipelineStep.OCR)

    assert step_repo.reset_calls == [(doc.id, PipelineStep.OCR)]
    assert (doc.id, PipelineStep.OCR) in enqueued


async def test_reprocess_from_store_rejected() -> None:
    orchestrator, doc_repo, _, _ = _service()
    doc = await doc_repo.create()

    try:
        await orchestrator.reprocess(doc.id, PipelineStep.STORE)
    except ConflictError as exc:
        assert exc.code == "cannot_reprocess_store"
    else:
        raise AssertionError("expected ConflictError")


async def test_next_step_walks_order() -> None:
    orchestrator, _, _, _ = _service()

    assert await orchestrator.next_step(PipelineStep.STORE) == PipelineStep.DECOMPOSE
    assert await orchestrator.next_step(PipelineStep.OCR) == PipelineStep.EXTRACT
    assert await orchestrator.next_step(PipelineStep.REVIEW) is None


async def test_reprocess_unknown_document_raises_not_found() -> None:
    orchestrator, _, _, _ = _service()

    try:
        await orchestrator.reprocess(uuid4(), PipelineStep.OCR)
    except NotFoundError:
        pass
    else:
        raise AssertionError("expected NotFoundError")


async def test_derive_status_completed_when_all_succeeded() -> None:
    orchestrator, doc_repo, step_repo, _ = _service()
    doc = await doc_repo.create()
    step_repo.seed_steps(doc.id)
    for step in STEP_ORDER:
        key = (doc.id, step)
        step_repo._steps[key] = DocumentStepDto(
            **{
                **step_repo._steps[key].model_dump(),
                "status": StepStatus.SUCCEEDED,
            }
        )

    status = await orchestrator.recompute_status(doc.id)

    assert status == DocumentStatus.COMPLETED
