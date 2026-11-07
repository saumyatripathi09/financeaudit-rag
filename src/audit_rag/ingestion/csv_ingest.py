"""Ingest a registered CSV file into extracted rows."""
from __future__ import annotations

import csv
import io

from audit_rag.config import Settings
from audit_rag.ingestion.grid import (
    GridCell,
    GridIngestResult,
    build_rows,
    cell_text,
    column_letter,
)
from audit_rag.ingestion.registry import RegisteredDocument
from audit_rag.models.extraction import ExtractionMethod

ENCODINGS = ("utf-8-sig", "cp1252")
DELIMITERS = ",;\t|"


class CsvIngestionError(Exception):
    """The CSV could not be processed. The message is safe to show the user."""


def _detect_delimiter(text: str) -> str:
    """Use the delimiter most common on the first line that contains any; default comma."""
    for line in text.splitlines()[:20]:
        counts = {d: line.count(d) for d in DELIMITERS}
        best = max(counts, key=counts.get)
        if counts[best] > 0:
            return best
    return ","


def ingest_csv(document: RegisteredDocument, settings: Settings) -> GridIngestResult:
    """Read a registered CSV and extract its table rows."""
    if document.file_type != "csv":
        raise CsvIngestionError(f"{document.original_filename} is not a CSV file")

    path = settings.data_dir / document.stored_path
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CsvIngestionError(f"{document.original_filename} could not be read") from exc

    result = GridIngestResult(document_id=document.document_id)

    text = None
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding != "utf-8-sig":
            result.notes.append(
                "decoded as Windows-1252 because the file is not valid UTF-8; "
                "check accented characters"
            )
        break
    if text is None:
        raise CsvIngestionError(f"{document.original_filename} uses an unsupported text encoding")

    try:
        parsed = list(csv.reader(io.StringIO(text), delimiter=_detect_delimiter(text)))
    except csv.Error as exc:
        raise CsvIngestionError(f"{document.original_filename} is not valid CSV: {exc}") from exc

    grid = [
        [GridCell(value=value, ref=f"{column_letter(c)}{r + 1}") for c, value in enumerate(row)]
        for r, row in enumerate(parsed)
    ]
    if not any(cell_text(cell.value) for row in grid for cell in row):
        result.outcomes.append(("csv", "blank", None))
        return result

    result.rows = build_rows(
        grid,
        document_id=document.document_id,
        filename=document.original_filename,
        method=ExtractionMethod.CSV,
    )
    result.outcomes.append(("csv", "table" if result.rows else "no_table", None))
    return result
