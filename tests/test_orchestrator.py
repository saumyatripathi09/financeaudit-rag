"""Tests for the ingestion orchestrator."""
import openpyxl
import pymupdf
import pytest
import pytesseract

from audit_rag.config import Settings, get_settings
from audit_rag.db.database import open_database
from audit_rag.db.repository import (
    load_notes,
    load_page_outcomes,
    load_rows,
    load_sheet_outcomes,
)
from audit_rag.ingestion import ocr_ingest
from audit_rag.ingestion.ocr_ingest import OcrUnavailableError
from audit_rag.ingestion.orchestrator import ingest_document
from audit_rag.ingestion.registry import register_document

TABLE = [
    (50, 60, "Item"), (400, 60, "2022"), (480, 60, "2021"),
    (50, 80, "Cash"), (400, 80, "100.0"), (480, 80, "90.0"),
    (50, 100, "Total assets"), (400, 100, "250.5"), (480, 100, "230.0"),
]


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=100)


@pytest.fixture
def ocr_settings(tmp_path):
    configured = Settings(
        data_dir=tmp_path / "data", max_upload_mb=100,
        tesseract_cmd=get_settings().tesseract_cmd,
    )
    try:
        ocr_ingest._prepare_tesseract(configured)
    except OcrUnavailableError:
        pytest.skip("Tesseract is not available on this machine")
    return configured


def text_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text in TABLE:
        page.insert_text((x, y), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def scanned_pdf(path):
    source = pymupdf.open()
    page = source.new_page()
    for x, y, text in TABLE:
        page.insert_text((x, y), text, fontsize=12)
    png = page.get_pixmap(dpi=300).tobytes("png")
    source.close()
    doc = pymupdf.open()
    target = doc.new_page()
    target.insert_image(target.rect, stream=png)
    doc.save(str(path), deflate=True)
    doc.close()
    return path, png


def row_count(settings, document_id):
    with open_database(settings.db_path) as conn:
        return len(load_rows(conn, document_id))


def status_of(settings, document_id):
    with open_database(settings.db_path) as conn:
        return conn.execute(
            "SELECT status FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()["status"]


def test_a_text_pdf_is_read_and_saved(tmp_path, settings):
    document = register_document(text_pdf(tmp_path / "t.pdf"), settings)
    summary = ingest_document(document, settings)
    assert summary.status == "ingested" and summary.row_count == 2
    assert summary.notes == []
    with open_database(settings.db_path) as conn:
        assert load_page_outcomes(conn, document.document_id) == [(0, "table", None)]
    assert status_of(settings, document.document_id) == "ingested"


def test_a_scanned_pdf_is_read_by_ocr_and_says_so(tmp_path, ocr_settings):
    path, _ = scanned_pdf(tmp_path / "scan.pdf")
    document = register_document(path, ocr_settings)
    summary = ingest_document(document, ocr_settings)
    assert summary.status == "ingested" and summary.row_count == 2
    assert any("read by OCR" in note for note in summary.notes)
    with open_database(ocr_settings.db_path) as conn:
        assert load_page_outcomes(conn, document.document_id) == [(0, "table", "read by OCR")]
        assert load_notes(conn, document.document_id) == summary.notes


def test_a_scanned_pdf_without_tesseract_keeps_its_pages_unread(tmp_path, monkeypatch):
    # Restore the global command after this test, whatever happens.
    monkeypatch.setattr(
        pytesseract.pytesseract, "tesseract_cmd", pytesseract.pytesseract.tesseract_cmd
    )
    broken = Settings(
        data_dir=tmp_path / "data", max_upload_mb=100,
        tesseract_cmd=tmp_path / "no_such_tesseract.exe",
    )
    path, _ = scanned_pdf(tmp_path / "scan.pdf")
    document = register_document(path, broken)
    summary = ingest_document(document, broken)
    assert summary.status == "ingested" and summary.row_count == 0
    assert any("not read" in note for note in summary.notes)
    with open_database(broken.db_path) as conn:
        assert load_page_outcomes(conn, document.document_id) == [(0, "ocr_candidate", None)]
        assert load_notes(conn, document.document_id) == summary.notes


def test_a_corrupt_pdf_is_recorded_as_failed(tmp_path, settings):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf body\n")
    document = register_document(bad, settings)
    summary = ingest_document(document, settings)
    assert summary.status == "failed" and summary.message
    assert status_of(settings, document.document_id) == "failed"
    with open_database(settings.db_path) as conn:
        notes = load_notes(conn, document.document_id)
    assert len(notes) == 1 and notes[0].startswith("ingestion failed:")


def test_an_old_xls_workbook_fails_with_advice(tmp_path, settings):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    document = register_document(path, settings)
    summary = ingest_document(document, settings)
    assert summary.status == "failed" and "xlsx" in summary.message


def test_a_csv_is_read_and_saved(tmp_path, settings):
    path = tmp_path / "s.csv"
    path.write_bytes(b"Particulars,2023,2022\nCash,742.6,601.6\n")
    document = register_document(path, settings)
    summary = ingest_document(document, settings)
    assert summary.status == "ingested" and summary.row_count == 1
    with open_database(settings.db_path) as conn:
        assert load_sheet_outcomes(conn, document.document_id) == [("csv", "table", None)]


def test_a_workbook_is_read_and_saved(tmp_path, settings):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ws.append(["Particulars", 2023, 2022])
    ws.append(["Cash", 742.6, 601.6])
    path = tmp_path / "b.xlsx"
    wb.save(str(path))
    document = register_document(path, settings)
    summary = ingest_document(document, settings)
    assert summary.status == "ingested" and summary.row_count == 1
    assert any("stored in the workbook" in note for note in summary.notes)
    with open_database(settings.db_path) as conn:
        assert load_sheet_outcomes(conn, document.document_id) == [("Balance Sheet", "table", None)]


def test_an_image_is_read_by_ocr_and_saved(tmp_path, ocr_settings):
    _, png = scanned_pdf(tmp_path / "scan.pdf")
    image = tmp_path / "statement.png"
    image.write_bytes(png)
    document = register_document(image, ocr_settings)
    summary = ingest_document(document, ocr_settings)
    assert summary.status == "ingested" and summary.row_count == 2
    with open_database(ocr_settings.db_path) as conn:
        assert load_sheet_outcomes(conn, document.document_id) == [("image", "table", None)]


def test_ingesting_twice_replaces_instead_of_duplicating(tmp_path, settings):
    document = register_document(text_pdf(tmp_path / "t.pdf"), settings)
    ingest_document(document, settings)
    ingest_document(document, settings)
    assert row_count(settings, document.document_id) == 2


def test_a_known_failure_does_not_stop_the_next_document(tmp_path, settings):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf body\n")
    good = text_pdf(tmp_path / "good.pdf")
    summaries = [
        ingest_document(register_document(path, settings), settings) for path in (bad, good)
    ]
    assert [s.status for s in summaries] == ["failed", "ingested"]
