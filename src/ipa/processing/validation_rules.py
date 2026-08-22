"""Pure, side-effect-free field validation rules.

Each validator takes a value (and sometimes a spec) and returns a list of error
messages — empty when the value passes. They are deliberately free of any I/O or
state so they can be unit-tested in isolation and reused by T13.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


def check_required(value: Any, *, required: bool) -> list[str]:
    """Check that a required field is present and non-null.

    Args:
        value: The extracted value.
        required: Whether the field must be populated.

    Returns:
        A list of error messages (empty when valid).
    """
    if required and (value is None or value == "" or value == []):
        return ["field is required"]
    return []


def check_regex(value: Any, *, pattern: str | None) -> list[str]:
    """Check a string value against a regex pattern.

    Args:
        value: The extracted value.
        pattern: The pattern to match, or None to skip.

    Returns:
        A list of error messages (empty when valid).
    """
    if pattern is None or value is None:
        return []
    try:
        if not re.fullmatch(pattern, str(value)):
            return [f"does not match pattern {pattern}"]
    except re.error:
        return []
    return []


def check_range(
    value: Any, *, minimum: float | None = None, maximum: float | None = None
) -> list[str]:
    """Check a numeric value against inclusive bounds.

    Args:
        value: The extracted value.
        minimum: Lower bound, or None to skip.
        maximum: Upper bound, or None to skip.

    Returns:
        A list of error messages (empty when valid).
    """
    if value is None:
        return []
    try:
        number = float(value)
    except (TypeError, ValueError):
        return []
    errors: list[str] = []
    if minimum is not None and number < minimum:
        errors.append(f"must be >= {minimum}")
    if maximum is not None and number > maximum:
        errors.append(f"must be <= {maximum}")
    return errors


def check_length(
    value: Any, *, min_length: int | None = None, max_length: int | None = None
) -> list[str]:
    """Check the length of a string or collection.

    Args:
        value: The extracted value.
        min_length: Minimum length, or None to skip.
        max_length: Maximum length, or None to skip.

    Returns:
        A list of error messages (empty when valid).
    """
    if value is None:
        return []
    try:
        length = len(value)
    except TypeError:
        return []
    errors: list[str] = []
    if min_length is not None and length < min_length:
        errors.append(f"length must be >= {min_length}")
    if max_length is not None and length > max_length:
        errors.append(f"length must be <= {max_length}")
    return errors


def check_enum(value: Any, *, allowed: list[str]) -> list[str]:
    """Check a value against an allowed set.

    Args:
        value: The extracted value.
        allowed: The permitted values.

    Returns:
        A list of error messages (empty when valid).
    """
    if value is None:
        return []
    if str(value) not in [str(item) for item in allowed]:
        return [f"must be one of {allowed}"]
    return []


def check_date_parseable(value: Any) -> list[str]:
    """Check that a value parses as a date or datetime.

    Args:
        value: The extracted value.

    Returns:
        A list of error messages (empty when valid).
    """
    if value is None:
        return []
    if isinstance(value, (date, datetime)):
        return []
    if isinstance(value, str):
        parsed = _parse_date(value)
        if parsed is None:
            return ["not a parseable date"]
        return []
    return ["value is not a date"]


def _parse_date(value: str) -> date | None:
    """Attempt to parse a string as an ISO or common locale date.

    Args:
        value: The date string.

    Returns:
        A `date` when parseable, else None.
    """
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None
