"""Tests for CSV ingestion and the shared grid builder."""
from datetime import date

import pymupdf
import pytest

from audit_rag.config import Settings
from audit_rag.ingestion.csv_ingest import CsvIngestionError, ingest_csv
from audit_rag.ingestion.grid import cell_text, column_letter
from audit_rag.ingestion.registry import register_document
from audit_rag.models.extraction import ExtractionMethod

DASH = "\u2014"

BASIC = (
    "Particulars,Note,2023,2022\n"
    "Current assets,,,\n"
    "Cash,4,742.6,601.6\n"
    'Trade receivables,5,"1,615.9","1,864.3"\n'
    "Assets held for sale,6,\u2014,416.1\n"
)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5)


def ingest(tmp_path, settings, content, name="s.csv"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    path = tmp_path / name
    path.write_bytes(data)
    registered = register_document(path, settings)
    return registered, ingest_csv(registered, settings)


def row_named(result, label):
    matches = [r for r in result.rows if r.label == label]
    assert len(matches) == 1, f"expected one row labelled {label!r}, got {len(matches)}"
    return matches[0]


def values(row):
    return {c.column_label: c.raw_text for c in row.cells}


def test_column_letters_follow_spreadsheet_naming():
    assert [column_letter(i) for i in (0, 1, 25, 26, 27, 701, 702)] == [
        "A", "B", "Z", "AA", "AB", "ZZ", "AAA",
    ]


def test_cell_text_writes_numbers_as_a_person_would_read_them():
    assert cell_text(100.0) == "100.0"
    assert cell_text(1e-07) == "0.0000001"
    assert cell_text(5) == "5"
    assert cell_text(None) == ""
    assert cell_text("  x ") == "x"
    assert cell_text(date(2023, 3, 31)) == "2023-03-31"
    assert cell_text(True) == "True"


def test_basic_statement_rows_values_and_cell_references(tmp_path, settings):
    _, result = ingest(tmp_path, settings, BASIC)
    row = row_named(result, "Trade receivables")
    assert values(row) == {"Note": "5", "2023": "1,615.9", "2022": "1,864.3"}
    assert [c.location.cell_ref for c in row.cells] == ["B4", "C4", "D4"]
    assert row.location.cell_ref == "A4:D4"
    assert row.warnings == []
    assert result.outcomes == [("csv", "table", None)]


def test_a_dash_is_kept_as_displayed(tmp_path, settings):
    _, result = ingest(tmp_path, settings, BASIC)
    row = row_named(result, "Assets held for sale")
    assert values(row) == {"Note": "6", "2023": DASH, "2022": "416.1"}
    assert row.warnings == []


def test_a_section_heading_row_has_a_label_and_no_cells(tmp_path, settings):
    _, result = ingest(tmp_path, settings, BASIC)
    row = row_named(result, "Current assets")
    assert row.cells == []
    assert row.warnings == []


def test_semicolon_delimited_file(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Particulars;2023;2022\nCash;742.6;601.6\n")
    assert values(row_named(result, "Cash")) == {"2023": "742.6", "2022": "601.6"}


def test_windows_1252_file_is_decoded_and_noted(tmp_path, settings):
    content = "Particulars,2023,2022\nCr\u00e9ances,10.0,9.0\n".encode("cp1252")
    _, result = ingest(tmp_path, settings, content)
    assert result.rows[0].label == "Cr\u00e9ances"
    assert any("Windows-1252" in note for note in result.notes)


def test_a_utf8_byte_order_mark_is_ignored(tmp_path, settings):
    content = b"\xef\xbb\xbf" + "Particulars,2023,2022\nCash,1.0,2.0\n".encode("utf-8")
    _, result = ingest(tmp_path, settings, content)
    assert values(row_named(result, "Cash")) == {"2023": "1.0", "2022": "2.0"}
    assert result.notes == []


def test_no_year_heading_means_no_table(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Name,Amount\nCash,100\n")
    assert result.rows == []
    assert result.outcomes == [("csv", "no_table", None)]


def test_the_same_year_heading_twice_is_not_a_usable_header(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Particulars,2023,2023\nCash,1.0,2.0\n")
    assert result.rows == []
    assert result.outcomes == [("csv", "no_table", None)]


def test_a_file_with_only_empty_cells_is_blank(tmp_path, settings):
    _, result = ingest(tmp_path, settings, ",,\n,,\n")
    assert result.rows == []
    assert result.outcomes == [("csv", "blank", None)]


def test_a_value_in_a_column_without_a_heading_is_flagged(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Particulars,2023,2022\nCash,1.0,2.0,3.0\n")
    row = row_named(result, "Cash")
    assert [c.column_label for c in row.cells] == ["2023", "2022", None]
    assert any("D2" in w and "no heading" in w for w in row.warnings)


def test_text_in_a_year_column_is_flagged_not_dropped(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Particulars,2023,2022\nStatus,Audited,Audited\n")
    row = row_named(result, "Status")
    assert [c.raw_text for c in row.cells] == ["Audited", "Audited"]
    assert any("non-numeric" in w for w in row.warnings)


def test_values_without_a_label_are_flagged(tmp_path, settings):
    _, result = ingest(tmp_path, settings, "Particulars,2023,2022\n,1.0,2.0\n")
    row = result.rows[0]
    assert row.label == ""
    assert any("without a row label" in w for w in row.warnings)


def test_rows_carry_document_id_filename_and_method(tmp_path, settings):
    registered, result = ingest(tmp_path, settings, BASIC)
    assert all(r.document_id == registered.document_id for r in result.rows)
    assert all(r.filename == "s.csv" for r in result.rows)
    assert all(r.method == ExtractionMethod.CSV for r in result.rows)


def test_a_non_csv_document_is_refused(tmp_path, settings):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello")
    pdf = tmp_path / "a.pdf"
    doc.save(str(pdf))
    doc.close()
    registered = register_document(pdf, settings)
    with pytest.raises(CsvIngestionError):
        ingest_csv(registered, settings)
