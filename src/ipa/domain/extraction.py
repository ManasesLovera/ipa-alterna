"""Extraction domain: coercion, validation, confidence and classification.

Pure, side-effect-free helpers that turn raw model output into validated
`ExtractedField` objects and a document confidence. `score_document` is imported
by T13, so it stays here and is separately testable. `classify` picks a tag for
an unassigned document using the LLM.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ipa.contracts.models import ExtractedField
from ipa.core.enums import FieldType
from ipa.processing.validation_rules import (
    check_date_parseable,
    check_enum,
    check_length,
    check_range,
    check_regex,
    check_required,
)

# Regexes used by number coercion.
_CURRENCY_RE = re.compile(r"[^\d.,eE+-]")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%b %d, %Y",
    "%B %d, %Y",
)


class FieldSpec:
    """A minimal field spec used by extraction post-processing.

    Attributes:
        key: Field key.
        field_type: Declared type.
        required: Whether the field must be populated.
        regex: Optional pattern.
        min_value: Optional lower bound.
        max_value: Optional upper bound.
        min_length: Optional minimum length.
        max_length: Optional maximum length.
        enum_values: Allowed values for enum fields.
    """

    def __init__(
        self,
        *,
        key: str,
        field_type: FieldType,
        required: bool = False,
        regex: str | None = None,
        min_value: float | None = None,
        max_value: float | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        enum_values: list[str] | None = None,
    ) -> None:
        """Initialise the spec.

        Args:
            key: Field key.
            field_type: Declared type.
            required: Whether the field is required.
            regex: Optional pattern.
            min_value: Optional lower bound.
            max_value: Optional upper bound.
            min_length: Optional minimum length.
            max_length: Optional maximum length.
            enum_values: Allowed values for enum fields.
        """
        self.key = key
        self.field_type = field_type
        self.required = required
        self.regex = regex
        self.min_value = min_value
        self.max_value = max_value
        self.min_length = min_length
        self.max_length = max_length
        self.enum_values = enum_values or []


def coerce_value(spec: FieldSpec, raw: Any) -> tuple[Any, list[str]]:
    """Coerce a raw model value to the declared type.

    Coercion failure sets the value to None and returns an error message; it
    never raises.

    Args:
        spec: The field spec.
        raw: The value returned by the model.

    Returns:
        A tuple of `(coerced_value, errors)`.
    """
    if raw is None:
        return None, []
    errors: list[str] = []
    try:
        if spec.field_type in (FieldType.NUMBER, FieldType.INTEGER):
            value = _coerce_number(raw)
        elif spec.field_type in (FieldType.DATE, FieldType.DATETIME):
            value = _coerce_date(raw)
        elif spec.field_type == FieldType.BOOLEAN:
            value = _coerce_boolean(raw)
        elif spec.field_type == FieldType.STRING:
            value = str(raw).strip()
        else:
            value = raw
    except ValueError:
        value = None
        errors.append("value could not be coerced to its declared type")
    return value, errors


def validate_field(spec: FieldSpec, value: Any, confidence: float) -> list[str]:
    """Run every applicable validation rule on a coerced value.

    Args:
        spec: The field spec.
        value: The coerced value.
        confidence: The model-reported confidence (for adjustment decision).

    Returns:
        A list of validation error messages.
    """
    errors = list(check_required(value, required=spec.required))
    errors += check_regex(value, pattern=spec.regex)
    errors += check_range(value, minimum=spec.min_value, maximum=spec.max_value)
    errors += check_length(
        value, min_length=spec.min_length, max_length=spec.max_length
    )
    if spec.enum_values:
        errors += check_enum(value, allowed=spec.enum_values)
    if spec.field_type in (FieldType.DATE, FieldType.DATETIME):
        errors += check_date_parseable(value)
    return errors


def adjust_confidence(spec: FieldSpec, value: Any, confidence: float, valid: bool) -> float:
    """Clamp confidence based on validation outcomes.

    A field that fails validation or is missing-but-required never reports high
    confidence — the model cannot self-report a score we can prove wrong.

    Args:
        spec: The field spec.
        value: The coerced value.
        confidence: The model-reported confidence.
        valid: Whether the field passed validation.

    Returns:
        The adjusted confidence in [0, 1].
    """
    if value is None and spec.required:
        return 0.0
    if not valid:
        return min(confidence, 0.4)
    return confidence


def score_document(
    fields: list[ExtractedField], required_keys: set[str] | None = None
) -> float:
    """Compute a weighted mean document confidence from field confidences.

    Required fields are weighted 2.0, optional 1.0. A missing or invalid
    required field caps the score at 0.5. With no fields the score is 1.0.

    Args:
        fields: The extracted, validated fields.
        required_keys: Keys considered required; when None, a field is treated
            as required if it carries a "field is required" validation error.

    Returns:
        The document confidence in [0, 1].
    """
    if not fields:
        return 1.0
    total_weight = 0.0
    weighted = 0.0
    any_required_bad = False
    for field in fields:
        weight = 2.0 if _is_required(field, required_keys) else 1.0
        total_weight += weight
        weighted += field.confidence * weight
        if _is_required(field, required_keys) and (field.value is None or not field.valid):
            any_required_bad = True
    score = weighted / total_weight if total_weight else 0.0
    if any_required_bad:
        score = min(score, 0.5)
    return score


def _coerce_number(raw: Any) -> Any:
    """Coerce a value to a number, stripping currency and separators.

    Args:
        raw: The raw value.

    Returns:
        An int for integer fields, a float otherwise.

    Raises:
        ValueError: If the value cannot be parsed as a number.
    """
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return raw
    cleaned = _CURRENCY_RE.sub("", str(raw))
    cleaned = _THOUSANDS_RE.sub("", cleaned)
    if not cleaned:
        raise ValueError("empty number")
    if "." in cleaned:
        return float(cleaned)
    return int(cleaned)


def _coerce_date(raw: Any) -> Any:
    """Coerce a value to a date string, trying ISO then locale formats.

    Args:
        raw: The raw value.

    Returns:
        A normalised ISO date string.

    Raises:
        ValueError: If the value cannot be parsed as a date.
    """
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError("not a date")


def _coerce_boolean(raw: Any) -> bool:
    """Coerce a value to a boolean.

    Args:
        raw: The raw value.

    Returns:
        True for truthy representations.

    Raises:
        ValueError: If the value is not recognisable as a boolean.
    """
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "yes", "1", "y"):
        return True
    if text in ("false", "no", "0", "n"):
        return False
    raise ValueError("not a boolean")


def _is_required(field: ExtractedField, required_keys: set[str] | None) -> bool:
    """Report whether an extracted field maps to a required spec.

    Args:
        field: The extracted field.
        required_keys: Keys considered required, when provided.

    Returns:
        True when the field is required.
    """
    if required_keys is not None:
        return field.key in required_keys
    return "field is required" in field.validation_errors
