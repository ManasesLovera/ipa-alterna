"""Shared DTO shapes — these are the contract 18 downstream tasks import."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ipa.contracts import models
from ipa.contracts.models import (
    ChunkHit,
    ChunkVector,
    ExtractedField,
    ExtractionRecord,
    ImageRef,
    LlmJsonResult,
    OcrResult,
    PageText,
    SearchFilters,
    StepContext,
    StepResult,
)
from ipa.core.enums import DocumentStatus, ExtractionSource, PipelineStep, StepStatus


def test_page_text_round_trips() -> None:
    page = PageText(page=1, text="hello", source="text_layer", char_count=5)

    assert page.model_dump() == {
        "page": 1,
        "text": "hello",
        "source": "text_layer",
        "confidence": None,
        "char_count": 5,
    }


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        OcrResult(text="x", source="vlm", typo=True)  # type: ignore[call-arg]


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_is_bounded(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ExtractedField(key="total", value=1, confidence=confidence)


def test_extracted_field_defaults_to_valid_with_no_errors() -> None:
    field = ExtractedField(key="total", value="10.00", confidence=0.9)

    assert field.valid is True
    assert field.validation_errors == []
    assert field.evidence is None


def test_extraction_record_accepts_a_full_version() -> None:
    record = ExtractionRecord(
        document_id=uuid4(),
        version=2,
        tag_id=uuid4(),
        tag_version=1,
        source=ExtractionSource.HUMAN,
        model=None,
        prompt_hash=None,
        fields=[ExtractedField(key="total", value=10, confidence=1.0)],
        document_confidence=1.0,
        created_at=datetime.now(UTC),
        created_by="reviewer@example.com",
    )

    assert record.source is ExtractionSource.HUMAN
    assert record.fields[0].key == "total"


def test_chunk_vector_reports_its_dimension() -> None:
    chunk = ChunkVector(
        document_id=uuid4(),
        chunk_index=0,
        text="body",
        embedding=[0.1, 0.2, 0.3],
        embed_model="nvidia/nv-embed-v1",
        page_from=1,
        page_to=2,
    )

    assert chunk.embed_dim == 3
    assert chunk.metadata == {}


def test_default_collections_are_not_shared_between_instances() -> None:
    first = StepResult(status=StepStatus.SUCCEEDED)
    second = StepResult(status=StepStatus.SUCCEEDED)
    first.metrics["pages"] = 3.0

    assert second.metrics == {}


def test_search_filters_are_all_optional() -> None:
    empty = SearchFilters()
    filtered = SearchFilters(statuses=[DocumentStatus.VALIDATED])

    assert empty.model_dump(exclude_none=True) == {}
    assert filtered.statuses == [DocumentStatus.VALIDATED]


def test_step_context_and_result_carry_pipeline_types() -> None:
    context = StepContext(document_id=uuid4(), step=PipelineStep.OCR, attempt=1)
    result = StepResult(
        status=StepStatus.FAILED,
        detail="provider timeout",
        metrics={"pages": 12.0},
        next_step_override=PipelineStep.REVIEW,
    )

    assert context.step is PipelineStep.OCR
    assert result.next_step_override is PipelineStep.REVIEW


def test_llm_json_result_and_image_ref_minimal_forms() -> None:
    result = LlmJsonResult(data={"total": 1}, raw_text='{"total": 1}', model="m", latency_ms=12)
    ref = ImageRef(page=1, blob_key="pages/x/00001.png", mime="image/png")

    assert result.repaired is False
    assert result.prompt_tokens is None
    assert ref.page == 1


def test_chunk_hit_orders_by_score_descending() -> None:
    hits = [
        ChunkHit(document_id=uuid4(), chunk_index=i, text="t", score=score, page_from=1, page_to=1)
        for i, score in enumerate([0.2, 0.9, 0.5])
    ]

    assert [hit.score for hit in sorted(hits, key=lambda h: -h.score)] == [0.9, 0.5, 0.2]


def test_every_documented_dto_is_exported() -> None:
    expected = {
        "PageText",
        "OcrResult",
        "ImageRef",
        "LlmJsonResult",
        "ExtractionRecord",
        "ExtractedField",
        "ChunkVector",
        "ChunkHit",
        "SearchFilters",
        "StepContext",
        "StepResult",
    }

    assert expected <= set(dir(models))
