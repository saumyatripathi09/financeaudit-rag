from dataclasses import replace

from audit_rag.audit.checks import run_checks
from test_checks import bs, by_rule, rec


def flags(records, rule):
    return [c for c in by_rule(run_checks(records), rule) if c.status == "not_testable"]


def without(records, label):
    return [r for r in records if r.label != label]


def test_complete_balance_sheet_has_no_partial_flags():
    assert [c for c in run_checks(bs()) if c.status == "not_testable"] == []


def test_dropping_total_liabilities_or_total_equity_is_now_reported():
    for label, missing in (("Total liabilities", "total_liabilities"), ("Total equity", "total equity")):
        got = flags(without(bs(), label), "liabilities_plus_equity")
        assert len(got) == 1 and missing in got[0].message and got[0].column == "2022"
    assert by_rule(run_checks(without(bs(), "Total equity")), "liabilities_plus_equity")[0].status == "not_testable"


def test_dropping_total_assets_is_still_reported_by_the_page_level_flag():
    got = [c for c in run_checks(without(bs(), "Total assets")) if c.status == "not_testable"]
    assert {c.rule_id for c in got} == {"balance_equation", "current_assets_within_total"}


def test_dropping_total_liabilities_also_reports_the_current_liabilities_check():
    got = flags(without(bs(), "Total liabilities"), "current_liabilities_within_total")
    assert len(got) == 1 and "total_liabilities" in got[0].message


def test_gross_profit_with_a_missing_input_is_reported_but_no_gross_profit_is_not():
    rows = [rec("Revenue", "revenue", "500", 0, statement_type="income_statement"),
            rec("Cost of sales", "cost_of_revenue", "300", 1, statement_type="income_statement"),
            rec("Gross profit", "gross_profit", "200", 2, statement_type="income_statement")]
    assert flags(without(rows, "Revenue"), "gross_profit")
    assert flags(without(rows, "Cost of sales"), "gross_profit")
    assert flags(without(rows, "Gross profit"), "gross_profit") == []  # not reported: cannot tell


def test_other_statement_types_are_not_flagged_for_balance_sheet_rules():
    rows = [replace(r, statement_type="income_statement") for r in without(bs(), "Total equity")]
    assert flags(rows, "liabilities_plus_equity") == []


def test_columns_are_judged_separately():
    rows = bs() + [rec("Total liabilities and equity", "total_liabilities_and_equity", "95.0", 5, pos=1)]
    got = flags(rows, "liabilities_plus_equity")
    assert [c.column for c in got] == ["2021"]


def test_gross_profit_line_on_a_page_that_is_not_an_income_statement_is_not_flagged():
    # an MD&A segment table can show "Gross profit" without revenue or cost lines
    rows = [rec("Gross profit", "gross_profit", "200", 0, statement_type="unknown")]
    assert flags(rows, "gross_profit") == []
