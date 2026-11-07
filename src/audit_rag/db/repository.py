"""Save and load extraction results in SQLite.

These functions take an open connection from open_database(), so the caller
controls the transaction: everything inside one `with` block commits together or
not at all.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from audit_rag.ingestion.grid import GridIngestResult
from audit_rag.ingestion.pdf_ingest import UNPARSED_DETAIL, PdfIngestResult
from audit_rag.models.extraction import (
    ExtractedCell,
    ExtractedRow,
    ExtractionMethod,
    SourceLocation,
)

INSERT_ROW = (
    "INSERT INTO extracted_rows (document_id, row_order, label, method, location_json, "
    "warnings_json, extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
)
INSERT_CELL = (
    "INSERT INTO extracted_cells (row_id, position, raw_text, column_label, confidence, "
    "location_json) VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_OUTCOME = (
    "INSERT INTO page_outcomes (document_id, page_index, outcome, detail) VALUES (?, ?, ?, ?)"
)
INSERT_SHEET = (
    "INSERT INTO sheet_outcomes (document_id, sheet_order, sheet_name, outcome, detail) "
    "VALUES (?, ?, ?, ?, ?)"
)
INSERT_NOTE = "INSERT INTO document_notes (document_id, note_order, note) VALUES (?, ?, ?)"


def _insert_rows(conn: sqlite3.Connection, document_id: str, rows: list[ExtractedRow]) -> None:
    """Write rows and their cells, keeping the original order."""
    for order, row in enumerate(rows):
        cursor = conn.execute(
            INSERT_ROW,
            (
                document_id,
                order,
                row.label,
                row.method.value,
                row.location.model_dump_json(),
                json.dumps(row.warnings),
                row.extracted_at.isoformat(),
            ),
        )
        for position, cell in enumerate(row.cells):
            conn.execute(
                INSERT_CELL,
                (
                    cursor.lastrowid,
                    position,
                    cell.raw_text,
                    cell.column_label,
                    cell.confidence,
                    cell.location.model_dump_json(),
                ),
            )


def _mark_ingested(conn: sqlite3.Connection, document_id: str) -> None:
    conn.execute("UPDATE documents SET status = 'ingested' WHERE document_id = ?", (document_id,))


def save_pdf_result(conn: sqlite3.Connection, result: PdfIngestResult) -> None:
    """Replace everything stored for this document with the given result."""
    # Deleting rows cascades to their cells.
    conn.execute("DELETE FROM extracted_rows WHERE document_id = ?", (result.document_id,))
    conn.execute("DELETE FROM page_outcomes WHERE document_id = ?", (result.document_id,))

    _insert_rows(conn, result.document_id, result.rows)

    # Pages that were read by OCR say so, so a report can tell the reader.
    ocr_pages = set(result.ocr_read_pages)

    def detail_for(page: int) -> str | None:
        return "read by OCR" if page in ocr_pages else None

    outcomes = (
        [(i, "table", detail_for(i)) for i in result.table_pages]
        + [(i, "no_table", detail_for(i)) for i in result.no_table_pages]
        + [(i, "ocr_unparsed", UNPARSED_DETAIL) for i in result.ocr_unparsed_pages]
        + [(i, "ocr_candidate", None) for i in result.ocr_candidate_pages]
        + [(i, "blank", detail_for(i)) for i in result.blank_pages]
        + [(issue.page_index, "error", issue.message) for issue in result.page_errors]
    )
    conn.executemany(
        INSERT_OUTCOME,
        [(result.document_id, page, outcome, detail) for page, outcome, detail in outcomes],
    )
    _mark_ingested(conn, result.document_id)


def save_grid_result(conn: sqlite3.Connection, result: GridIngestResult) -> None:
    """Replace everything stored for a CSV or workbook with the given result."""
    conn.execute("DELETE FROM extracted_rows WHERE document_id = ?", (result.document_id,))
    conn.execute("DELETE FROM sheet_outcomes WHERE document_id = ?", (result.document_id,))
    conn.execute("DELETE FROM document_notes WHERE document_id = ?", (result.document_id,))

    _insert_rows(conn, result.document_id, result.rows)

    conn.executemany(
        INSERT_SHEET,
        [
            (result.document_id, order, sheet, outcome, detail)
            for order, (sheet, outcome, detail) in enumerate(result.outcomes)
        ],
    )
    conn.executemany(
        INSERT_NOTE,
        [(result.document_id, order, note) for order, note in enumerate(result.notes)],
    )
    _mark_ingested(conn, result.document_id)


def load_rows(conn: sqlite3.Connection, document_id: str) -> list[ExtractedRow]:
    """Load the stored rows of a document, in their original order."""
    document = conn.execute(
        "SELECT original_filename FROM documents WHERE document_id = ?", (document_id,)
    ).fetchone()
    if document is None:
        return []

    rows: list[ExtractedRow] = []
    stored_rows = conn.execute(
        "SELECT * FROM extracted_rows WHERE document_id = ? ORDER BY row_order", (document_id,)
    ).fetchall()
    for stored in stored_rows:
        cells = [
            ExtractedCell(
                raw_text=c["raw_text"],
                location=SourceLocation.model_validate_json(c["location_json"]),
                column_label=c["column_label"],
                confidence=c["confidence"],
            )
            for c in conn.execute(
                "SELECT * FROM extracted_cells WHERE row_id = ? ORDER BY position",
                (stored["row_id"],),
            )
        ]
        rows.append(
            ExtractedRow(
                document_id=document_id,
                filename=document["original_filename"],
                label=stored["label"],
                cells=cells,
                location=SourceLocation.model_validate_json(stored["location_json"]),
                method=ExtractionMethod(stored["method"]),
                warnings=json.loads(stored["warnings_json"]),
                extracted_at=datetime.fromisoformat(stored["extracted_at"]),
            )
        )
    return rows


def load_page_outcomes(
    conn: sqlite3.Connection, document_id: str
) -> list[tuple[int, str, str | None]]:
    """Return (page_index, outcome, detail) for every page, in page order."""
    return [
        (r["page_index"], r["outcome"], r["detail"])
        for r in conn.execute(
            "SELECT page_index, outcome, detail FROM page_outcomes "
            "WHERE document_id = ? ORDER BY page_index",
            (document_id,),
        )
    ]


def load_sheet_outcomes(
    conn: sqlite3.Connection, document_id: str
) -> list[tuple[str, str, str | None]]:
    """Return (sheet name, outcome, detail) for every sheet, in workbook order."""
    return [
        (r["sheet_name"], r["outcome"], r["detail"])
        for r in conn.execute(
            "SELECT sheet_name, outcome, detail FROM sheet_outcomes "
            "WHERE document_id = ? ORDER BY sheet_order",
            (document_id,),
        )
    ]


def load_notes(conn: sqlite3.Connection, document_id: str) -> list[str]:
    """Return the document-level notes, in the order they were recorded."""
    return [
        r["note"]
        for r in conn.execute(
            "SELECT note FROM document_notes WHERE document_id = ? ORDER BY note_order",
            (document_id,),
        )
    ]


def replace_notes(conn: sqlite3.Connection, document_id: str, notes: list[str]) -> None:
    """Replace the document-level notes for a document."""
    conn.execute("DELETE FROM document_notes WHERE document_id = ?", (document_id,))
    conn.executemany(
        INSERT_NOTE, [(document_id, order, note) for order, note in enumerate(notes)]
    )


def mark_failed(conn: sqlite3.Connection, document_id: str) -> None:
    """Record that ingestion of a document failed."""
    conn.execute("UPDATE documents SET status = 'failed' WHERE document_id = ?", (document_id,))
