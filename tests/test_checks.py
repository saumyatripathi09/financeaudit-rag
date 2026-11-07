import sqlite3
from decimal import Decimal as D

import pytest

from audit_rag.audit.checks import run_checks
from audit_rag.audit.checks_repo import (ensure_schema, load_checks, save_checks, set_review)
from audit_rag.normalization.records import NormalizedRecord


def rec(label, canon, text, order, pos=0, page=3, **kw):
    value = D(text.replace(",", ""))
    base = dict(
        document_id="d1", row_order=order, cell_position=pos, page_index=page,
        statement_type="balance_sheet", label=label, label_canonical=canon,
        column_label=str(2022 - pos), raw_text=text, kind="number", displayed_value=value,
        scaled_value=value * 1000, scale_status="scaled", multiplier=D(1000), reasons=(),
        method="pdf_text", confidence=None, row_warnings=())
    base.update(kw)
    return NormalizedRecord(**base)


def by_rule(checks, rule):
    return [c for c in checks if c.rule_id == rule]


def bs(ta="100.0", tle="100.0", tl="60.0", te="40.0", tca="30.0", tcl="20.0"):
    return [rec("Total current assets", "total_current_assets", tca, 0),
            rec("Total assets", "total_assets", ta, 1),
            rec("Total current liabilities", "total_current_liabilities", tcl, 2),
            rec("Total liabilities", "total_liabilities", tl, 3),
            rec("Total equity", "total_equity", te, 4),
            rec("Total liabilities and equity", "total_liabilities_and_equity", tle, 5)]


def test_clean_balance_sheet_passes_everything():
    checks = run_checks(bs())
    assert {c.status for c in checks} == {"pass"}
    assert {c.rule_id for c in checks} == {"balance_equation", "liabilities_plus_equity"}


def test_equation_failure_is_high_with_evidence_and_difference():
    c = by_rule(run_checks(bs(ta="105.0")), "balance_equation")[0]
    assert (c.status, c.severity, c.difference) == ("fail", "high", D("5.0"))
    assert [e.raw_text for e in c.evidence] == ["105.0", "100.0"]


def test_rounding_is_allowed_only_for_sums_not_for_totals():
    # 60.0 + 39.9 vs 100.0: off by 0.1, within rounding of two operands at 0.1 units
    assert by_rule(run_checks(bs(te="39.9")), "liabilities_plus_equity")[0].status == "rounding"
    assert by_rule(run_checks(bs(te="39.0")), "liabilities_plus_equity")[0].status == "fail"
    # the two totals must match exactly
    assert by_rule(run_checks(bs(ta="100.1")), "balance_equation")[0].status == "fail"


def test_subtotal_larger_than_total_is_flagged():
    c = by_rule(run_checks(bs(tca="120.0")), "current_assets_within_total")
    assert c and c[0].status == "fail" and c[0].severity == "high"


def test_missing_figure_means_no_check_not_a_pass():
    got = run_checks([rec("Total assets", "total_assets", "100.0", 0)])
    assert not [c for c in got if c.status in ("pass", "rounding", "fail")]
    assert [c.status for c in got] == ["not_testable"]


def test_gross_profit():
    rows = [rec("Revenue", "revenue", "500", 0), rec("Cost of sales", "cost_of_revenue", "300", 1),
            rec("Gross profit", "gross_profit", "200", 2)]
    assert run_checks(rows)[0].status == "pass"
    rows[2] = rec("Gross profit", "gross_profit", "210", 2)
    assert run_checks(rows)[0].status == "fail"


def test_columns_are_checked_separately():
    rows = bs() + [rec("Total assets", "total_assets", "90.0", 1, pos=1),
                   rec("Total liabilities and equity", "total_liabilities_and_equity", "95.0", 5, pos=1)]
    eq = by_rule(run_checks(rows), "balance_equation")
    assert [(c.column, c.status) for c in eq] == [("2022", "pass"), ("2021", "fail")]


def test_quality_flags():
    rows = [rec("Cash", None, "5", 0, method="ocr", confidence=0.5),
            rec("Other", None, "6", 1, scale_status="scale_unknown", scaled_value=None,
                row_warnings=("row may be incomplete",)),
            rec("Fine", None, "7", 2, method="ocr", confidence=0.95)]
    got = {c.rule_id: c for c in run_checks(rows)}
    got = {k: v for k, v in got.items() if v.status != "not_testable"}
    assert set(got) == {"low_ocr_confidence", "scale_not_determined", "extraction_warnings"}
    assert len(got["low_ocr_confidence"].evidence) == 1


def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO documents VALUES ('d1')")
    ensure_schema(conn)
    return conn


def test_round_trip_and_review_survives_rerun():
    conn = db()
    checks = run_checks(bs(ta="105.0"))
    with conn:
        save_checks(conn, "d1", checks)
    stored = load_checks(conn, "d1")
    assert [s["check"] for s in stored] == checks
    failing = [s for s in stored if s["check"].status == "fail"][0]
    with conn:
        set_review(conn, failing["check_id"], "dismissed", "known restatement")
    with conn:  # re-run produces the same checks again
        save_checks(conn, "d1", run_checks(bs(ta="105.0")))
    again = [s for s in load_checks(conn, "d1") if s["check"].status == "fail"][0]
    assert (again["review_status"], again["review_note"]) == ("dismissed", "known restatement")
    with conn:  # a changed number is a different check and starts open
        save_checks(conn, "d1", run_checks(bs(ta="106.0")))
    changed = [s for s in load_checks(conn, "d1") if s["check"].status == "fail"][0]
    assert changed["review_status"] == "open"


def test_bad_review_status_and_unknown_check():
    conn = db()
    with pytest.raises(ValueError):
        set_review(conn, 1, "maybe")
    with pytest.raises(ValueError):
        set_review(conn, 999, "confirmed")


def test_audit_document_end_to_end_and_requires_records():
    from audit_rag.audit.runner import audit_document
    from audit_rag.normalization.records_repo import save_records
    conn = db()
    with pytest.raises(ValueError):
        audit_document(conn, "d1")
    with conn:
        save_records(conn, "d1", bs(ta="105.0"))
    with conn:
        checks = audit_document(conn, "d1")
    assert any(c.status == "fail" for c in checks)
    assert len(load_checks(conn, "d1")) == len(checks)


def test_noncontrolling_interest_explains_the_gap_when_shown():
    rows = [rec("Total assets", "total_assets", "100.0", 0),
            rec("Total liabilities", "total_liabilities", "60.0", 1),
            rec("Total stockholders equity", "total_parent_equity", "39.0", 2),
            rec("Noncontrolling interests", "noncontrolling_interest", "1.0", 3),
            rec("Total liabilities and equity", "total_liabilities_and_equity", "100.0", 4)]
    c = by_rule(run_checks(rows), "liabilities_plus_equity")[0]
    assert c.status == "pass"
    # without the NCI row the same page is a failure to review, not a silent pass
    c = by_rule(run_checks(rows[:3] + rows[4:]), "liabilities_plus_equity")[0]
    assert c.status == "fail"


def test_redeemable_interest_is_added_and_total_equity_is_preferred():
    rows = [rec("Total liabilities", "total_liabilities", "60.0", 0),
            rec("Redeemable interest", "redeemable_interest", "10.0", 1),
            rec("Total stockholders equity", "total_parent_equity", "29.0", 2),
            rec("Total equity", "total_equity", "30.0", 3),
            rec("Total liabilities and equity", "total_liabilities_and_equity", "100.0", 4)]
    assert by_rule(run_checks(rows), "liabilities_plus_equity")[0].status == "pass"


def test_gross_profit_with_cost_in_parentheses():
    rows = [rec("Net sales", "revenue", "14,693", 0),
            rec("Cost of sales", "cost_of_revenue", "-11,968", 1),
            rec("Gross profit", "gross_profit", "2,725", 2)]
    assert run_checks(rows)[0].status == "pass"


def test_subtotal_that_already_includes_nci_is_not_double_counted():
    # Amcor/CVS style: "Total shareholders equity" (100 - 60 = 40) already includes the 1.0 NCI
    rows = [rec("Total liabilities", "total_liabilities", "60.0", 0),
            rec("Noncontrolling interests", "noncontrolling_interest", "1.0", 1),
            rec("Total shareholders equity", "total_parent_equity", "40.0", 2),
            rec("Total liabilities and equity", "total_liabilities_and_equity", "100.0", 3)]
    c = by_rule(run_checks(rows), "liabilities_plus_equity")[0]
    assert c.status == "pass" and "already including" in c.message
    # parent-only reading still works
    rows[2] = rec("Total shareholders equity", "total_parent_equity", "39.0", 2)
    c = by_rule(run_checks(rows), "liabilities_plus_equity")[0]
    assert c.status == "pass" and "already including" not in c.message
    # and a genuine gap fails under both readings
    rows[2] = rec("Total shareholders equity", "total_parent_equity", "30.0", 2)
    assert by_rule(run_checks(rows), "liabilities_plus_equity")[0].status == "fail"


def test_other_cost_line_between_cost_and_gross_profit():
    rows = [rec("Revenue", "revenue", "39,528", 0), rec("Cost of goods sold", "cost_of_revenue", "30,334", 1),
            rec("Restructuring charges cost of goods sold", None, "3", 2),
            rec("Gross profit", "gross_profit", "9,191", 3)]
    c = by_rule(run_checks(rows), "gross_profit")[0]
    assert c.status == "pass" and "between" in c.message
    assert [e.raw_text for e in c.evidence] == ["39,528", "30,334", "3", "9,191"]
    rows[3] = rec("Gross profit", "gross_profit", "9,000", 3)  # the extra line does not explain it
    c = by_rule(run_checks(rows), "gross_profit")[0]
    assert c.status == "fail" and "between" not in c.message
