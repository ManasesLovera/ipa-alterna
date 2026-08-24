"""Search service: field matching, grouping and citation parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ipa.api.schemas.search import ChunkHitRead
from ipa.contracts.models import ExtractedField, ExtractionRecord
from ipa.core.enums import ExtractionSource
from ipa.domain.search import CITATION_RE, _group_by_document, _matches_query


def _record(**overrides: object) -> ExtractionRecord:
    values = {
        "document_id": uuid4(),
        "version": 1,
        "tag_id": uuid4(),
        "tag_version": 1,
        "source": ExtractionSource.MODEL,
        "fields": [
            ExtractedField(key="vendor", value="ACME", confidence=0.9),
            ExtractedField(key="total", value=1500, confidence=0.9),
        ],
        "document_confidence": 0.9,
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return ExtractionRecord(**values)


def test_matches_query_eq() -> None:
    record = _record()

    assert _matches_query(record, {"vendor": "ACME"}) is True
    assert _matches_query(record, {"vendor": "Other"}) is False


def test_matches_query_gte_operator() -> None:
    record = _record()

    assert _matches_query(record, {"total": {"gte": 1000}}) is True
    assert _matches_query(record, {"total": {"gte": 2000}}) is False


def test_matches_query_contains() -> None:
    record = _record()

    assert _matches_query(record, {"vendor": {"contains": "AC"}}) is True
    assert _matches_query(record, {"vendor": {"contains": "ZZ"}}) is False


def test_matches_query_exists() -> None:
    record = _record(fields=[ExtractedField(key="vendor", value=None, confidence=0.5)])

    assert _matches_query(record, {"vendor": {"exists": False}}) is True
    assert _matches_query(record, {"vendor": {"exists": True}}) is False


def test_group_by_document_collapses_chunks() -> None:
    doc_id = uuid4()
    hits = [
        ChunkHitRead(document_id=doc_id, chunk_index=0, text="a", score=0.9),
        ChunkHitRead(document_id=doc_id, chunk_index=1, text="b", score=0.7),
        ChunkHitRead(document_id=uuid4(), chunk_index=0, text="c", score=0.5),
    ]

    grouped = _group_by_document(hits)

    assert len(grouped) == 2
    first = grouped[0]
    assert first.document_id == doc_id
    assert first.best_score == 0.9
    assert len(first.chunks) == 2


def test_citation_regex_parses_markers() -> None:
    text = "The total is 100 [doc:00000000-0000-0000-0000-000000000001 p:2]."

    matches = CITATION_RE.findall(text)

    assert matches == [("00000000-0000-0000-0000-000000000001", "2")]
