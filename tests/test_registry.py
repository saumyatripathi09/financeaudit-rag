"""Tests for the document registry, using small synthetic files."""
import shutil
import zipfile
from pathlib import Path

import openpyxl
import pymupdf
import pytest
from PIL import Image

from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.ingestion import registry
from audit_rag.ingestion.registry import (
    ContentMismatchError,
    EmptyFileError,
    FileTooLargeError,
    RegistrationError,
    UnsupportedFileTypeError,
    _safe_filename,
    register_document,
)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=1)


def make_pdf(path, text="hello"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def count_rows(settings):
    with open_database(settings.db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def test_registers_a_pdf_and_stores_a_readable_copy(tmp_path, settings):
    result = register_document(make_pdf(tmp_path / "report.pdf"), settings)
    assert result.file_type == "pdf"
    assert result.document_id.startswith("doc-")
    assert result.status == "registered"
    assert not result.is_duplicate
    stored = settings.data_dir / result.stored_path
    assert stored.is_file()
    with pymupdf.open(stored) as doc:
        assert doc.page_count == 1
    assert count_rows(settings) == 1


def test_every_supported_type_registers(tmp_path, settings):
    xlsx = tmp_path / "book.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = 1
    workbook.save(str(xlsx))

    csv = tmp_path / "data.csv"
    csv.write_bytes(b"a,b\n1,2\n")

    png = tmp_path / "scan.png"
    Image.new("RGB", (4, 4), "white").save(str(png))

    jpg = tmp_path / "scan.jpg"
    Image.new("RGB", (4, 4), "black").save(str(jpg), "JPEG")

    # Signature-only fixture: real .xls parsing is tested when the Excel reader exists.
    xls = tmp_path / "old.xls"
    xls.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)

    types = {
        register_document(path, settings).file_type
        for path in (make_pdf(tmp_path / "a.pdf"), xlsx, csv, png, jpg, xls)
    }
    assert types == {"pdf", "xlsx", "csv", "png", "jpg", "xls"}


def test_the_same_file_twice_is_recognised_as_a_duplicate(tmp_path, settings):
    path = make_pdf(tmp_path / "report.pdf")
    first = register_document(path, settings)
    second = register_document(path, settings)
    assert second.is_duplicate
    assert second.document_id == first.document_id
    assert count_rows(settings) == 1
    assert len(list(settings.uploads_dir.glob("*/*"))) == 1


def test_same_content_under_another_name_is_a_duplicate(tmp_path, settings):
    original = make_pdf(tmp_path / "first.pdf")
    renamed = tmp_path / "second.pdf"
    shutil.copyfile(original, renamed)
    first = register_document(original, settings)
    second = register_document(renamed, settings)
    assert second.is_duplicate
    assert second.document_id == first.document_id
    assert second.original_filename == "first.pdf"  # the existing record is returned


def test_a_text_file_named_pdf_is_rejected_and_leaves_no_trace(tmp_path, settings):
    fake = tmp_path / "fake.pdf"
    fake.write_text("this is not a pdf")
    with pytest.raises(ContentMismatchError):
        register_document(fake, settings)
    assert count_rows(settings) == 0
    assert list(settings.uploads_dir.glob("*")) == []


def test_a_zip_posing_as_xlsx_is_rejected(tmp_path, settings):
    fake = tmp_path / "fake.xlsx"
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr("word/document.xml", "<doc/>")
    with pytest.raises(ContentMismatchError):
        register_document(fake, settings)


def test_unsupported_extension_is_rejected(tmp_path, settings):
    path = tmp_path / "program.exe"
    path.write_bytes(b"MZ\x00\x00")
    with pytest.raises(UnsupportedFileTypeError):
        register_document(path, settings)


def test_empty_file_is_rejected(tmp_path, settings):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(EmptyFileError):
        register_document(path, settings)


def test_file_over_the_size_limit_is_rejected(tmp_path, settings):
    path = tmp_path / "big.csv"
    path.write_bytes(b"a" * (1024 * 1024 + 1))
    with pytest.raises(FileTooLargeError):
        register_document(path, settings)


def test_missing_file_is_reported(tmp_path, settings):
    with pytest.raises(RegistrationError):
        register_document(tmp_path / "nope.pdf", settings)


def test_stored_path_is_relative_to_the_data_folder(tmp_path, settings):
    result = register_document(make_pdf(tmp_path / "report.pdf"), settings)
    assert not Path(result.stored_path).is_absolute()
    assert ":" not in result.stored_path
    assert "\\" not in result.stored_path


def test_unsafe_filenames_are_cleaned():
    assert _safe_filename("../../x y?.pdf", ".pdf") == "x y_.pdf"
    assert _safe_filename("..", ".pdf") == "document.pdf"
    assert _safe_filename("report", ".pdf") == "report.pdf"


def test_failed_copy_leaves_no_database_row_and_no_folder(tmp_path, settings, monkeypatch):
    def broken_copy(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(registry.shutil, "copyfile", broken_copy)
    with pytest.raises(OSError):
        register_document(make_pdf(tmp_path / "report.pdf"), settings)
    assert count_rows(settings) == 0
    assert list(settings.uploads_dir.glob("*")) == []
