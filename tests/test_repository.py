"""Tests for saving and loading extraction results."""
import sqlite3

import pymupdf
import pytest

from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.db.repository import load_page_outcomes, load_rows, save_pdf_result
from audit_rag.ingestion.pdf_ingest import PdfIngestResult, ingest_pdf
from audit_rag.ingestion.registry import register_document


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5)


def build_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), "Item")
    page.insert_text((400, 60), "2022")
    page.insert_text((480, 60), "2021")
    page.insert_text((50, 80), "Cash")
    page.insert_text((400, 80), "100.0")
    page.insert_text((480, 80), "90.0")
    page.insert_text((50, 100), "Total assets")
    page.insert_text((400, 100), "250.5")
    page.insert_text((480, 100), "230.0")
    doc.new_page().insert_text((50, 60), "This note describes the company and contains no table at all.")
    doc.new_page()
    doc.save(str(path))
    doc.close()
    return path


def ingest(tmp_path, settings):
    registered = register_document(build_pdf(tmp_path / "t.pdf"), settings)
    return registered, ingest_pdf(registered, settings)


def test_rows_survive_a_restart_exactly(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    assert len(result.rows) == 2
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)
    with open_database(settings.db_path) as conn:  # a new connection, like a restart
        loaded = load_rows(conn, registered.document_id)
        outcomes = load_page_outcomes(conn, registered.document_id)
    assert loaded == result.rows
    assert outcomes == [(0, "table", None), (1, "no_table", None), (2, "blank", None)]


def test_saving_twice_replaces_instead_of_duplicating(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    for _ in range(2):
        with open_database(settings.db_path) as conn:
            save_pdf_result(conn, result)
    with open_database(settings.db_path) as conn:
        assert len(load_rows(conn, registered.document_id)) == 2
        assert conn.execute("SELECT COUNT(*) FROM extracted_rows").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM page_outcomes").fetchone()[0] == 3


def test_a_failed_save_keeps_the_previous_data(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)

    # Page 0 listed under two outcomes breaks the one-outcome-per-page rule. The
    # old rows are deleted first, then the insert fails: the rollback must restore them.
    broken = PdfIngestResult(
        document_id=registered.document_id, page_count=1,
        table_pages=[0], no_table_pages=[0],
    )
    with pytest.raises(sqlite3.IntegrityError):
        with open_database(settings.db_path) as conn:
            save_pdf_result(conn, broken)

    with open_database(settings.db_path) as conn:
        assert load_rows(conn, registered.document_id) == result.rows
        assert len(load_page_outcomes(conn, registered.document_id)) == 3


def test_deleting_a_document_removes_its_rows_cells_and_outcomes(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)
        conn.execute("DELETE FROM documents WHERE document_id = ?", (registered.document_id,))
        for table in ("extracted_rows", "extracted_cells", "page_outcomes"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_saving_marks_the_document_as_ingested(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    assert registered.status == "registered"
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)
        status = conn.execute(
            "SELECT status FROM documents WHERE document_id = ?", (registered.document_id,)
        ).fetchone()["status"]
    assert status == "ingested"


def test_loading_an_unknown_document_returns_nothing(settings):
    with open_database(settings.db_path) as conn:
        assert load_rows(conn, "doc-nope") == []
        assert load_page_outcomes(conn, "doc-nope") == []


def test_every_cell_keeps_its_original_text_and_column(tmp_path, settings):
    registered, result = ingest(tmp_path, settings)
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)
    with open_database(settings.db_path) as conn:
        cash = [r for r in load_rows(conn, registered.document_id) if r.label == "Cash"][0]
    assert {c.column_label: c.raw_text for c in cash.cells} == {"2022": "100.0", "2021": "90.0"}
    assert all(c.confidence is None for c in cash.cells)  # embedded text has no score


def test_ocr_read_and_unparsed_pages_are_stored_and_loaded(tmp_path, settings):
    registered, _ = ingest(tmp_path, settings)
    custom = PdfIngestResult(
        document_id=registered.document_id,
        page_count=3,
        table_pages=[0],
        ocr_unparsed_pages=[1],
        blank_pages=[2],
        ocr_read_pages=[0, 1],
    )
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, custom)
    with open_database(settings.db_path) as conn:
        outcomes = load_page_outcomes(conn, registered.document_id)
    assert outcomes[0] == (0, "table", "read by OCR")
    assert outcomes[1][:2] == (1, "ocr_unparsed")
    assert "review" in outcomes[1][2]
    assert outcomes[2] == (2, "blank", None)
