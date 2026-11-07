"""Normalised records: one per extracted value, tying together everything Phase 4 learned.

A record never replaces the original text. It adds the parsed amount, the scaled value
(only when scaling was safe), the statement type and the canonical label (only when
recognised), and it keeps the source location, extraction method and confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from audit_rag.normalization.labels import canonical_label
from audit_rag.normalization.scaling import ScaledRow


@dataclass(frozen=True)
class NormalizedRecord:
    document_id: str
    row_order: int  # index of the row in load_rows() order
    cell_position: int  # index of the cell within the row
    page_index: int | None
    statement_type: str  # "unknown" when not decided
    label: str  # original label, as displayed
    label_canonical: str | None
    column_label: str | None
    raw_text: str  # original text, never altered
    kind: str  # number, dash, percentage, text, ambiguous
    displayed_value: Decimal | None  # parsed, before scaling
    scaled_value: Decimal | None  # set only when scaling was safe
    scale_status: str
    multiplier: Decimal | None
    reasons: tuple[str, ...]
    method: str
    confidence: float | None
    row_warnings: tuple[str, ...]
    sheet_name: str | None = None
    cell_ref: str | None = None


def build_records(
    scaled_rows: list[ScaledRow],
    statement_by_page: dict[int | str, str] | None = None,
) -> list[NormalizedRecord]:
    """Turn scaled rows into records. `scaled_rows` must keep load_rows() order."""
    statement_by_page = statement_by_page or {}
    records: list[NormalizedRecord] = []
    for row_order, scaled in enumerate(scaled_rows):
        row = scaled.row
        canonical = canonical_label(row.label).canonical
        page = row.location.page_index
        key = page if page is not None else (row.location.sheet_name or "")
        statement = statement_by_page.get(key, "unknown")
        by_column = row.method.value in ("spreadsheet", "csv")
        for index, (cell, amount) in enumerate(zip(row.cells, scaled.amounts)):
            # a spreadsheet has exact columns: pair by column, so a shifted row is never
            # matched with the wrong year
            position = (cell.location.column_index
                        if by_column and cell.location.column_index is not None else index)
            records.append(
                NormalizedRecord(
                    document_id=row.document_id,
                    row_order=row_order,
                    cell_position=position,
                    page_index=cell.location.page_index
                    if cell.location.page_index is not None else page,
                    statement_type=statement,
                    label=row.label,
                    label_canonical=canonical,
                    column_label=cell.column_label,
                    raw_text=cell.raw_text,
                    kind=amount.parsed.kind,
                    displayed_value=amount.parsed.value,
                    scaled_value=amount.scaled_value,
                    scale_status=amount.status,
                    multiplier=amount.multiplier,
                    reasons=tuple(amount.reasons),
                    method=row.method.value,
                    confidence=cell.confidence,
                    row_warnings=tuple(row.warnings),
                    sheet_name=cell.location.sheet_name or row.location.sheet_name,
                    cell_ref=cell.location.cell_ref,
                )
            )
    return records
