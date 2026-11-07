import sqlite3
from decimal import Decimal as D

from audit_rag.audit.checks import run_checks
from audit_rag.audit.checks_repo import ensure_schema, load_checks, save_checks
from audit_rag.audit.reconcile import reconcile
from audit_rag.normalization.records import NormalizedRecord

ENT = lambda doc: doc.split("_")[0]  # noqa: E731


def rec(doc, text, mult=1000000, col="2022", canon="total_assets", stmt="balance_sheet", **kw):
    v = D(text.replace(",", ""))
    base = dict(
        document_id=doc, row_order=0, cell_position=0, page_index=3, statement_type=stmt,
        label="Total assets", label_canonical=canon, column_label=col, raw_text=text, kind="number",
        displayed_value=v, scaled_value=v * mult, scale_status="scaled", multiplier=D(mult),
        reasons=(), method="pdf_text", confidence=None, row_warnings=())
    base.update(kw)
    return NormalizedRecord(**base)


def test_same_figure_in_two_filings_passes_even_with_different_scales():
    c = reconcile([rec("ACME_2023_10K", "1,500"), rec("ACME_2022_10K", "1.5", mult=1000000000)], ENT)
    assert len(c) == 1 and c[0].status == "pass"
    assert {e.document_id for e in c[0].evidence} == {"ACME_2023_10K", "ACME_2022_10K"}


def test_restated_figure_is_flagged_for_review_with_the_difference():
    c = reconcile([rec("ACME_2022_10K", "1,500"), rec("ACME_2023_10K", "1,520")], ENT)[0]
    assert (c.status, c.severity, c.difference) == ("fail", "medium", D("20000000"))
    assert "restatement" in c.message


def test_difference_within_rounding_of_the_coarser_figure_is_rounding():
    # 1,500 (whole millions) vs 1,500.4: the 0.4 is smaller than half of the coarser unit
    c = reconcile([rec("ACME_2022_10K", "1,500"), rec("ACME_2023_10K", "1,500.4")], ENT)
    assert c[0].status == "rounding"
    c = reconcile([rec("ACME_2022_10K", "1,500"), rec("ACME_2023_10K", "1,501.4")], ENT)
    assert c[0].status == "fail"


def test_never_compares_across_entities_columns_statements_or_unsafe_scale():
    base = rec("ACME_2022_10K", "100")
    others = [rec("OTHER_2022_10K", "999"), rec("ACME_2023_10K", "999", col="2021"),
              rec("ACME_2023_10K", "999", stmt="income_statement"),
              rec("ACME_2023_10K", "999", scale_status="needs_review", scaled_value=None),
              rec("ACME_2023_10K", "999", canon=None)]
    assert reconcile([base] + others, ENT) == []


def test_same_document_is_not_compared_with_itself():
    assert reconcile([rec("ACME_2022_10K", "100"), rec("ACME_2022_10K", "999", row_order=5)], ENT) == []


def test_cross_and_single_document_checks_are_saved_separately():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO documents VALUES ('ACME_2022_10K')")
    ensure_schema(conn)
    cross = reconcile([rec("ACME_2022_10K", "1,500"), rec("ACME_2023_10K", "1,520")], ENT)
    single = run_checks([rec("ACME_2022_10K", "5", row_warnings=("w",), statement_type="unknown")])
    with conn:
        save_checks(conn, "ACME_2022_10K", single)
        save_checks(conn, "ACME_2022_10K", cross, cross_document=True)
    with conn:  # re-running one kind leaves the other alone
        save_checks(conn, "ACME_2022_10K", single)
    rules = sorted(c["check"].rule_id for c in load_checks(conn, "ACME_2022_10K"))
    assert rules == ["cross_document", "extraction_warnings"]


def test_documents_of_different_period_basis_are_never_compared():
    # annual 2022 vs a quarterly release whose "2022" column is one quarter
    annual = rec("ACME_2022_10K", "63,854")
    quarter = rec("ACME_2023Q2_EARNINGS", "16,101")
    basis = lambda doc: "annual" if doc.endswith("10K") else "quarterly"  # noqa: E731
    assert reconcile([annual, quarter], ENT, basis) == []
    # two annual filings are still compared
    assert len(reconcile([annual, rec("ACME_2023_10K", "63,854")], ENT, basis)) == 1
    # without a basis function the old behaviour remains
    assert len(reconcile([annual, quarter], ENT)) == 1
