"""Tests for Excel ingestion, using small workbooks built on the fly."""
from datetime import datetime

import openpyxl
import pytest

from audit_rag.config import Settings
from audit_rag.ingestion import excel_ingest
from audit_rag.ingestion.excel_ingest import ExcelIngestionError, ingest_excel
from audit_rag.ingestion.registry import register_document
from audit_rag.models.extraction import ExtractionMethod

DASH = "\u2014"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5)


def table(ws):
    ws.append(["Particulars", "Note", 2023, 2022])    # row 1
    ws.append(["Current assets"])                      # row 2
    ws.append(["Cash", 4, 742.6, 601.6])               # row 3
    ws.append(["Trade receivables", 5, 1615.9, 1864.3])  # row 4
    ws.append(["Assets held for sale", 6, DASH, 416.1])  # row 5


def make_book(tmp_path, fill=table, extra=None, name="b.xlsx", title="Balance Sheet"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    fill(ws)
    if extra:
        extra(wb)
    path = tmp_path / name
    wb.save(str(path))
    return path


def ingest(path, settings):
    registered = register_document(path, settings)
    return registered, ingest_excel(registered, settings)


def row_named(result, label):
    matches = [r for r in result.rows if r.label == label]
    assert len(matches) == 1, f"expected one row labelled {label!r}, got {len(matches)}"
    return matches[0]


def values(row):
    return {c.column_label: c.raw_text for c in row.cells}


def test_basic_rows_values_and_cell_references(tmp_path, settings):
    registered, result = ingest(make_book(tmp_path), settings)
    cash = row_named(result, "Cash")
    assert values(cash) == {"Note": "4", "2023": "742.6", "2022": "601.6"}
    assert [c.location.cell_ref for c in cash.cells] == ["B3", "C3", "D3"]
    assert cash.location.cell_ref == "A3:D3"
    assert cash.location.sheet_name == "Balance Sheet"
    assert cash.method == ExtractionMethod.SPREADSHEET
    assert cash.document_id == registered.document_id
    assert cash.warnings == []
    assert result.outcomes == [("Balance Sheet", "table", None)]
    assert any("stored in the workbook" in note for note in result.notes)


def test_a_dash_is_kept_as_displayed(tmp_path, settings):
    _, result = ingest(make_book(tmp_path), settings)
    assert values(row_named(result, "Assets held for sale")) == {
        "Note": "6", "2023": DASH, "2022": "416.1",
    }


def test_a_formula_with_no_stored_result_is_flagged_not_read_as_zero(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws["A6"] = "Total"
        ws["C6"] = "=C3+C4"
        ws["D6"] = "=D3+D4"

    _, result = ingest(make_book(tmp_path, fill), settings)
    total = row_named(result, "Total")
    assert total.cells == []
    assert sum("no stored result" in w for w in total.warnings) == 2


def test_percentage_formatted_cells_are_flagged(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws["A6"] = "Margin"
        ws["C6"] = 0.25
        ws["C6"].number_format = "0.0%"
        ws["D6"] = 0.2

    _, result = ingest(make_book(tmp_path, fill), settings)
    margin = row_named(result, "Margin")
    assert values(margin) == {"2023": "0.25", "2022": "0.2"}
    assert any("C6" in w and "percentage" in w for w in margin.warnings)


def test_a_hidden_row_is_read_and_flagged(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws.row_dimensions[3].hidden = True

    _, result = ingest(make_book(tmp_path, fill), settings)
    cash = row_named(result, "Cash")
    assert values(cash)["2023"] == "742.6"
    assert any("hidden" in w for w in cash.warnings)
    assert row_named(result, "Trade receivables").warnings == []


def test_a_hidden_column_is_read_and_flagged(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws.column_dimensions["B"].hidden = True

    _, result = ingest(make_book(tmp_path, fill), settings)
    cash = row_named(result, "Cash")
    assert values(cash)["Note"] == "4"
    assert any("B3" in w and "hidden column" in w for w in cash.warnings)


def test_a_hidden_sheet_is_read_and_flagged(tmp_path, settings):
    def add_hidden(wb):
        old = wb.create_sheet("Old")
        table(old)
        old.sheet_state = "hidden"

    _, result = ingest(make_book(tmp_path, extra=add_hidden), settings)
    old_rows = [r for r in result.rows if r.location.sheet_name == "Old"]
    assert old_rows
    assert all(r.warnings[0] == "sheet is hidden" for r in old_rows)
    assert ("Old", "table", "hidden sheet") in result.outcomes


def test_an_excel_error_value_is_flagged(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws["C3"] = "#DIV/0!"

    _, result = ingest(make_book(tmp_path, fill), settings)
    cash = row_named(result, "Cash")
    assert any("C3" in w and "Excel error value" in w for w in cash.warnings)
    assert values(cash)["2023"] == "#DIV/0!"


def test_merged_cells_are_noted(tmp_path, settings):
    def fill(ws):
        table(ws)
        ws["A6"] = "Memo"
        ws.merge_cells("A6:B6")

    _, result = ingest(make_book(tmp_path, fill), settings)
    assert any("merged cells" in note for note in result.notes)


def test_each_sheet_gets_its_own_outcome(tmp_path, settings):
    def add_sheets(wb):
        notes = wb.create_sheet("Notes")
        notes["A1"] = "Basis of preparation"
        notes["A2"] = "No table here."
        wb.create_sheet("Empty")

    _, result = ingest(make_book(tmp_path, extra=add_sheets), settings)
    assert [(name, outcome) for name, outcome, _ in result.outcomes] == [
        ("Balance Sheet", "table"), ("Notes", "no_table"), ("Empty", "blank"),
    ]


def test_one_failing_sheet_does_not_stop_the_others(tmp_path, settings, monkeypatch):
    def add_bad(wb):
        table(wb.create_sheet("Bad"))

    real = excel_ingest.build_rows

    def flaky(grid, **kwargs):
        if kwargs.get("sheet_name") == "Bad":
            raise ValueError("boom")
        return real(grid, **kwargs)

    monkeypatch.setattr(excel_ingest, "build_rows", flaky)
    _, result = ingest(make_book(tmp_path, extra=add_bad), settings)
    assert result.outcomes[0] == ("Balance Sheet", "table", None)
    assert result.outcomes[1][0] == "Bad" and result.outcomes[1][1] == "error"
    assert "boom" in result.outcomes[1][2]
    assert any(r.label == "Cash" for r in result.rows)


def test_date_headings_become_column_labels(tmp_path, settings):
    def fill(ws):
        ws.append(["Particulars", datetime(2023, 3, 31), datetime(2022, 3, 31)])
        ws.append(["Cash", 10.0, 9.0])

    _, result = ingest(make_book(tmp_path, fill), settings)
    assert values(row_named(result, "Cash")) == {
        "2023-03-31T00:00:00": "10", "2022-03-31T00:00:00": "9",  # a stored whole number has no ".0"
    }


def test_a_very_large_sheet_is_truncated_and_says_so(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(excel_ingest, "MAX_ROWS", 4)
    _, result = ingest(make_book(tmp_path), settings)
    assert any(r.label == "Trade receivables" for r in result.rows)
    assert not any(r.label == "Assets held for sale" for r in result.rows)
    assert "truncated" in result.outcomes[0][2]


def test_an_old_xls_workbook_is_refused_with_advice(tmp_path, settings):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    registered = register_document(path, settings)
    with pytest.raises(ExcelIngestionError, match="xlsx"):
        ingest_excel(registered, settings)


def test_a_non_excel_document_is_refused(tmp_path, settings):
    path = tmp_path / "data.csv"
    path.write_bytes(b"a,b\n1,2\n")
    registered = register_document(path, settings)
    with pytest.raises(ExcelIngestionError):
        ingest_excel(registered, settings)

