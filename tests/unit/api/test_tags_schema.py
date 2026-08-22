"""Tag API schema validation: keys, reserved words, type constraints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ipa.api.schemas.tags import TagCreate, TagFieldCreate
from ipa.core.enums import FieldType


def _field(**kwargs: object) -> TagFieldCreate:
    defaults: dict[str, object] = {
        "key": "amount",
        "label": "Amount",
        "field_type": FieldType.STRING,
    }
    defaults.update(kwargs)
    return TagFieldCreate(**defaults)


def test_reserved_key_rejected() -> None:
    with pytest.raises(ValidationError):
        _field(key="value")


def test_duplicate_nonsense_key_format_rejected() -> None:
    with pytest.raises(ValidationError):
        _field(key="UPPER-CASE!")


def test_enum_field_requires_values() -> None:
    with pytest.raises(ValidationError):
        _field(key="cur", field_type=FieldType.ENUM, enum_values=None)


def test_array_field_requires_item_type() -> None:
    with pytest.raises(ValidationError):
        _field(key="items", field_type=FieldType.ARRAY)


def test_object_field_requires_schema() -> None:
    with pytest.raises(ValidationError):
        _field(key="obj", field_type=FieldType.OBJECT)


def test_bad_regex_rejected() -> None:
    with pytest.raises(ValidationError):
        _field(key="x", regex="[unclosed")


def test_min_greater_than_max_rejected() -> None:
    with pytest.raises(ValidationError):
        _field(key="n", field_type=FieldType.NUMBER, min_value=10, max_value=1)


def test_valid_field_accepts_optional_position() -> None:
    field = _field(key="number")

    assert field.position == 0


def test_tag_create_validates() -> None:
    tag = TagCreate(
        slug="invoice",
        name="Invoice",
        fields=[_field(key="number", field_type=FieldType.STRING, is_required=True)],
    )

    assert tag.slug == "invoice"
    assert tag.auto_approve_threshold is None
