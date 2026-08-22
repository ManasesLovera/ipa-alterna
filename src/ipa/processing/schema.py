"""Compile tag field definitions into JSON Schema for structured extraction.

The schema produced here is handed to the LLM (via `LlmProvider.complete_json`)
and to the validation rules. It wraps every field in the extraction envelope so
the model returns a value, confidence, page and evidence alongside each result,
and it is strict about unknown fields so a hallucinated key is rejected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from ipa.core.enums import FieldType


@dataclass(frozen=True)
class TagFieldSpec:
    """A field definition as understood by the schema compiler.

    Attributes:
        key: snake_case identifier, used as the JSON property name.
        field_type: The field's declared type.
        label: Human-readable label.
        description: Guidance for extraction.
        required: Whether the field must be populated.
        enum_values: Allowed values for `ENUM` fields.
        item_type: Item type for `ARRAY` fields.
        object_schema: A JSON Schema for `OBJECT` fields.
        regex: Pattern a `STRING` field must match.
        is_required: Alias for `required` (backwards-compatible).
    """

    key: str
    field_type: FieldType
    label: str = ""
    description: str = ""
    required: bool = False
    enum_values: list[str] = field(default_factory=list)
    item_type: FieldType | None = None
    object_schema: dict[str, Any] | None = None
    regex: str | None = None

    @property
    def is_required(self) -> bool:
        """Return whether the field is required.

        Returns:
            True when `required` is set.
        """
        return self.required


_JSON_TYPE = Literal[
    "string", "number", "integer", "boolean", "array", "object", "null"
]


def build_json_schema(fields: list[TagFieldSpec]) -> dict[str, Any]:
    """Compile a list of field specs into a Draft 2020-12 JSON Schema.

    The schema is an envelope with a single `fields` object, each field wrapped
    with `value`, `confidence`, `page` and `evidence`. Every `value` is nullable
    so the model may state "not present" rather than hallucinate.

    Args:
        fields: The field definitions.

    Returns:
        A JSON Schema dict conforming to Draft 2020-12.
    """
    required_fields = [f.key for f in fields if f.required]
    field_properties: dict[str, Any] = {}
    for spec in fields:
        value_schema = _value_schema(spec)
        field_schema: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["value"],
            "properties": {
                "value": value_schema,
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "page": {"type": ["integer", "null"]},
                "evidence": {"type": ["string", "null"]},
            },
        }
        field_properties[spec.key] = field_schema

    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["fields"],
        "properties": {
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": required_fields,
                "properties": field_properties,
            }
        },
    }
    return schema


def _value_schema(spec: TagFieldSpec) -> dict[str, Any]:
    """Return the JSON Schema for a field's `value`.

    Args:
        spec: The field definition.

    Returns:
        A JSON Schema dict for the value.
    """
    base = _type_schema(spec)
    base["type"] = [base["type"], "null"]
    return base


def _type_schema(spec: TagFieldSpec) -> dict[str, Any]:
    """Return the non-null JSON Schema for a field's declared type.

    Args:
        spec: The field definition.

    Returns:
        A JSON Schema dict.
    """
    field_type = spec.field_type
    if field_type == FieldType.STRING:
        result: dict[str, Any] = {"type": "string"}
        if spec.regex:
            result["pattern"] = spec.regex
        return result
    if field_type == FieldType.NUMBER:
        return {"type": "number"}
    if field_type == FieldType.INTEGER:
        return {"type": "integer"}
    if field_type == FieldType.BOOLEAN:
        return {"type": "boolean"}
    if field_type == FieldType.DATE:
        return {"type": "string", "format": "date"}
    if field_type == FieldType.DATETIME:
        return {"type": "string", "format": "date-time"}
    if field_type == FieldType.ENUM:
        return {"type": "string", "enum": list(spec.enum_values)}
    if field_type == FieldType.ARRAY:
        item = spec.item_type or FieldType.STRING
        item_spec = TagFieldSpec(key=spec.key, field_type=item)
        return {"type": "array", "items": _type_schema(item_spec)}
    if field_type == FieldType.OBJECT:
        return spec.object_schema or {"type": "object"}
    return {"type": "string"}


def schema_hash(schema: dict[str, Any]) -> str:
    """Return a stable SHA-256 over canonical JSON (sorted keys).

    Args:
        schema: The JSON Schema.

    Returns:
        A 64-character hex digest, stable across key reordering.
    """
    import hashlib

    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
