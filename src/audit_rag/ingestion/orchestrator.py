"""Ingest a registered document: pick the right reader, run OCR where needed, save."""
from __future__ import annotations

from dataclasses import dataclass, field

from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.db.repository import (
    mark_failed,
    replace_notes,
    save_grid_result,
    save_pdf_result,
)
from audit_rag.ingestion.csv_ingest import CsvIngestionError, ingest_csv
from audit_rag.ingestion.excel_ingest import ExcelIngestionError, ingest_excel
from audit_rag.ingestion.ocr_ingest import (
    OcrIngestionError,
    OcrUnavailableError,
    ingest_image,
    ocr_pdf_pages,
)
from audit_rag.ingestion.pdf_ingest import PdfIngestionError, ingest_pdf
from audit_rag.ingestion.registry import RegisteredDocument

# Errors the readers raise on purpose, with messages that are safe to show a user.
KNOWN_FAILURES = (
    PdfIngestionError,
    CsvIngestionError,
    ExcelIngestionError,
    OcrIngestionError,
    OcrUnavailableError,
)


@dataclass(frozen=True)
class IngestSummary:
    """What happened to one document."""

    document_id: str
    status: str  # "ingested" or "failed"
    row_count: int
    notes: list[str] = field(default_factory=list)
    message: str | None = None  # the reason, when status is "failed"


def ingest_document(document: RegisteredDocument, settings: Settings) -> IngestSummary:
    """Read a registered document and save what was extracted.

    Known failures are recorded and returned as a summary with status "failed".
    Unexpected errors are not caught.
    """
    try:
        if document.file_type == "pdf":
            return _ingest_pdf_document(document, settings)
        if document.file_type == "csv":
            result = ingest_csv(document, settings)
        elif document.file_type in ("xlsx", "xls"):
            result = ingest_excel(document, settings)
        elif document.file_type in ("png", "jpg"):
            result = ingest_image(document, settings)
        else:
            raise OcrIngestionError(f"{document.original_filename}: no reader for this file type")
        with open_database(settings.db_path) as conn:
            save_grid_result(conn, result)
        return IngestSummary(document.document_id, "ingested", len(result.rows), list(result.notes))
    except KNOWN_FAILURES as exc:
        with open_database(settings.db_path) as conn:
            mark_failed(conn, document.document_id)
            replace_notes(conn, document.document_id, [f"ingestion failed: {exc}"])
        return IngestSummary(document.document_id, "failed", 0, message=str(exc))


def _ingest_pdf_document(document: RegisteredDocument, settings: Settings) -> IngestSummary:
    result = ingest_pdf(document, settings)
    notes: list[str] = []
    if result.ocr_candidate_pages:
        pending = len(result.ocr_candidate_pages)
        try:
            ocr_pdf_pages(document, settings, result)
        except OcrUnavailableError as exc:
            # The text pages are still good. Keep the scanned pages recorded as unread.
            notes.append(f"{pending} scanned page(s) were not read: {exc}")
    if result.ocr_read_pages:
        notes.append(
            f"{len(result.ocr_read_pages)} page(s) were read by OCR; confirm their values "
            "against the page image"
        )
    with open_database(settings.db_path) as conn:
        save_pdf_result(conn, result)
        replace_notes(conn, document.document_id, notes)
    return IngestSummary(document.document_id, "ingested", len(result.rows), notes)
