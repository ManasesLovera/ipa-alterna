"""Extraction domain: coercion, validation and document confidence."""

from __future__ import annotations

from ipa.contracts.models import ExtractedField
from ipa.core.enums import FieldType
from ipa.domain.extraction import (
    FieldSpec,
    adjust_confidence,
    coerce_value,
    score_document,
    validate_field,
)


def _number_spec(**kwargs: object) -> FieldSpec:
    defaults: dict[str, object] = {"key": "total", "field_type": FieldType.NUMBER}
    defaults.update(kwargs)
    return FieldSpec(**defaults)


def _string_spec(**kwargs: object) -> FieldSpec:
    defaults: dict[str, object] = {"key": "code", "field_type": FieldType.STRING}
    defaults.update(kwargs)
    return FieldSpec(**defaults)


def test_number_coercion_strips_currency() -> None:
    value, errors = coerce_value(_number_spec(), "$1,234.56")

    assert value == 1234.56
    assert errors == []


def test_number_coercion_invalid_sets_none() -> None:
    value, errors = coerce_value(_number_spec(), "not a number")

    assert value is None
    assert errors


def test_date_coercion_normalises_formats() -> None:
    spec = FieldSpec(key="issued_on", field_type=FieldType.DATE)

    iso, _ = coerce_value(spec, "2024-12-31")
    locale, _ = coerce_value(spec, "31/12/2024")
    named, _ = coerce_value(spec, "Dec 31, 2024")

    assert iso == locale == named == "2024-12-31"


def test_date_coercion_invalid_sets_none_no_exception() -> None:
    spec = FieldSpec(key="issued_on", field_type=FieldType.DATE)

    value, errors = coerce_value(spec, "not a date")

    assert value is None
    assert errors


def test_boolean_coercion() -> None:
    spec = FieldSpec(key="flag", field_type=FieldType.BOOLEAN)

    assert coerce_value(spec, "true")[0] is True
    assert coerce_value(spec, "no")[0] is False


def test_validate_required_and_regex() -> None:
    spec = _string_spec(required=True, regex=r"[A-Z]+\d+")

    assert validate_field(spec, "ABC123", 0.9) == []
    assert validate_field(spec, "abc", 0.9)
    assert validate_field(spec, None, 0.9)


def test_adjust_confidence_clamps_invalid_to_04() -> None:
    spec = _string_spec(regex=r"[A-Z]")

    adjusted = adjust_confidence(spec, "invalid", 0.9, valid=False)

    assert adjusted <= 0.4


def test_adjust_confidence_missing_required_is_zero() -> None:
    spec = _string_spec(required=True)

    adjusted = adjust_confidence(spec, None, 0.9, valid=False)

    assert adjusted == 0.0


def test_score_document_weights_required_higher() -> None:
    fields = [
        ExtractedField(key="required", value="x", confidence=0.9, valid=True),
        ExtractedField(key="optional", value="y", confidence=0.5, valid=True),
    ]

    score = score_document(fields, required_keys={"required"})

    assert score == (0.9 * 2 + 0.5) / 3


def test_score_document_caps_at_05_when_required_missing() -> None:
    fields = [
        ExtractedField(key="required", value=None, confidence=0.9, valid=False),
        ExtractedField(key="optional", value="y", confidence=0.9, valid=True),
    ]

    score = score_document(fields, required_keys={"required"})

    assert score <= 0.5


def test_score_document_empty_is_one() -> None:
    assert score_document([]) == 1.0
