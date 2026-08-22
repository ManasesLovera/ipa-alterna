"""JSON Schema compiler and schema hashing."""

from __future__ import annotations

from jsonschema import Draft202012Validator

from ipa.core.enums import FieldType
from ipa.processing.schema import TagFieldSpec, build_json_schema, schema_hash


def _specs() -> list[TagFieldSpec]:
    return [
        TagFieldSpec(key="invoice_number", field_type=FieldType.STRING, required=True),
        TagFieldSpec(key="total", field_type=FieldType.NUMBER),
        TagFieldSpec(key="currency", field_type=FieldType.ENUM, enum_values=["USD", "EUR"]),
        TagFieldSpec(key="issued_on", field_type=FieldType.DATE),
    ]


def test_build_json_schema_is_valid_json_schema() -> None:
    schema = build_json_schema(_specs())

    Draft202012Validator.check_schema(schema)


def test_schema_uses_envelope_and_strict_object() -> None:
    schema = build_json_schema(_specs())

    fields = schema["properties"]["fields"]
    assert fields["additionalProperties"] is False
    assert schema["additionalProperties"] is False
    assert fields["required"] == ["invoice_number"]


def test_field_value_is_nullable() -> None:
    schema = build_json_schema(_specs())

    invoice = schema["properties"]["fields"]["properties"]["invoice_number"]
    value_schema = invoice["properties"]["value"]
    assert value_schema["type"] == ["string", "null"]


def test_enum_field_gets_enum_constraint() -> None:
    schema = build_json_schema(_specs())

    currency = schema["properties"]["fields"]["properties"]["currency"]
    value_schema = currency["properties"]["value"]
    assert value_schema["enum"] == ["USD", "EUR"]


def test_date_field_gets_format() -> None:
    schema = build_json_schema(_specs())

    issued = schema["properties"]["fields"]["properties"]["issued_on"]
    assert issued["properties"]["value"]["format"] == "date"


def test_number_field_accepts_null() -> None:
    schema = build_json_schema(_specs())

    total = schema["properties"]["fields"]["properties"]["total"]
    assert total["properties"]["value"]["type"] == ["number", "null"]


def test_schema_hash_stable_across_key_reordering() -> None:
    original = build_json_schema(_specs())

    reordered = dict(original)
    reordered["properties"]["fields"]["properties"] = dict(
        reversed(list(reordered["properties"]["fields"]["properties"].items()))
    )

    assert schema_hash(original) == schema_hash(reordered)


def test_schema_hash_changes_when_fields_change() -> None:
    schema_a = build_json_schema(_specs())
    schema_b = build_json_schema(_specs()[:2])

    assert schema_hash(schema_a) != schema_hash(schema_b)
