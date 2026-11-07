"""Tests for OCR ingestion.

Pages are drawn as text, rendered to a picture and wrapped in an image-only PDF,
so the only way to read them is OCR. The pictures are clean, so these tests show
that the pipeline works, not how accurate OCR is on real scans.
"""
import pymupdf
import pytesseract
import pytest

from audit_rag.config import Settings, get_settings
from audit_rag.ingestion import ocr_ingest
from audit_rag.ingestion.ocr_ingest import (
    OcrIngestionError,
    OcrUnavailableError,
    _looks_misread,
    add_ocr_warnings,
    ingest_image,
    ocr_pdf_pages,
)
from audit_rag.ingestion.pdf_ingest import PdfIngestResult, ingest_pdf
from audit_rag.ingestion.registry import register_document
from audit_rag.models.extraction import (
    ExtractedCell,
    ExtractedRow,
    ExtractionMethod,
    SourceLocation,
)


@pytest.fixture
def settings(tmp_path):
    configured = Settings(
        data_dir=tmp_path / "data",
        max_upload_mb=100,
        tesseract_cmd=get_settings().tesseract_cmd,
    )
    try:
        ocr_ingest._prepare_tesseract(configured)
    except OcrUnavailableError:
        pytest.skip("Tesseract is not available on this machine")
    return configured


def draw_table(page):
    for x, y, text in [
        (50, 60, "Item"), (400, 60, "2022"), (480, 60, "2021"),
        (50, 80, "Cash"), (400, 80, "100.0"), (480, 80, "90.0"),
        (50, 100, "Total assets"), (400, 100, "250.5"), (480, 100, "230.0"),
    ]:
        page.insert_text((x, y), text, fontsize=12)


def draw_prose(page):
    page.insert_text(
        (50, 60), "This note describes the company and contains no table at all.", fontsize=12
    )


def render_png(draw, dpi=300):
    doc = pymupdf.open()
    page = doc.new_page()
    draw(page)
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    doc.close()
    return png


def scan_pdf(path, pngs):
    doc = pymupdf.open()
    for png in pngs:
        page = doc.new_page()
        page.insert_image(page.rect, stream=png)
    doc.save(str(path), deflate=True)
    doc.close()
    return path


def row_named(rows, label):
    matches = [r for r in rows if r.label == label]
    assert len(matches) == 1, f"expected one row labelled {label!r}, got {len(matches)}"
    return matches[0]


def values(row):
    return {c.column_label: c.raw_text for c in row.cells}


def make_row(label, cells):
    location = SourceLocation(page_index=0)
    return ExtractedRow(
        document_id="d", filename="f.pdf", label=label, location=location,
        method=ExtractionMethod.OCR,
        cells=[
            ExtractedCell(raw_text=text, location=location, column_label="2022", confidence=conf)
            for text, conf in cells
        ],
    )


def test_a_scanned_page_is_read_by_ocr(tmp_path, settings):
    registered = register_document(scan_pdf(tmp_path / "scan.pdf", [render_png(draw_table)]), settings)
    result = ingest_pdf(registered, settings)
    assert result.ocr_candidate_pages == [0]

    ocr_pdf_pages(registered, settings, result)

    assert result.ocr_candidate_pages == []
    assert result.table_pages == [0]
    assert result.ocr_read_pages == [0]
    assert result.accounted_pages == result.page_count == 1
    cash = row_named(result.rows, "Cash")
    assert values(cash) == {"2022": "100.0", "2021": "90.0"}
    assert cash.method == ExtractionMethod.OCR
    assert all(c.confidence is not None and 0.0 <= c.confidence <= 1.0 for c in cash.cells)
    # Positions are converted from image pixels back to PDF points (300 dpi to 72).
    assert 40 < cash.location.bbox.x0 < 60
    assert 390 < cash.cells[0].location.bbox.x0 < 415


def test_a_scanned_page_of_prose_is_not_a_table(tmp_path, settings):
    registered = register_document(scan_pdf(tmp_path / "prose.pdf", [render_png(draw_prose)]), settings)
    result = ingest_pdf(registered, settings)
    ocr_pdf_pages(registered, settings, result)
    assert result.no_table_pages == [0]
    assert result.ocr_read_pages == [0]
    assert result.rows == []


def test_a_blank_scan_is_reported_as_blank(tmp_path, settings):
    registered = register_document(scan_pdf(tmp_path / "blank.pdf", [render_png(lambda page: None)]), settings)
    result = ingest_pdf(registered, settings)
    assert result.ocr_candidate_pages == [0]
    ocr_pdf_pages(registered, settings, result)
    assert result.blank_pages == [0]
    assert result.accounted_pages == 1


def test_one_failing_page_is_recorded_and_the_others_continue(tmp_path, settings, monkeypatch):
    pngs = [render_png(draw_table), render_png(draw_table)]
    registered = register_document(scan_pdf(tmp_path / "two.pdf", pngs), settings)
    result = ingest_pdf(registered, settings)

    real = ocr_ingest.words_from_image
    calls = {"count": 0}

    def flaky(image, scale):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("boom")
        return real(image, scale)

    monkeypatch.setattr(ocr_ingest, "words_from_image", flaky)
    ocr_pdf_pages(registered, settings, result)

    assert [issue.page_index for issue in result.page_errors] == [0]
    assert "boom" in result.page_errors[0].message
    assert result.table_pages == [1]
    assert result.accounted_pages == 2


def test_missing_tesseract_gives_a_clear_error(tmp_path, monkeypatch):
    # Restore the global command after this test, whatever happens.
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", pytesseract.pytesseract.tesseract_cmd)
    broken = Settings(
        data_dir=tmp_path / "data", max_upload_mb=100,
        tesseract_cmd=tmp_path / "no_such_tesseract.exe",
    )
    registered = register_document(scan_pdf(tmp_path / "scan.pdf", [render_png(draw_table)]), broken)
    result = ingest_pdf(registered, broken)
    with pytest.raises(OcrUnavailableError, match="TESSERACT_CMD"):
        ocr_pdf_pages(registered, broken, result)


def test_an_image_file_is_read_by_ocr(tmp_path, settings):
    path = tmp_path / "statement.png"
    path.write_bytes(render_png(draw_table))
    registered = register_document(path, settings)

    result = ingest_image(registered, settings)

    assert result.outcomes == [("image", "table", None)]
    assert any("pixels" in note for note in result.notes)
    cash = row_named(result.rows, "Cash")
    assert values(cash) == {"2022": "100.0", "2021": "90.0"}
    assert cash.method == ExtractionMethod.OCR
    assert cash.location.bbox.x0 > 150  # image pixels (about 208), not points (about 50)


def test_low_confidence_values_are_flagged():
    flagged = add_ocr_warnings(make_row("Cash", [("100.0", 0.95), ("90.0", 0.5)]))
    assert any("low OCR confidence" in w and "50%" in w for w in flagged.warnings)
    assert all("100.0" not in w for w in flagged.warnings)


def test_confident_or_unscored_values_are_not_flagged():
    assert add_ocr_warnings(make_row("Cash", [("100.0", 0.95), ("90.0", 0.80)])).warnings == []
    assert add_ocr_warnings(make_row("Cash", [("100.0", None)])).warnings == []


def test_numbers_with_lookalike_letters_are_recognised_as_misreads():
    for token in ("1,6l5.9", "l0O", "5B"):
        assert _looks_misread(token), token
    for token in ("1,615.9", "Cash", "OOO", "2022", "Bank"):
        assert not _looks_misread(token), token


def test_a_misread_number_in_a_label_is_flagged():
    flagged = add_ocr_warnings(make_row("Total assets 1,6l5.9", []))
    assert any("misread number" in w for w in flagged.warnings)
    assert add_ocr_warnings(make_row("Total assets", [])).warnings == []


def test_ocr_refuses_documents_of_the_wrong_type(tmp_path, settings):
    csv = tmp_path / "data.csv"
    csv.write_bytes(b"a,b\n1,2\n")
    registered_csv = register_document(csv, settings)
    with pytest.raises(OcrIngestionError):
        ocr_pdf_pages(registered_csv, settings, PdfIngestResult(registered_csv.document_id, 0))
    with pytest.raises(OcrIngestionError):
        ingest_image(registered_csv, settings)


def test_a_pdf_without_scanned_pages_is_left_alone(tmp_path, settings):
    doc = pymupdf.open()
    draw_table(doc.new_page())
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    registered = register_document(path, settings)
    result = ingest_pdf(registered, settings)
    before = (list(result.table_pages), list(result.rows))
    ocr_pdf_pages(registered, settings, result)
    assert (result.table_pages, result.rows) == before
    assert result.ocr_read_pages == []



def draw_unreadable_header(page):
    """A table whose second year heading is not a year, as when OCR garbles it."""
    page.insert_text((50, 60), "Item", fontsize=12)
    page.insert_text((400, 60), "2022", fontsize=12)
    page.insert_text((480, 60), "Prior", fontsize=12)
    for n in range(8):
        y = 80 + 20 * n
        page.insert_text((50, y), f"Row {chr(65 + n)}", fontsize=12)
        page.insert_text((400, y), f"{100 + n}.0", fontsize=12)
        page.insert_text((480, y), f"{90 + n}.0", fontsize=12)


def draw_two_numbers_without_a_header(page):
    page.insert_text((50, 60), "Item", fontsize=12)
    page.insert_text((400, 60), "Current", fontsize=12)
    page.insert_text((480, 60), "Prior", fontsize=12)
    page.insert_text((50, 80), "Cash", fontsize=12)
    page.insert_text((400, 80), "100.0", fontsize=12)
    page.insert_text((480, 80), "90.0", fontsize=12)


def test_a_table_whose_header_cannot_be_read_is_reported_as_unparsed(tmp_path, settings):
    pdf = scan_pdf(tmp_path / "bad_header.pdf", [render_png(draw_unreadable_header)])
    registered = register_document(pdf, settings)
    result = ingest_pdf(registered, settings)
    ocr_pdf_pages(registered, settings, result)
    assert result.ocr_unparsed_pages == [0]
    assert result.no_table_pages == []
    assert result.rows == []
    assert result.ocr_read_pages == [0]
    assert result.accounted_pages == result.page_count == 1


def test_a_few_numbers_without_a_header_is_not_called_unparsed(tmp_path, settings):
    pdf = scan_pdf(tmp_path / "few.pdf", [render_png(draw_two_numbers_without_a_header)])
    registered = register_document(pdf, settings)
    result = ingest_pdf(registered, settings)
    ocr_pdf_pages(registered, settings, result)
    assert result.no_table_pages == [0]
    assert result.ocr_unparsed_pages == []


def test_an_image_whose_header_cannot_be_read_is_reported_as_unparsed(tmp_path, settings):
    path = tmp_path / "bad_header.png"
    path.write_bytes(render_png(draw_unreadable_header))
    registered = register_document(path, settings)
    result = ingest_image(registered, settings)
    assert result.rows == []
    assert result.outcomes[0][:2] == ("image", "unparsed")
    assert "review" in result.outcomes[0][2]
