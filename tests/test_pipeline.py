from decimal import Decimal

from audit_rag.models.extraction import (ExtractedCell, ExtractedRow, ExtractionMethod,
                                         SourceLocation)
from audit_rag.normalization.pipeline import normalize_rows


def row(label, *texts, page=0):
    loc = SourceLocation(page_index=page, row_index=0)
    return ExtractedRow(
        document_id="d1", filename="a.pdf", label=label, location=loc,
        method=ExtractionMethod.PDF_TEXT,
        cells=[ExtractedCell(raw_text=t, location=loc, column_label=str(2022 - i))
               for i, t in enumerate(texts)],
    )


TEXTS = {
    0: "Consolidated Balance Sheets (in millions)\nTotal assets 10",
    1: "Consolidated Statements of Income\nNo scale stated here",
}


def test_pages_get_their_own_scale_and_statement():
    rows = [row("Total assets", "10", page=0), row("Revenue", "5", page=1),
            row("Total liabilities and stockholders equity", "10", page=0)]
    result = normalize_rows(rows, TEXTS.get)
    assert [r.label for r in result.records] == [
        "Total assets", "Revenue", "Total liabilities and stockholders equity"
    ]  # original row order is kept even though pages interleave
    a, rev, eq = result.records
    assert a.statement_type == "balance_sheet" and a.scaled_value == Decimal("10000000")
    assert eq.label_canonical == "total_liabilities_and_equity"
    assert rev.statement_type == "income_statement" and rev.scale_status == "scale_unknown"
    assert rev.scaled_value is None


def test_no_page_text_means_unknown_scale_not_a_guess():
    result = normalize_rows([row("Total assets", "10")], None)
    assert result.records[0].scale_status == "scale_unknown"
    assert result.records[0].statement_type == "unknown"


def test_unreadable_page_is_noted_and_does_not_stop_the_rest():
    def text(i):
        if i == 0:
            raise RuntimeError("boom")
        return TEXTS[1]
    result = normalize_rows([row("Total assets", "10", page=0), row("Revenue", "5", page=1)], text)
    assert len(result.records) == 2
    assert any("could not read page text" in n for n in result.notes)
    assert result.records[0].scale_status == "scale_unknown"


def test_status_counts():
    result = normalize_rows([row("Total assets", "10")], TEXTS.get)
    assert result.status_counts() == {"scaled": 1}


def srow(label, *texts, sheet=None):
    from audit_rag.models.extraction import ExtractedCell, ExtractedRow, ExtractionMethod, SourceLocation
    loc = SourceLocation(sheet_name=sheet, row_index=0)
    return ExtractedRow(
        document_id="d1", filename="a.xlsx", label=label, location=loc,
        method=ExtractionMethod.SPREADSHEET,
        cells=[ExtractedCell(raw_text=t, location=loc, column_label="2022") for t in texts])


BS_LABELS = [("Accounts receivable", "5"), ("Total current assets", "10"), ("Total assets", "30"),
             ("Total current liabilities", "4"), ("Total liabilities", "12"), ("Retained earnings", "8")]


def test_spreadsheet_statement_type_comes_from_the_sheet_name_or_heading_rows():
    named = normalize_rows([srow("Total assets", "30", sheet="Balance Sheet")], None)
    assert named.records[0].statement_type == "balance_sheet"
    heading = normalize_rows([srow("ACME - Consolidated Statements of Income (test)", sheet="Sheet1"),
                              srow("Revenue", "5", sheet="Sheet1")], None)
    assert heading.records[0].statement_type == "income_statement"


def test_spreadsheet_statement_type_falls_back_to_row_labels_and_never_guesses():
    typed = normalize_rows([srow(l, v, sheet="Sheet1") for l, v in BS_LABELS], None)
    assert {r.statement_type for r in typed.records} == {"balance_sheet"}
    thin = normalize_rows([srow("Total assets", "30", sheet="Sheet1")], None)
    assert thin.records[0].statement_type == "unknown"


def test_each_sheet_is_typed_on_its_own_and_csv_without_sheets_works():
    rows = [srow("Total assets", "30", sheet="Balance Sheet"), srow("Revenue", "9", sheet="Income Statement")]
    got = {r.label: r.statement_type for r in normalize_rows(rows, None).records}
    assert got == {"Total assets": "balance_sheet", "Revenue": "income_statement"}
    csv = normalize_rows([srow(l, v) for l, v in BS_LABELS], None)
    assert {r.statement_type for r in csv.records} == {"balance_sheet"}


def test_spreadsheet_missing_total_liabilities_is_now_reported():
    from audit_rag.audit.checks import run_checks
    rows = [(l, v) for l, v in BS_LABELS if l != "Total liabilities"] + [
        ("Total stockholders equity", "18"), ("Total liabilities and stockholders equity", "30")]
    result = normalize_rows([srow(l, v, sheet="Balance Sheet") for l, v in rows], None)
    got = {(c.rule_id, c.status) for c in run_checks(result.records)}
    assert ("liabilities_plus_equity", "not_testable") in got
    assert ("current_liabilities_within_total", "not_testable") in got


def grow(label, cols, sheet="Balance Sheet"):
    """A spreadsheet row whose cells sit in the given (column index, text) positions."""
    from audit_rag.models.extraction import ExtractedCell, ExtractedRow, ExtractionMethod, SourceLocation
    loc = SourceLocation(sheet_name=sheet, row_index=0)
    cells = [ExtractedCell(raw_text=t, column_label={1: "2022", 2: "2021"}.get(c),
                           location=SourceLocation(sheet_name=sheet, row_index=0, column_index=c))
             for c, t in cols]
    return ExtractedRow(document_id="d1", filename="a.xlsx", label=label, cells=cells,
                        location=loc, method=ExtractionMethod.SPREADSHEET)


def test_a_row_shifted_one_column_is_paired_by_column_and_fails_the_check():
    from audit_rag.audit.checks import run_checks
    rows = [grow("Total assets", [(2, "2500"), (3, "2300")]),  # shifted right by one column
            grow("Total liabilities and stockholders equity", [(1, "2500"), (2, "2300")])]
    checks = run_checks(normalize_rows(rows, None).records)
    fails = [c for c in checks if c.rule_id == "balance_equation" and c.status == "fail"]
    assert len(fails) == 1 and fails[0].column == "2021"  # 2500 sits under 2021, which is 2300


def test_unshifted_spreadsheet_columns_still_pair_up():
    from audit_rag.audit.checks import run_checks
    rows = [grow("Total assets", [(1, "2500"), (2, "2300")]),
            grow("Total liabilities and stockholders equity", [(1, "2500"), (2, "2300")])]
    checks = [c for c in run_checks(normalize_rows(rows, None).records) if c.rule_id == "balance_equation"]
    assert [c.status for c in checks] == ["pass", "pass"]


def test_rows_with_no_readable_values_are_reported_in_the_notes():
    from audit_rag.models.extraction import ExtractedRow, ExtractionMethod, SourceLocation
    loc = SourceLocation(sheet_name="Balance Sheet", row_index=0)
    lost = [ExtractedRow(document_id="d1", filename="a.xlsx", label=name, cells=[], location=loc,
                         method=ExtractionMethod.SPREADSHEET,
                         warnings=[f"B{i}: formula with no stored result (the workbook was never calculated); value unknown"])
            for i, name in ((8, "Total current assets"), (10, "Total assets"))]
    result = normalize_rows(lost + [grow("Cash", [(1, "5"), (2, "4")])], None)
    assert any(n.startswith("2 row(s) have no readable values: formula with no stored result") for n in result.notes)
