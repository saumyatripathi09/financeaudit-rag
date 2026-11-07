"""Turn a grid of cells (a worksheet or a CSV file) into ExtractedRow records.

Unlike a PDF, a spreadsheet has exact columns, so each value takes the heading of
the header cell in its own column. No guessing by position is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from audit_rag.ingestion.pdf_text import AMOUNT_RE, DASHES
from audit_rag.models.extraction import (
    ExtractedCell,
    ExtractedRow,
    ExtractionMethod,
    SourceLocation,
)

YEAR_IN_TEXT = re.compile(r"(?:19|20)\d{2}")
MAX_HEADER_CHARS = 40  # a long sentence that mentions a year is prose, not a heading


@dataclass(frozen=True)
class GridCell:
    """One cell: its value, where it is, and any problem noticed while reading it."""

    value: object  # str, int, float, date, or None for an empty cell
    ref: str  # e.g. "B7"
    number_format: str | None = None
    note: str | None = None  # e.g. "formula with no stored result"


@dataclass
class GridIngestResult:
    """Everything that happened to a CSV or workbook."""

    document_id: str
    rows: list[ExtractedRow] = field(default_factory=list)
    # (sheet name, "table" | "no_table" | "blank" | "error", detail)
    outcomes: list[tuple[str, str, str | None]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # document-level remarks


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, like a spreadsheet's column names."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_text(value: object) -> str:
    """A cell's content as text, with numbers written the way a person would read them."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if "e" in text or "E" in text:
            text = format(Decimal(text), "f")  # avoid scientific notation like 1e-07
        return text
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def is_value_text(text: str) -> bool:
    """True if the text looks like an amount or a dash (a nil value)."""
    return bool(text) and (text in DASHES or bool(AMOUNT_RE.match(text)))


def find_grid_header(
    grid: list[list[GridCell]],
) -> tuple[int, dict[int, str], set[int]] | None:
    """Find the heading row: two or more distinct year-like cells.

    Returns (row index, {column: heading text}, {columns that hold years}) or None.
    """
    for index, row in enumerate(grid):
        texts: dict[int, str] = {}
        for col, cell in enumerate(row):
            text = cell_text(cell.value)
            if text:
                texts[col] = text
        year_cols = {
            c for c, t in texts.items()
            if len(t) <= MAX_HEADER_CHARS and YEAR_IN_TEXT.search(t)
        }
        if len(year_cols) < 2:
            continue
        if len({texts[c] for c in year_cols}) < len(year_cols):
            continue  # the same heading twice: the columns would be ambiguous
        return index, texts, year_cols
    return None


def build_rows(
    grid: list[list[GridCell]],
    *,
    document_id: str,
    filename: str,
    method: ExtractionMethod,
    sheet_name: str | None = None,
    row_notes: dict[int, str] | None = None,
) -> list[ExtractedRow]:
    """Convert a grid into rows. Returns [] if no year heading is found."""
    header = find_grid_header(grid)
    if header is None:
        return []
    header_index, header_texts, year_cols = header
    first_year_col = min(year_cols)
    row_notes = row_notes or {}

    extracted: list[ExtractedRow] = []
    for r in range(header_index + 1, len(grid)):
        label_parts: list[str] = []
        cells: list[ExtractedCell] = []
        refs: list[str] = []
        warnings: list[str] = []
        if r in row_notes:
            warnings.append(row_notes[r])

        for c, gc in enumerate(grid[r]):
            if gc.note:
                warnings.append(f"{gc.ref}: {gc.note}")
                refs.append(gc.ref)
            text = cell_text(gc.value)
            if not text:
                continue
            refs.append(gc.ref)
            value_like = is_value_text(text)

            # Text to the left of the first year column is the row label. A number
            # there (such as a note reference) is kept as a cell, not mixed into it.
            if c < first_year_col and not value_like:
                label_parts.append(text)
                continue

            column_label = header_texts.get(c)
            if column_label is None:
                warnings.append(
                    f"{gc.ref}: value in a column with no heading, so its column label is unknown"
                )
            elif c in year_cols and not value_like:
                warnings.append(f"{gc.ref}: non-numeric text {text[:30]!r} in a year column")
            if gc.number_format and "%" in gc.number_format and value_like:
                warnings.append(
                    f"{gc.ref}: formatted as a percentage; the stored value is the underlying number"
                )
            cells.append(
                ExtractedCell(
                    raw_text=text,
                    location=SourceLocation(
                        sheet_name=sheet_name, cell_ref=gc.ref, row_index=r, column_index=c
                    ),
                    column_label=column_label,
                )
            )

        if not refs:
            continue  # nothing at all on this row
        label = " ".join(label_parts)
        if not label and not cells and not warnings:
            continue
        if cells and not label:
            warnings.append("values found without a row label; they may belong to a nearby row")
        extracted.append(
            ExtractedRow(
                document_id=document_id,
                filename=filename,
                label=label,
                cells=cells,
                location=SourceLocation(
                    sheet_name=sheet_name, cell_ref=f"{refs[0]}:{refs[-1]}", row_index=r
                ),
                method=method,
                warnings=warnings,
            )
        )
    return extracted
