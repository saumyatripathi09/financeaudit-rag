"""Tests for whole-PDF ingestion, using small synthetic PDFs."""
import pymupdf
import pytest
from PIL import Image

from audit_rag.config import Settings
from audit_rag.ingestion import pdf_ingest
from audit_rag.ingestion.pdf_ingest import PdfIngestionError, ingest_pdf
from audit_rag.ingestion.registry import register_document


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5)


def add_table_page(doc, cash="100.0"):
    page = doc.new_page()
    page.insert_text((50, 60), "Item")
    page.insert_text((400, 60), "2022")
    page.insert_text((480, 60), "2021")
    page.insert_text((50, 80), "Cash")
    page.insert_text((400, 80), cash)
    page.insert_text((480, 80), "90.0")
    page.insert_text((50, 100), "Total assets")
    page.insert_text((400, 100), "250.5")
    page.insert_text((480, 100), "230.0")
    return page


def add_prose_page(doc):
    page = doc.new_page()
    page.insert_text((50, 60), "This note describes the company and contains no table at all.")
    return page


def save(doc, path, **kwargs):
    doc.save(str(path), **kwargs)
    doc.close()
    return path


def test_extracts_rows_from_a_table_page(tmp_path, settings):
    doc = pymupdf.open()
    add_table_page(doc)
    registered = register_document(save(doc, tmp_path / "t.pdf"), settings)

    result = ingest_pdf(registered, settings)

    assert result.table_pages == [0]
    cash = [r for r in result.rows if r.label == "Cash"][0]
    assert {c.column_label: c.raw_text for c in cash.cells} == {"2022": "100.0", "2021": "90.0"}
    assert cash.document_id == registered.document_id
    assert cash.location.page_index == 0


def test_every_page_is_accounted_for_exactly_once(tmp_path, settings):
    png = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "white").save(str(png))

    doc = pymupdf.open()
    add_table_page(doc)                       # page 0: a table
    add_prose_page(doc)                       # page 1: text, no table
    doc.new_page()                            # page 2: blank
    scan = doc.new_page()                     # page 3: image only, like a scan
    scan.insert_image(pymupdf.Rect(50, 50, 250, 250), filename=str(png))
    registered = register_document(save(doc, tmp_path / "mixed.pdf"), settings)

    result = ingest_pdf(registered, settings)

    assert result.table_pages == [0]
    assert result.no_table_pages == [1]
    assert result.blank_pages == [2]
    assert result.ocr_candidate_pages == [3]
    assert result.page_errors == []
    assert result.accounted_pages == result.page_count == 4


def test_one_failing_page_does_not_stop_the_others(tmp_path, settings, monkeypatch):
    doc = pymupdf.open()
    add_table_page(doc)
    add_prose_page(doc)
    add_table_page(doc, cash="111.0")
    registered = register_document(save(doc, tmp_path / "three.pdf"), settings)

    real = pdf_ingest.extract_page_rows

    def flaky(page, page_index, document_id, filename):
        if page_index == 1:
            raise ValueError("boom")
        return real(page, page_index, document_id, filename)

    monkeypatch.setattr(pdf_ingest, "extract_page_rows", flaky)
    result = ingest_pdf(registered, settings)

    assert [issue.page_index for issue in result.page_errors] == [1]
    assert "boom" in result.page_errors[0].message
    assert result.table_pages == [0, 2]
    assert result.accounted_pages == 3


def test_corrupt_pdf_raises_a_clear_error(tmp_path, settings):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not a real pdf body\n")
    registered = register_document(bad, settings)  # the start looks like a PDF
    with pytest.raises(PdfIngestionError):
        ingest_pdf(registered, settings)


def test_password_protected_pdf_is_refused(tmp_path, settings):
    doc = pymupdf.open()
    add_table_page(doc)
    path = save(
        doc, tmp_path / "locked.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user",
    )
    registered = register_document(path, settings)
    with pytest.raises(PdfIngestionError, match="password"):
        ingest_pdf(registered, settings)


def test_a_non_pdf_document_is_refused(tmp_path, settings):
    csv = tmp_path / "data.csv"
    csv.write_bytes(b"a,b\n1,2\n")
    registered = register_document(csv, settings)
    with pytest.raises(PdfIngestionError):
        ingest_pdf(registered, settings)
