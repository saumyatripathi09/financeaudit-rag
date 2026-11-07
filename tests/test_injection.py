import random
from decimal import Decimal

import pytest

from audit_rag.audit.injection import (
    CORRUPTIONS, corrupt_value, inject_page, role_of, tally,
)
from audit_rag.normalization.records import NormalizedRecord


def rec(order, pos, label, canonical, value, doc="D", page=1, multiplier=None):
    return NormalizedRecord(
        document_id=doc, row_order=order, cell_position=pos, page_index=page,
        statement_type="balance_sheet", label=label, label_canonical=canonical,
        column_label="2023", raw_text=str(value), kind="number",
        displayed_value=Decimal(str(value)), scaled_value=None, scale_status="scaled",
        multiplier=multiplier, reasons=(), method="embedded_text", confidence=None,
        row_warnings=(),
    )


def balance_page():
    return [
        rec(0, 0, "Total assets", "total_assets", "1000"),
        rec(1, 0, "Total liabilities", "total_liabilities", "600"),
        rec(2, 0, "Total equity", "total_equity", "400"),
        rec(3, 0, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
        rec(4, 0, "Cash", "cash", "55"),  # taken by no rule
    ]


@pytest.mark.parametrize("kind", ["digit", "transpose"])
def test_digit_corruptions_always_change_the_value(kind):
    for seed in range(200):
        rng = random.Random(seed)
        value = Decimal("1615.9")
        new = corrupt_value(value, kind, rng)
        assert new is not None and new != value
        assert len(new.as_tuple().digits) == len(value.as_tuple().digits)
        assert new.as_tuple().exponent == value.as_tuple().exponent


def test_transpose_not_applicable_when_all_digits_equal():
    assert corrupt_value(Decimal("111"), "transpose", random.Random(1)) is None


def test_zero_has_no_corruption():
    for kind in CORRUPTIONS:
        assert corrupt_value(Decimal("0"), kind, random.Random(1)) is None


def test_scale_and_sign():
    rng = random.Random(1)
    assert corrupt_value(Decimal("5"), "times1000", rng) == Decimal("5000")
    assert corrupt_value(Decimal("5000"), "div1000", rng) == Decimal("5")
    assert corrupt_value(Decimal("5"), "sign", rng) == Decimal("-5")
    assert corrupt_value(Decimal("-5"), "sign", rng) == Decimal("5")


def test_same_seed_same_corruption():
    a = corrupt_value(Decimal("123456"), "digit", random.Random("x"))
    b = corrupt_value(Decimal("123456"), "digit", random.Random("x"))
    assert a == b


def test_role():
    assert role_of(rec(0, 0, "Total assets", "total_assets", 1)) == "total"
    assert role_of(rec(0, 0, "Gross profit", "gross_profit", 1)) == "total"
    assert role_of(rec(0, 0, "Revenue", "revenue", 1)) == "component"
    assert role_of(rec(0, 0, "Other", None, 1)) == "component"


def test_clean_page_is_not_changed_and_uncovered_value_is_not_injected():
    records = balance_page()
    before = list(records)
    result = inject_page(records)
    assert records == before  # originals untouched
    assert result.numeric_values == 5
    assert result.covered_values == 4  # cash (row 4) is in no check
    assert {i.row_order for i in result.injections} == {0, 1, 2, 3}


def test_balance_page_corruptions_are_caught():
    result = inject_page(balance_page())
    applicable = [i for i in result.injections if i.outcome != "not_applicable"]
    assert applicable
    # every digit, scale and sign change on a verified total must be flagged
    for i in applicable:
        assert i.outcome == "flagged", (i.label, i.corruption, i.outcome)
    assert {"balance_equation", "liabilities_plus_equity"} & {
        r for i in applicable for r in i.rules}


def test_sign_flip_on_cost_of_revenue_is_a_known_silent_miss():
    # The gross profit rule uses abs(cost), so a lost sign on cost is invisible to it.
    records = [
        rec(0, 0, "Revenue", "revenue", "1000"),
        rec(1, 0, "Cost of revenue", "cost_of_revenue", "600"),
        rec(2, 0, "Gross profit", "gross_profit", "400"),
    ]
    result = inject_page(records)
    sign = [i for i in result.injections if i.label == "Cost of revenue" and i.corruption == "sign"]
    assert len(sign) == 1 and sign[0].outcome == "passed_unnoticed"
    digit = [i for i in result.injections if i.label == "Cost of revenue" and i.corruption == "digit"]
    assert digit[0].outcome == "flagged"


def test_change_inside_rounding_allowance_is_reported_as_absorbed(monkeypatch):
    # One decimal shown: allowance on gross profit is 0.5 * 0.1 * 2 = 0.1, so 4.0 -> 4.1 is rounding.
    import audit_rag.audit.injection as injection
    monkeypatch.setattr(injection, "CORRUPTIONS", ("digit",))
    monkeypatch.setattr(
        injection, "corrupt_value",
        lambda value, kind, rng: Decimal("4.1") if value == Decimal("4.0") else Decimal("99.9"))
    records = [
        rec(0, 0, "Revenue", "revenue", "10.0"),
        rec(1, 0, "Cost of revenue", "cost_of_revenue", "6.0"),
        rec(2, 0, "Gross profit", "gross_profit", "4.0"),
    ]
    by_label = {i.label: i.outcome for i in injection.inject_page(records).injections}
    assert by_label["Gross profit"] == "absorbed_as_rounding"
    assert by_label["Revenue"] == "flagged"


def test_page_without_checks_has_nothing_covered():
    result = inject_page([rec(0, 0, "Cash", "cash", "5")])
    assert result.covered_values == 0 and result.injections == []


def test_scaled_value_follows_the_corrupted_value():
    records = [
        rec(0, 0, "Total assets", "total_assets", "1000", multiplier=Decimal(1000)),
        rec(1, 0, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
    ]
    result = inject_page(records)
    assert result.covered_values == 2 and all(
        i.outcome == "flagged" for i in result.injections if i.outcome != "not_applicable")


def test_tally_groups_outcomes():
    result = inject_page(balance_page())
    table = tally(result.injections, lambda x: x.corruption)
    assert set(table) == set(CORRUPTIONS)
    assert sum(sum(c.values()) for c in table.values()) == len(result.injections)


def test_failure_in_another_column_with_the_same_label_does_not_hide_a_new_failure():
    # Two columns share the label "2023"; the second is already out of balance on the clean page.
    records = [
        rec(0, 0, "Total assets", "total_assets", "1000"),
        rec(1, 0, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
        rec(0, 1, "Total assets", "total_assets", "500"),
        rec(1, 1, "Total liabilities and equity", "total_liabilities_and_equity", "700"),
    ]
    result = inject_page(records)
    applicable = [i for i in result.injections if i.outcome != "not_applicable"]
    assert applicable and {i.cell_position for i in applicable} == {0}
    assert all(i.outcome == "flagged" for i in applicable), [(i.corruption, i.outcome) for i in applicable]


def test_change_that_cancels_the_filings_own_rounding_is_not_a_silent_miss(monkeypatch):
    # 600 + 399 = 999 against a printed 1000: the clean page is off by one (rounding).
    # Changing 600 to 601 makes the identity exact, so no rule could object.
    import audit_rag.audit.injection as injection
    monkeypatch.setattr(injection, "CORRUPTIONS", ("digit",))
    monkeypatch.setattr(
        injection, "corrupt_value",
        lambda value, kind, rng: Decimal("601") if value == Decimal("600") else Decimal("99999"))
    records = [
        rec(0, 0, "Total assets", "total_assets", "1000"),
        rec(1, 0, "Total liabilities", "total_liabilities", "600"),
        rec(2, 0, "Total equity", "total_equity", "399"),
        rec(3, 0, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
    ]
    by_label = {i.label: i.outcome for i in injection.inject_page(records).injections}
    assert by_label["Total liabilities"] == "cancelled_rounding"
    assert by_label["Total assets"] == "flagged"
