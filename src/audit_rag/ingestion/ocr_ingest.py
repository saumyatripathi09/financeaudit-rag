"""Read scanned PDF pages and image files with Tesseract (local OCR).

OCR output is never treated as ground truth. Every value keeps Tesseract's
confidence, low-confidence values and likely misreads are flagged for review, and
every row carries method=ocr.
"""
from __future__ import annotations

import pymupdf
import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

from audit_rag.config import Settings
from audit_rag.ingestion.grid import GridIngestResult, is_value_text
from audit_rag.ingestion.pdf_ingest import UNPARSED_DETAIL, PageIssue, PdfIngestResult
from audit_rag.ingestion.pdf_text import YEAR_RE, Word, rows_from_words
from audit_rag.ingestion.registry import RegisteredDocument
from audit_rag.models.extraction import ExtractedRow, ExtractionMethod

# Resolution used to render a PDF page before OCR. Tesseract reads best near 300 dpi.
RENDER_DPI = 300
# Tesseract page-segmentation mode 6: "a single uniform block of text". A starting
# point chosen without data; compare modes on real scans before trusting it.
OCR_CONFIG = "--psm 6"
# Values Tesseract is less than 80% sure about are flagged. Also chosen without data.
LOW_CONFIDENCE = 0.80
# A scanned page counts as unparsed (a table may have been lost) when OCR read at
# least this many numbers but found no table header. A guess; tune on real scans.
UNPARSED_MIN_VALUES = 10
# Letters Tesseract often reads in place of digits (O/o for 0, I/l/| for 1, S for 5, B for 8).
LOOKALIKES = "OolISB|"
_NUMBER_CHARS = set("0123456789,.()$%-" + LOOKALIKES)


class OcrUnavailableError(Exception):
    """Tesseract could not be started. The message is safe to show the user."""


class OcrIngestionError(Exception):
    """The file could not be read by OCR. The message is safe to show the user."""


def _prepare_tesseract(settings: Settings) -> None:
    """Point pytesseract at the configured Tesseract and check that it starts."""
    # Always set the command, so an earlier (wrong) value cannot linger.
    command = str(settings.tesseract_cmd) if settings.tesseract_cmd else "tesseract"
    pytesseract.pytesseract.tesseract_cmd = command
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        raise OcrUnavailableError(
            "Tesseract OCR could not be started; check TESSERACT_CMD in .env"
        ) from exc


def words_from_image(image: Image.Image, scale: float) -> list[Word]:
    """Run Tesseract on an image. Positions are multiplied by `scale`.

    Use scale = 72 / dpi for a rendered PDF page (pixels become points), or 1.0
    for an image file (positions stay in pixels).
    """
    data = pytesseract.image_to_data(image, config=OCR_CONFIG, output_type=Output.DICT)
    words: list[Word] = []
    for i, raw in enumerate(data["text"]):
        text = str(raw).strip()
        confidence = float(data["conf"][i])
        if not text or confidence < 0:  # Tesseract uses -1 for layout entries that are not words
            continue
        left, top = float(data["left"][i]), float(data["top"][i])
        width, height = float(data["width"][i]), float(data["height"][i])
        words.append(
            Word(
                left * scale,
                top * scale,
                (left + width) * scale,
                (top + height) * scale,
                text,
                confidence=min(confidence / 100.0, 1.0),
            )
        )
    return words


def _looks_misread(token: str) -> bool:
    """True for a token of digits mixed with letters Tesseract confuses with digits."""
    return (
        any(ch.isdigit() for ch in token)
        and any(ch in LOOKALIKES for ch in token)
        and all(ch in _NUMBER_CHARS for ch in token)
    )


def add_ocr_warnings(row: ExtractedRow, threshold: float = LOW_CONFIDENCE) -> ExtractedRow:
    """Attach review warnings for low-confidence values and likely misreads."""
    warnings = list(row.warnings)

    low = [c for c in row.cells if c.confidence is not None and c.confidence < threshold]
    if low:
        shown = ", ".join(f"{c.raw_text} ({c.confidence:.0%})" for c in low[:4])
        warnings.append(f"low OCR confidence: {shown}; check against the image")

    suspicious = [t for t in row.label.split() if _looks_misread(t)]
    if suspicious:
        warnings.append(
            "label contains text that may be a misread number: " + ", ".join(suspicious[:3])
        )

    return row.model_copy(update={"warnings": warnings}) if warnings != row.warnings else row


def read_image_rows(
    image: Image.Image,
    *,
    page_index: int,
    document_id: str,
    filename: str,
    scale: float,
) -> tuple[list[ExtractedRow], str]:
    """OCR one image. Returns (rows, status).

    status is "table" (rows found), "unparsed" (many numbers were read but no table
    header was recognised, so a table may have been lost), "no_table" (text but
    nothing table-like) or "blank" (no text at all).
    """
    words = words_from_image(image, scale)
    if not words:
        return [], "blank"
    rows = rows_from_words(words, page_index, document_id, filename, ExtractionMethod.OCR)
    if rows:
        return [add_ocr_warnings(row) for row in rows], "table"
    numbers = sum(
        1 for w in words if is_value_text(w.text) and not YEAR_RE.match(w.text)
    )
    return [], "unparsed" if numbers >= UNPARSED_MIN_VALUES else "no_table"


def ocr_pdf_pages(
    document: RegisteredDocument,
    settings: Settings,
    result: PdfIngestResult,
    dpi: int = RENDER_DPI,
) -> PdfIngestResult:
    """Run OCR on the pages the PDF reader marked as scans, and update the result.

    Each scanned page moves from ocr_candidate_pages to exactly one of table_pages,
    no_table_pages, blank_pages or page_errors, and is also listed in ocr_read_pages.
    """
    if document.file_type != "pdf":
        raise OcrIngestionError(f"{document.original_filename} is not a PDF")
    pages = list(result.ocr_candidate_pages)
    if not pages:
        return result

    _prepare_tesseract(settings)
    path = settings.data_dir / document.stored_path
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise OcrIngestionError(f"{document.original_filename} could not be opened") from exc

    scale = 72.0 / dpi
    with doc:
        for index in pages:
            result.ocr_candidate_pages.remove(index)
            try:
                pixmap = doc[index].get_pixmap(dpi=dpi)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                rows, status = read_image_rows(
                    image,
                    page_index=index,
                    document_id=document.document_id,
                    filename=document.original_filename,
                    scale=scale,
                )
            except Exception as exc:  # one bad page must not stop the others
                result.page_errors.append(PageIssue(index, f"OCR failed: {type(exc).__name__}: {exc}"))
                continue
            result.ocr_read_pages.append(index)
            if status == "table":
                result.table_pages.append(index)
                result.rows.extend(rows)
            elif status == "unparsed":
                result.ocr_unparsed_pages.append(index)
            elif status == "no_table":
                result.no_table_pages.append(index)
            else:
                result.blank_pages.append(index)

    result.table_pages.sort()
    result.no_table_pages.sort()
    result.blank_pages.sort()
    result.ocr_unparsed_pages.sort()
    result.ocr_read_pages.sort()
    result.page_errors.sort(key=lambda issue: issue.page_index)
    result.rows.sort(key=lambda row: row.location.page_index)  # stable within a page
    return result


def ingest_image(document: RegisteredDocument, settings: Settings) -> GridIngestResult:
    """OCR a registered PNG or JPG file and extract table rows."""
    name = document.original_filename
    if document.file_type not in ("png", "jpg"):
        raise OcrIngestionError(f"{name} is not an image file")

    _prepare_tesseract(settings)
    path = settings.data_dir / document.stored_path
    try:
        with Image.open(path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise OcrIngestionError(f"{name} could not be opened as an image") from exc

    result = GridIngestResult(document_id=document.document_id)
    result.notes.append(
        "text read by OCR; positions are in image pixels, not points, and the "
        "scale of the original is unknown"
    )
    try:
        rows, status = read_image_rows(
            image,
            page_index=0,
            document_id=document.document_id,
            filename=name,
            scale=1.0,
        )
    except Exception as exc:
        result.outcomes.append(("image", "error", f"OCR failed: {type(exc).__name__}: {exc}"))
        return result

    result.rows = rows
    detail = UNPARSED_DETAIL if status == "unparsed" else None
    result.outcomes.append(("image", status, detail))
    return result
