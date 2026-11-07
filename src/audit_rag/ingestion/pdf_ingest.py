"""Ingest a registered PDF: extract table rows page by page and account for every page."""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from audit_rag.config import Settings
from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.ingestion.registry import RegisteredDocument
from audit_rag.models.extraction import ExtractedRow

# A page with fewer text characters than this is treated as having no real text.
MIN_TEXT_CHARS = 20
# Recorded for a scanned page where OCR read many numbers but found no table header.
UNPARSED_DETAIL = "numbers were read by OCR but no table header was recognised; review the page"


class PdfIngestionError(Exception):
    """The PDF could not be processed. The message is safe to show the user."""


@dataclass(frozen=True)
class PageIssue:
    page_index: int
    message: str


@dataclass
class PdfIngestResult:
    """Everything that happened to a PDF. Every page is in exactly one list."""

    document_id: str
    page_count: int
    rows: list[ExtractedRow] = field(default_factory=list)
    table_pages: list[int] = field(default_factory=list)
    no_table_pages: list[int] = field(default_factory=list)
    ocr_candidate_pages: list[int] = field(default_factory=list)
    blank_pages: list[int] = field(default_factory=list)
    page_errors: list[PageIssue] = field(default_factory=list)
    # Pages read by OCR. They also appear in one of the lists above.
    ocr_read_pages: list[int] = field(default_factory=list)
    # Scanned pages where numbers were read but no table header was found.
    ocr_unparsed_pages: list[int] = field(default_factory=list)

    @property
    def accounted_pages(self) -> int:
        return (
            len(self.table_pages)
            + len(self.no_table_pages)
            + len(self.ocr_candidate_pages)
            + len(self.blank_pages)
            + len(self.page_errors)
            + len(self.ocr_unparsed_pages)
        )


def ingest_pdf(document: RegisteredDocument, settings: Settings) -> PdfIngestResult:
    """Extract table rows from every page of a registered PDF."""
    if document.file_type != "pdf":
        raise PdfIngestionError(f"{document.original_filename} is not a PDF")

    path = settings.data_dir / document.stored_path
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf raises several different types for broken files
        raise PdfIngestionError(
            f"{document.original_filename} could not be opened as a PDF"
        ) from exc

    with doc:
        if doc.needs_pass:
            raise PdfIngestionError(f"{document.original_filename} is password protected")
        if doc.page_count == 0:
            raise PdfIngestionError(f"{document.original_filename} has no pages")

        result = PdfIngestResult(document_id=document.document_id, page_count=doc.page_count)
        for index in range(doc.page_count):
            page = doc[index]
            try:
                if len(page.get_text().strip()) < MIN_TEXT_CHARS:
                    # Little text: a scan if it holds an image, otherwise blank.
                    if page.get_images():
                        result.ocr_candidate_pages.append(index)
                    else:
                        result.blank_pages.append(index)
                    continue
                rows = extract_page_rows(page, index, document.document_id, document.original_filename)
            except Exception as exc:  # one bad page must not stop the others
                result.page_errors.append(PageIssue(index, f"{type(exc).__name__}: {exc}"))
                continue
            if rows:
                result.table_pages.append(index)
                result.rows.extend(rows)
            else:
                result.no_table_pages.append(index)
    return result
