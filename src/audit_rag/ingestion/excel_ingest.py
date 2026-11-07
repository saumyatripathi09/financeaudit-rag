"""Ingest a registered .xlsx workbook into extracted rows, sheet by sheet."""
from __future__ import annotations

import openpyxl

from audit_rag.config import Settings
from audit_rag.ingestion.grid import (
    GridCell,
    GridIngestResult,
    build_rows,
    cell_text,
)
from audit_rag.ingestion.registry import RegisteredDocument
from audit_rag.models.extraction import ExtractionMethod

MAX_ROWS = 20000
MAX_COLS = 200

STORED_VALUES_NOTE = (
    "values are the numbers stored in the workbook, not the formatted text Excel "
    "displays (for example thousands separators are not preserved)"
)


class ExcelIngestionError(Exception):
    """The workbook could not be processed. The message is safe to show the user."""


def _hidden_columns(sheet) -> set[int]:
    """Zero-based indexes of hidden columns (a dimension can cover a range)."""
    hidden: set[int] = set()
    for dim in sheet.column_dimensions.values():
        if dim.hidden and dim.min and dim.max:
            hidden.update(range(dim.min - 1, dim.max))
    return hidden


def _read_sheet(values_sheet, formulas_sheet):
    """Read one sheet into a grid. Returns (grid, row notes, truncated?)."""
    total_rows = values_sheet.max_row or 0
    total_cols = values_sheet.max_column or 0
    truncated = total_rows > MAX_ROWS or total_cols > MAX_COLS
    hidden_cols = _hidden_columns(values_sheet)

    grid: list[list[GridCell]] = []
    row_notes: dict[int, str] = {}
    for r in range(1, min(total_rows, MAX_ROWS) + 1):
        if values_sheet.row_dimensions[r].hidden:
            row_notes[r - 1] = "row is hidden in the workbook"
        row: list[GridCell] = []
        for c in range(1, min(total_cols, MAX_COLS) + 1):
            stored = values_sheet.cell(row=r, column=c)
            original = formulas_sheet.cell(row=r, column=c)
            note = None
            if stored.data_type == "e":
                note = "Excel error value"
            elif original.data_type == "f" and stored.value is None:
                note = (
                    "formula with no stored result (the workbook was never calculated); "
                    "value unknown"
                )
            elif (c - 1) in hidden_cols and stored.value is not None:
                note = "value is in a hidden column"
            row.append(
                GridCell(
                    value=stored.value,
                    ref=stored.coordinate,
                    number_format=stored.number_format,
                    note=note,
                )
            )
        grid.append(row)
    return grid, row_notes, truncated


def ingest_excel(document: RegisteredDocument, settings: Settings) -> GridIngestResult:
    """Read a registered .xlsx workbook and extract table rows from every sheet."""
    name = document.original_filename
    if document.file_type == "xls":
        raise ExcelIngestionError(
            f"{name} is an old-format .xls workbook, which is not supported yet; "
            "open it in Excel and save it as .xlsx"
        )
    if document.file_type != "xlsx":
        raise ExcelIngestionError(f"{name} is not an Excel workbook")

    path = settings.data_dir / document.stored_path
    try:
        values_book = openpyxl.load_workbook(path, data_only=True)
        formulas_book = openpyxl.load_workbook(path, data_only=False)
    except Exception as exc:  # openpyxl raises several different types for bad files
        raise ExcelIngestionError(f"{name} could not be opened as an .xlsx workbook") from exc

    result = GridIngestResult(document_id=document.document_id)
    result.notes.append(STORED_VALUES_NOTE)
    try:
        for sheet_name in values_book.sheetnames:
            values_sheet = values_book[sheet_name]
            formulas_sheet = formulas_book[sheet_name]
            hidden = values_sheet.sheet_state != "visible"
            try:
                grid, row_notes, truncated = _read_sheet(values_sheet, formulas_sheet)
                if not any(cell_text(c.value) or c.note for row in grid for c in row):
                    result.outcomes.append((sheet_name, "blank", None))
                    continue
                rows = build_rows(
                    grid,
                    document_id=document.document_id,
                    filename=name,
                    method=ExtractionMethod.SPREADSHEET,
                    sheet_name=sheet_name,
                    row_notes=row_notes,
                )
            except Exception as exc:  # one bad sheet must not stop the others
                result.outcomes.append((sheet_name, "error", f"{type(exc).__name__}: {exc}"))
                continue

            if hidden:
                rows = [r.model_copy(update={"warnings": ["sheet is hidden", *r.warnings]}) for r in rows]
            details = []
            if hidden:
                details.append("hidden sheet")
            if truncated:
                details.append(f"truncated to the first {MAX_ROWS} rows and {MAX_COLS} columns")
            if values_sheet.merged_cells.ranges:
                result.notes.append(
                    f"{sheet_name}: contains merged cells; a merged value appears only "
                    "in the top-left cell of its range"
                )
            result.outcomes.append(
                (sheet_name, "table" if rows else "no_table", "; ".join(details) or None)
            )
            result.rows.extend(rows)
    finally:
        values_book.close()
        formulas_book.close()
    return result
