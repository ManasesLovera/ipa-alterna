"""Pure field validation rules."""

from __future__ import annotations

from ipa.processing.validation_rules import (
    check_date_parseable,
    check_enum,
    check_length,
    check_range,
    check_regex,
    check_required,
)


def test_check_required() -> None:
    assert check_required("x", required=True) == []
    assert check_required(None, required=True) != []
    assert check_required("", required=True) != []
    assert check_required(None, required=False) == []


def test_check_regex() -> None:
    assert check_regex("ACME-001", pattern=r"[A-Z]+-\d+") == []
    assert check_regex("bad", pattern=r"[A-Z]+-\d+") != []
    assert check_regex(None, pattern=r"[A-Z]+") == []


def test_check_range() -> None:
    assert check_range(5, minimum=0, maximum=10) == []
    assert check_range(15, minimum=0, maximum=10) != []
    assert check_range(-1, minimum=0) != []
    assert check_range("not-a-number", minimum=0) == []


def test_check_length() -> None:
    assert check_length("hello", min_length=2, max_length=10) == []
    assert check_length("hello", min_length=10) != []
    assert check_length("hello", max_length=3) != []


def test_check_enum() -> None:
    assert check_enum("USD", allowed=["USD", "EUR"]) == []
    assert check_enum("GBP", allowed=["USD", "EUR"]) != []
    assert check_enum(None, allowed=["USD"]) == []


def test_check_date_parseable() -> None:
    assert check_date_parseable("2024-12-31") == []
    assert check_date_parseable("31/12/2024") == []
    assert check_date_parseable("Dec 31, 2024") == []
    assert check_date_parseable("not a date") != []
    assert check_date_parseable(None) == []
