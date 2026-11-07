"""Phase 4 pipeline: stored rows -> scaled, labelled, statement-typed records.

Page text is not stored in the database, so for PDFs the page is read again to find its
scale and statement title. A page with no embedded text (a scan) therefore gets scale
"unknown" and its values go to review; nothing is guessed. Spreadsheets and CSV files
have no page text, so their values also stay "scale_unknown" until a scale is supplied.
Their statement type is read from the sheet name and the heading rows (rows with a label
and no numbers), then from the row labels, in the same way as for a PDF page.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from audit_rag.models.extraction import ExtractedRow
from audit_rag.normalization.records import NormalizedRecord, build_records
from audit_rag.normalization.records_repo import ensure_schema, save_records
from audit_rag.normalization.scale import ScaleResult, detect_scale
from audit_rag.normalization.scaling import ScaledRow, scale_rows
from audit_rag.normalization.statements import StatementGuess, detect_statement

NO_SCALE = ScaleResult("unknown", None, (), False)


@dataclass
class NormalizeResult:
    document_id: str
    records: list[NormalizedRecord]
    statement_by_page: dict[int | str, StatementGuess] = field(default_factory=dict)  # page, or sheet name
    scale_by_page: dict[int | str, ScaleResult] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def status_counts(self) -> dict[str, int]:
        return dict(Counter(r.scale_status for r in self.records))


def normalize_rows(
    rows: list[ExtractedRow],
    page_text: Callable[[int], str] | None,
    document_id: str = "",
) -> NormalizeResult:
    """`page_text(page_index)` returns the text of a page, or None for no page text."""
    by_page: dict[int | str, list[int]] = {}
    for index, row in enumerate(rows):
        page = row.location.page_index
        # a spreadsheet or CSV row has no page: its group is its sheet ("" when it has none)
        by_page.setdefault(page if page is not None else (row.location.sheet_name or ""), []).append(index)

    scaled: list[ScaledRow | None] = [None] * len(rows)
    guesses: dict[int | str, StatementGuess] = {}
    scales: dict[int | str, ScaleResult] = {}
    notes: list[str] = []

    for page, indexes in by_page.items():
        page_rows = [rows[i] for i in indexes]
        text = ""
        if isinstance(page, int) and page_text is not None:
            try:
                text = page_text(page) or ""
            except Exception as problem:  # one unreadable page must not stop the rest
                notes.append(f"page {page + 1}: could not read page text ({problem})")
        scale = detect_scale(text) if text.strip() else NO_SCALE
        labels = [r.label for r in page_rows]
        if isinstance(page, int):
            scales[page] = scale
            guesses[page] = detect_statement(text[:1500], labels)
        else:
            scales[page] = scale  # unknown: no page text to read a unit from
            headings = [r.label for r in page_rows if not r.cells]
            guesses[page] = detect_statement("\n".join([page, *headings]), labels)
        for index, item in zip(indexes, scale_rows(page_rows, scale)):
            scaled[index] = item

    skipped: Counter = Counter()
    for row in rows:
        if not row.cells and row.warnings and row.method.value in ("spreadsheet", "csv"):
            for text in {w.split(": ", 1)[-1] for w in row.warnings}:
                skipped[text] += 1
    for text, count in skipped.items():
        notes.append(f"{count} row(s) have no readable values: {text}")
    ordered = [item for item in scaled if item is not None]
    statement_by_page = {page: guess.statement for page, guess in guesses.items()}
    records = build_records(ordered, statement_by_page)
    return NormalizeResult(
        document_id or (rows[0].document_id if rows else ""),
        records, guesses, scales, notes,
    )


def normalize_document(
    conn: sqlite3.Connection, document_id: str, base_dir: Path | None = None
) -> NormalizeResult:
    """Normalise one stored document and save its records. The caller controls the commit."""
    from audit_rag.db.repository import load_rows  # imported here to keep the pure part light

    ensure_schema(conn)
    doc = conn.execute(
        "SELECT stored_path, file_type FROM documents WHERE document_id = ?", (document_id,)
    ).fetchone()
    if doc is None:
        raise ValueError(f"unknown document id: {document_id}")
    rows = load_rows(conn, document_id)

    if str(doc["file_type"]).lower() != "pdf":
        result = normalize_rows(rows, None, document_id)
        result.notes.append("spreadsheet or CSV: scale left unknown; statement type read from sheet names, headings and row labels")
    else:
        import pymupdf

        path = Path(doc["stored_path"])
        if base_dir is not None and (base_dir / path).exists():
            path = base_dir / path  # stored paths are relative to the data folder
        with pymupdf.open(path) as pdf:
            result = normalize_rows(rows, lambda i: pdf[i].get_text(), document_id)
    save_records(conn, document_id, result.records)
    return result
