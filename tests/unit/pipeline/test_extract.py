"""Extract step: field coercion, validation, scoring and prompt assembly."""

from __future__ import annotations

from ipa.contracts.models import PageText
from ipa.pipeline.steps.extract import (
    _build_user_message,
    _extract_fields,
    _TagSpec,
)

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "object",
            "additionalProperties": False,
            "required": ["invoice_number"],
            "properties": {
                "invoice_number": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                        "page": {"type": ["integer", "null"]},
                        "evidence": {"type": ["string", "null"]},
                    },
                },
                "total": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["number", "null"]},
                        "confidence": {"type": "number"},
                    },
                },
            },
        }
    },
}


def _spec(required: set[str]) -> _TagSpec:
    return _TagSpec(
        tag_id="00000000-0000-0000-0000-000000000000",
        json_schema=_SCHEMA,
        prompt="extract",
        tag_version=1,
        required_keys=required,
        field_keys=["invoice_number", "total"],
    )


def test_extract_fields_coerces_number() -> None:
    data = {
        "fields": {
            "invoice_number": {"value": "INV-001", "confidence": 0.9},
            "total": {"value": "$1,234.56", "confidence": 0.9},
        }
    }

    fields = _extract_fields(_spec(required={"invoice_number"}), data)

    by_key = {f.key: f for f in fields}
    assert by_key["invoice_number"].value == "INV-001"
    assert by_key["total"].value == 1234.56
    assert all(f.valid for f in fields)


def test_extract_fields_marks_missing_required_invalid() -> None:
    data = {"fields": {"invoice_number": {"value": None, "confidence": 0.9}}}

    fields = _extract_fields(_spec(required={"invoice_number"}), data)

    by_key = {f.key: f for f in fields}
    assert by_key["invoice_number"].valid is False
    assert by_key["invoice_number"].confidence == 0.0


def test_extract_fields_clamps_invalid_confidence() -> None:
    data = {
        "fields": {
            "invoice_number": {"value": "bad", "confidence": 0.9},
            "total": {"value": "not a number", "confidence": 0.95},
        }
    }

    fields = _extract_fields(_spec(required={"invoice_number"}), data)

    by_key = {f.key: f for f in fields}
    assert by_key["total"].valid is False
    assert by_key["total"].confidence <= 0.4


def test_extract_fields_ignores_unexpected_keys() -> None:
    data = {
        "fields": {
            "invoice_number": {"value": "INV-001", "confidence": 0.9},
            "mystery": {"value": "x", "confidence": 0.9},
        }
    }

    fields = _extract_fields(_spec(required={"invoice_number"}), data)

    assert {f.key for f in fields} == {"invoice_number", "total"}


def test_build_user_message_includes_page_markers() -> None:
    document = type("D", (), {"original_filename": "a.pdf", "page_count": 2})()
    pages = [
        PageText(page=1, text="hello", source="text_layer", char_count=5),
        PageText(page=2, text="world", source="text_layer", char_count=5),
    ]

    message = _build_user_message(document, pages)

    assert "a.pdf" in message
    assert '<page n="1">' in message
    assert '<page n="2">' in message
