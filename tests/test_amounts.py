"""Tests for the amount parser."""
from decimal import Decimal

import pytest

from audit_rag.normalization.amounts import parse_amount

DASH = "\u2014"


def test_plain_and_grouped_numbers():
    for raw, expected in (("742.6", "742.6"), ("1,615.9", "1615.9"), ("1,234,567", "1234567")):
        parsed = parse_amount(raw)
        assert parsed.kind == "number" and parsed.value == Decimal(expected), raw
    assert parse_amount("742.6").transformations == ()
    assert "thousands separators removed" in parse_amount("1,615.9").transformations


def test_trailing_zeros_are_preserved():
    assert str(parse_amount("1,615.90").value) == "1615.90"


def test_a_leading_decimal_point_is_accepted():
    assert parse_amount(".5").value == Decimal("0.5")


def test_brackets_mean_negative_and_are_recorded():
    parsed = parse_amount("(1,049.3)")
    assert parsed.value == Decimal("-1049.3")
    assert any("brackets" in t for t in parsed.transformations)


def test_minus_signs():
    assert parse_amount("-5").value == Decimal("-5")
    assert parse_amount("\u22125").value == Decimal("-5")
    assert parse_amount("(0.0)").value == 0  # no negative zero


def test_dashes_are_nil_not_zero():
    for raw in (DASH, "\u2013", "-"):
        parsed = parse_amount(raw)
        assert parsed.kind == "nil" and parsed.value is None, raw
    assert parse_amount("$" + DASH).kind == "nil"


def test_currency_symbol_is_recorded_not_decided():
    parsed = parse_amount("$ 742.6")
    assert parsed.value == Decimal("742.6")
    assert parsed.currency_symbol == "$"
    assert any("currency symbol" in t for t in parsed.transformations)
    assert parse_amount("$117,359").value == Decimal("117359")


def test_percent_stays_a_percentage():
    parsed = parse_amount("12.5%")
    assert parsed.kind == "percent" and parsed.value == Decimal("12.5")
    assert any("not a fraction" in t for t in parsed.transformations)


def test_a_possible_decimal_comma_is_ambiguous_and_has_no_value():
    for raw in ("12,5", "1.234,56", "1,23"):
        parsed = parse_amount(raw)
        assert parsed.kind == "ambiguous" and parsed.value is None, raw
        assert parsed.warnings, raw


def test_indian_digit_grouping():
    assert parse_amount("1,23,456").kind == "ambiguous"  # not valid US grouping
    assert parse_amount("1,23,456", "indian").value == Decimal("123456")
    assert parse_amount("12,34,567.89", "indian").value == Decimal("1234567.89")
    assert parse_amount("12,345", "indian").value == Decimal("12345")  # valid in both
    assert parse_amount("1,234,567").value == Decimal("1234567")
    assert parse_amount("1,234,567", "indian").kind == "ambiguous"


def test_text_is_not_a_number():
    for raw in ("Audited", "N/A", "", "5-", "2023-03-31"):
        parsed = parse_amount(raw)
        assert parsed.kind == "text" and parsed.value is None, repr(raw)


def test_unbalanced_brackets_and_double_negatives_are_ambiguous():
    for raw in ("(12.3", "12.3)", "(-5)", "(\u22125)"):
        parsed = parse_amount(raw)
        assert parsed.kind == "ambiguous" and parsed.value is None, raw


def test_the_original_text_is_always_kept():
    for raw in ("(1,049.3)", "$117,359", DASH, "12,5", "Audited"):
        assert parse_amount(raw).raw == raw


def test_unknown_number_format_is_refused():
    with pytest.raises(ValueError):
        parse_amount("1", "european")
