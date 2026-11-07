import csv
import sqlite3
from decimal import Decimal as D

import pytest

from audit_rag.audit.checks import run_checks
from audit_rag.audit.checks_repo import ensure_schema as ensure_checks, save_checks, set_review, load_checks
from audit_rag.normalization.records import NormalizedRecord
from audit_rag.normalization.records_repo import ensure_schema as ensure_records, save_records
from audit_rag.reports.data import build_report
from audit_rag.reports.exporters import export_all


def rec(label, canon, text, order, pos=0, **kw):
    v = D(text.replace(",", ""))
    base = dict(
        document_id="d1", row_order=order, cell_position=pos, page_index=3,
        statement_type="balance_sheet", label=label, label_canonical=canon,
        column_label=str(2022 - pos), raw_text=text, kind="number", displayed_value=v,
        scaled_value=v * 1000, scale_status="scaled", multiplier=D(1000), reasons=(),
        method="pdf_text", confidence=None, row_warnings=())
    base.update(kw)
    return NormalizedRecord(**base)


RECORDS = [rec("Cash", None, "5.0", 0), rec("Total assets", "total_assets", "105.0", 1),
           rec("Total liabilities", "total_liabilities", "60.0", 2),
           rec("Total equity", "total_equity", "40.0", 3),
           rec("Total liabilities and equity", "total_liabilities_and_equity", "100.0", 4)]


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, original_filename TEXT, "
                 "file_type TEXT, status TEXT)")
    conn.execute("INSERT INTO documents VALUES ('d1', '=Acme.pdf', 'pdf', 'ingested')")
    ensure_records(conn)
    ensure_checks(conn)
    with conn:
        save_records(conn, "d1", RECORDS)
        save_checks(conn, "d1", run_checks(RECORDS))
    return conn


def test_report_numbers_are_honest():
    r = build_report(make_db(), "d1")
    assert (r.numbers_total, r.rows_total) == (5, 5)
    assert r.rows_verified == 4  # Cash is in no check
    assert round(r.verified_share, 2) == 0.8
    counts = r.status_counts()
    assert counts["fail"] == 1 and counts["pass"] >= 1
    assert r.needing_attention()[0].check.status == "fail"


def test_dismissed_items_leave_the_attention_list_but_stay_in_the_data():
    conn = make_db()
    stored = load_checks(conn, "d1")
    failing = [s for s in stored if s["check"].status == "fail"][0]
    with conn:
        set_review(conn, failing["check_id"], "dismissed", "known restatement")
    r = build_report(conn, "d1")
    assert all(i.check.status != "fail" for i in r.needing_attention())
    assert any(i.check.status == "fail" and i.review_note == "known restatement" for i in r.items)


def test_unknown_document_is_an_error():
    with pytest.raises(ValueError):
        build_report(make_db(), "nope")


def test_exports_are_written_and_readable(tmp_path):
    report = build_report(make_db(), "d1")
    files = export_all(report, tmp_path / "out")
    assert set(files) == {"csv", "xlsx", "pdf"}
    assert all(f.exists() and f.stat().st_size > 0 for f in files.values())

    with open(files["csv"], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(report.items) and {"rule", "status", "evidence"} <= set(rows[0])
    assert any(r["status"] == "fail" and "Total assets" in r["evidence"] for r in rows)

    from openpyxl import load_workbook
    wb = load_workbook(files["xlsx"])
    assert wb.sheetnames == ["Summary", "Needs attention", "All checks"]
    assert wb["All checks"].max_row == len(report.items) + 1
    summary = {row[0].value: row[1].value for row in wb["Summary"].iter_rows(min_row=1, max_row=11)}
    assert summary["Checks failed"] == "1"
    assert summary["Document"].startswith("'=")  # a formula-looking name is stored as text

    assert files["pdf"].read_bytes()[:5] == b"%PDF-"
    assert files["csv"].name == "_Acme_audit.csv"  # the unsafe first character is replaced in the file name


def test_empty_report_still_exports(tmp_path):
    conn = make_db()
    with conn:
        save_checks(conn, "d1", [])
    files = export_all(build_report(conn, "d1"), tmp_path)
    assert all(f.exists() for f in files.values())
