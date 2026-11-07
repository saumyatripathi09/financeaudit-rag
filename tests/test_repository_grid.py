"""Tests for saving and loading CSV and Excel results."""
import sqlite3

import openpyxl
import pytest

from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.db.repository import (
    load_notes,
    load_rows,
    load_sheet_outcomes,
    save_grid_result,
)
from audit_rag.ingestion.csv_ingest import ingest_csv
from audit_rag.ingestion.excel_ingest import ingest_excel
from audit_rag.ingestion.grid import GridIngestResult
from audit_rag.ingestion.registry import register_document

DASH = "\u2014"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5)


def build_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ws.append(["Particulars", "Note", 2023, 2022])
    ws.append(["Cash", 4, 742.6, 601.6])
    ws.append(["Assets held for sale", 6, DASH, 416.1])
    ws.append(["Margin", 1, 0.25, 0.2])
    ws["C4"].number_format = "0.0%"  # produces a warning that must survive saving
    wb.create_sheet("Notes")["A1"] = "No table here."
    wb.create_sheet("Empty")
    wb.save(str(path))
    return path


def ingest_workbook(tmp_path, settings):
    registered = register_document(build_workbook(tmp_path / "b.xlsx"), settings)
    return registered, ingest_excel(registered, settings)


def test_an_excel_result_survives_a_restart_exactly(tmp_path, settings):
    registered, result = ingest_workbook(tmp_path, settings)
    assert any(r.warnings for r in result.rows)  # the round trip includes warnings
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)
    with open_database(settings.db_path) as conn:  # a new connection, like a restart
        assert load_rows(conn, registered.document_id) == result.rows
        assert load_sheet_outcomes(conn, registered.document_id) == result.outcomes
        assert load_notes(conn, registered.document_id) == result.notes
    # Workbook order, not alphabetical order (alphabetical would put Empty before Notes).
    assert [name for name, _, _ in result.outcomes] == ["Balance Sheet", "Notes", "Empty"]


def test_a_csv_result_survives_a_restart_with_its_notes(tmp_path, settings):
    path = tmp_path / "s.csv"
    path.write_bytes("Particulars,2023,2022\nCr\u00e9ances,10.0,9.0\n".encode("cp1252"))
    registered = register_document(path, settings)
    result = ingest_csv(registered, settings)
    assert result.notes  # the Windows-1252 note
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)
    with open_database(settings.db_path) as conn:
        assert load_rows(conn, registered.document_id) == result.rows
        assert load_sheet_outcomes(conn, registered.document_id) == [("csv", "table", None)]
        assert load_notes(conn, registered.document_id) == result.notes
        assert load_rows(conn, registered.document_id)[0].label == "Cr\u00e9ances"


def test_saving_twice_replaces_instead_of_duplicating(tmp_path, settings):
    registered, result = ingest_workbook(tmp_path, settings)
    for _ in range(2):
        with open_database(settings.db_path) as conn:
            save_grid_result(conn, result)
    with open_database(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM extracted_rows").fetchone()[0] == len(result.rows)
        assert conn.execute("SELECT COUNT(*) FROM sheet_outcomes").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM document_notes").fetchone()[0] == len(result.notes)


def test_a_failed_save_keeps_the_previous_data(tmp_path, settings):
    registered, result = ingest_workbook(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)

    # The same sheet name twice breaks the primary key AFTER the old data was deleted.
    broken = GridIngestResult(
        document_id=registered.document_id,
        outcomes=[("Sheet1", "table", None), ("Sheet1", "blank", None)],
    )
    with pytest.raises(sqlite3.IntegrityError):
        with open_database(settings.db_path) as conn:
            save_grid_result(conn, broken)

    with open_database(settings.db_path) as conn:
        assert load_rows(conn, registered.document_id) == result.rows
        assert load_sheet_outcomes(conn, registered.document_id) == result.outcomes


def test_an_unknown_outcome_is_rejected_by_the_database(tmp_path, settings):
    registered, _ = ingest_workbook(tmp_path, settings)
    bad = GridIngestResult(
        document_id=registered.document_id, outcomes=[("Sheet1", "weird", None)]
    )
    with pytest.raises(sqlite3.IntegrityError):
        with open_database(settings.db_path) as conn:
            save_grid_result(conn, bad)


def test_deleting_a_document_removes_everything_stored_for_it(tmp_path, settings):
    registered, result = ingest_workbook(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)
        conn.execute("DELETE FROM documents WHERE document_id = ?", (registered.document_id,))
        for table in ("extracted_rows", "extracted_cells", "sheet_outcomes", "document_notes"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_saving_marks_the_document_as_ingested(tmp_path, settings):
    registered, result = ingest_workbook(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)
        status = conn.execute(
            "SELECT status FROM documents WHERE document_id = ?", (registered.document_id,)
        ).fetchone()["status"]
    assert status == "ingested"


def test_loading_an_unknown_document_returns_nothing(settings):
    with open_database(settings.db_path) as conn:
        assert load_rows(conn, "doc-nope") == []
        assert load_sheet_outcomes(conn, "doc-nope") == []
        assert load_notes(conn, "doc-nope") == []


def test_an_unparsed_image_outcome_is_stored_and_loaded(tmp_path, settings):
    registered, _ = ingest_workbook(tmp_path, settings)
    result = GridIngestResult(
        document_id=registered.document_id,
        outcomes=[("image", "ocr_unparsed", "review the image")],
    )
    with open_database(settings.db_path) as conn:
        save_grid_result(conn, result)
    with open_database(settings.db_path) as conn:
        assert load_sheet_outcomes(conn, registered.document_id) == [
            ("image", "ocr_unparsed", "review the image")
        ]
