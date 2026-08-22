"""DocumentService: ingest, dedupe, reads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ipa.api.schemas.documents import (
    UploadPayload,
)
from ipa.core.enums import PipelineStep, StepStatus
from ipa.db.dtos import (
    DocumentDto,
    DocumentEventDto,
    DocumentPatch,
    DocumentStepDto,
)
from ipa.db.enums import DocumentSource
from ipa.domain.documents import DocumentService


class FakeBlob:
    """In-memory BlobStore."""

    def __init__(self) -> None:
        self.put_calls: list[tuple[str, bytes, str]] = []
        self._data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        self.put_calls.append((key, data, content_type))
        self._data[key] = data
        return key

    async def get(self, key: str) -> bytes:
        return self._data.get(key, b"")

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def presigned_url(self, key: str, expires_s: int = 900) -> str:
        return f"https://cdn/{key}"

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class FakeContent:
    """In-memory ContentStore."""

    def __init__(self) -> None:
        self.pages: dict[UUID, list[Any]] = {}
        self.extractions: dict[UUID, list[Any]] = {}
        self.deleted: list[UUID] = []

    async def get_pages(self, document_id: UUID) -> list[Any]:
        return self.pages.get(document_id, [])

    async def put_pages(self, document_id: UUID, pages: list[Any]) -> None:
        self.pages[document_id] = pages

    async def get_extraction(self, document_id: UUID, version: int | None = None) -> Any:
        versions = self.extractions.get(document_id, [])
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for record in versions:
            if record.version == version:
                return record
        return None

    async def delete_document(self, document_id: UUID) -> None:
        self.pages.pop(document_id, None)
        self.extractions.pop(document_id, None)
        self.deleted.append(document_id)


class FakeDocRepo:
    """In-memory DocumentRepository."""

    def __init__(self) -> None:
        self._docs: dict[UUID, DocumentDto] = {}

    def _seed(self, sha256: str, **overrides: Any) -> DocumentDto:
        now = datetime.now(UTC)
        doc = DocumentDto(
            id=uuid4(),
            sha256=sha256,
            original_filename="sample.pdf",
            mime_type="application/pdf",
            size_bytes=1234,
            blob_key=f"originals/aa/{sha256}",
            page_count=None,
            tag_id=None,
            tag_version=None,
            status="received",
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
        doc = DocumentDto(**data)
        self._docs[doc.id] = doc
        return doc

    async def create(self, **kwargs: Any) -> DocumentDto:
        overrides = dict(kwargs)
        sha256 = overrides.pop("sha256")
        return self._seed(sha256, **overrides)

    async def get(self, document_id: UUID) -> DocumentDto | None:
        return self._docs.get(document_id)

    async def find_by_sha256(self, sha256: str) -> DocumentDto | None:
        for doc in self._docs.values():
            if doc.sha256 == sha256 and doc.deleted_at is None:
                return doc
        return None

    async def patch(self, document_id: UUID, patch: DocumentPatch) -> DocumentDto | None:
        doc = self._docs.get(document_id)
        if doc is None:
            return None
        changes = patch.model_dump(exclude_unset=True)
        data = doc.model_dump()
        data.update(changes)
        self._docs[document_id] = DocumentDto(**data)
        return self._docs[document_id]

    async def list_documents(self, **kwargs: Any) -> list[DocumentDto]:
        return [d for d in self._docs.values() if d.deleted_at is None]

    async def count_by_tag(self, tag_id: UUID) -> int:
        return sum(1 for d in self._docs.values() if d.tag_id == tag_id)


class FakeStepRepo:
    """In-memory StepRepository for the ingest flow."""

    def __init__(self) -> None:
        self._steps: dict[tuple[UUID, PipelineStep], DocumentStepDto] = {}
        self.ensure_calls = 0

    async def ensure_steps(self, document_id: UUID, *, max_attempts: int = 3) -> list[Any]:
        self.ensure_calls += 1
        for step in PipelineStep:
            key = (document_id, step)
            if key not in self._steps:
                self._steps[key] = DocumentStepDto(
                    id=uuid4(),
                    document_id=document_id,
                    step=step,
                    status=StepStatus.PENDING,
                    attempt=0,
                    max_attempts=max_attempts,
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
        return list(self._steps.values())

    async def get(self, document_id: UUID, step: PipelineStep) -> DocumentStepDto | None:
        return self._steps.get((document_id, step))

    async def list_steps(self, document_id: UUID) -> list[DocumentStepDto]:
        return [s for (did, _), s in self._steps.items() if did == document_id]

    async def mark_succeeded(
        self, document_id: UUID, step: PipelineStep, **kwargs: Any
    ) -> DocumentStepDto | None:
        key = (document_id, step)
        row = self._steps.get(key)
        if row is None:
            return None
        updated = DocumentStepDto(
            **{
                **row.model_dump(),
                "status": StepStatus.SUCCEEDED,
                "finished_at": datetime.now(UTC),
            }
        )
        self._steps[key] = updated
        return updated


class FakeEventRepo:
    """In-memory EventRepository."""

    def __init__(self) -> None:
        self._events: list[DocumentEventDto] = []
        self.calls: list[str] = []

    async def append(self, document_id: UUID, event_type: str, **kwargs: Any) -> DocumentEventDto:
        self.calls.append(event_type)
        event = DocumentEventDto(
            id=len(self._events) + 1,
            document_id=document_id,
            step=kwargs.get("step"),
            event_type=event_type,
            payload=kwargs.get("payload"),
            actor=kwargs.get("actor"),
            trace_id=kwargs.get("trace_id"),
            created_at=datetime.now(UTC),
        )
        self._events.append(event)
        return event

    async def list_events(self, document_id: UUID, *, limit: int = 100) -> list[DocumentEventDto]:
        return [e for e in self._events if e.document_id == document_id]


def _service(
    ) -> tuple[
        DocumentService, FakeDocRepo, FakeStepRepo, FakeEventRepo, FakeBlob, FakeContent
    ]:
    doc_repo = FakeDocRepo()
    step_repo = FakeStepRepo()
    event_repo = FakeEventRepo()
    blob = FakeBlob()
    content = FakeContent()
    service = DocumentService(doc_repo, step_repo, event_repo, blob, content)
    return service, doc_repo, step_repo, event_repo, blob, content


async def test_ingest_new_document_returns_202_and_writes_blob_once() -> None:
    service, _, step_repo, event_repo, blob, _ = _service()
    payload = UploadPayload(filename="a.pdf", data=b"hello world", mime_type="application/pdf")

    result = await service.ingest(payload)

    assert result.deduplicated is False
    assert result.http_status == 202
    assert len(blob.put_calls) == 1
    assert step_repo.ensure_calls == 1
    assert "uploaded" in event_repo.calls


async def test_ingest_deduplicates_same_content() -> None:
    service, _, _, _, blob, _ = _service()
    payload = UploadPayload(filename="a.pdf", data=b"same bytes", mime_type="application/pdf")

    first = await service.ingest(payload)
    second = await service.ingest(payload)

    assert first.deduplicated is False
    assert second.deduplicated is True
    assert second.http_status == 200
    assert len(blob.put_calls) == 1


async def test_get_returns_document() -> None:
    service, doc_repo, *_ = _service()
    doc = await doc_repo.create(sha256="a" * 64, original_filename="x.pdf")

    result = await service.get(doc.id)

    assert result.id == doc.id
    assert result.original_filename == "x.pdf"


async def test_ingest_creates_six_steps_and_marks_store_succeeded() -> None:
    service, _, _, _, _, _ = _service()
    payload = UploadPayload(filename="a.pdf", data=b"data", mime_type="application/pdf")

    result = await service.ingest(payload)

    steps = await service.steps(result.document.id)
    assert len(steps) == 6
    store = next(s for s in steps if s.step == PipelineStep.STORE)
    assert store.status == StepStatus.SUCCEEDED
    decompose = next(s for s in steps if s.step == PipelineStep.DECOMPOSE)
    assert decompose.status == StepStatus.PENDING


async def test_content_read_empty_when_no_pages() -> None:
    service, doc_repo, *_ = _service()
    doc = await doc_repo.create(sha256="b" * 64)

    content = await service.content(doc.id)

    assert content.pages == []


async def test_extraction_none_when_absent() -> None:
    service, doc_repo, *_ = _service()
    doc = await doc_repo.create(sha256="c" * 64)

    extraction = await service.extraction(doc.id)

    assert extraction is None


async def test_download_url_returns_presigned() -> None:
    service, doc_repo, *_ = _service()
    doc = await doc_repo.create(sha256="d" * 64)

    url = await service.download_url(doc.id)

    assert url.startswith("https://cdn/")
