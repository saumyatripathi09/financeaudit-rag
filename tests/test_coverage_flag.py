from dataclasses import replace

from audit_rag.audit.checks import run_checks
from test_injection import balance_page, rec


def flags(records):
    return [c for c in run_checks(records) if c.status == "not_testable"]


def test_balance_sheet_missing_total_assets_is_reported_as_not_testable():
    records = [r for r in balance_page() if r.label != "Total assets"]
    found = flags(records)
    assert len(found) == 1
    assert found[0].rule_id == "balance_equation" and found[0].severity == "medium"
    assert "total_assets" in found[0].message
    assert found[0].page_index == 1


def test_complete_balance_sheet_has_no_not_testable_flag():
    assert flags(balance_page()) == []


def test_other_statement_types_are_not_flagged():
    records = [replace(r, statement_type="income_statement")
               for r in balance_page() if r.label != "Total assets"]
    assert flags(records) == []


def test_totals_present_but_in_different_columns_are_reported():
    records = [
        rec(0, 0, "Total assets", "total_assets", "1000"),
        rec(1, 1, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
    ]
    found = [c for c in flags(records) if c.rule_id == "balance_equation"]
    assert len(found) == 1 and "same column" in found[0].message


def test_flag_is_not_an_arithmetic_result():
    records = [r for r in balance_page() if r.label != "Total assets"]
    statuses = {c.status for c in run_checks(records)}
    assert "fail" not in statuses and "not_testable" in statuses
