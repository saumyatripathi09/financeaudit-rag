"""Data shapes for raw extraction output (Phase 3).

These models hold what an extractor SAW. They do not interpret it: amounts stay
as the exact text displayed, and conversion to numbers happens in Phase 4.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractionMethod(str, Enum):
    """How a value was obtained. Reviewers trust OCR less than embedded PDF text."""

    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    SPREADSHEET = "spreadsheet"
    CSV = "csv"


class BoundingBox(BaseModel):
    """A rectangle on a page, in points measured from the top-left corner."""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _check_order(self) -> "BoundingBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bounding box corners are out of order (x1<x0 or y1<y0)")
        return self


class SourceLocation(BaseModel):
    """Where in the original file a value was found.

    page_index is ZERO-based, matching PyMuPDF and the FinanceBench answer key.
    Use page_number when showing a page to a human.
    """

    model_config = ConfigDict(frozen=True)

    page_index: int | None = Field(default=None, ge=0)
    sheet_name: str | None = None
    cell_ref: str | None = None  # e.g. "B12" in a spreadsheet
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)
    bbox: BoundingBox | None = None

    @model_validator(mode="after")
    def _needs_a_place(self) -> "SourceLocation":
        if self.page_index is None and self.sheet_name is None and self.row_index is None:
            raise ValueError("a location needs a page_index, sheet_name or row_index")
        return self

    @property
    def page_number(self) -> int | None:
        """One-based page number for people (page_index 49 is page 50)."""
        return None if self.page_index is None else self.page_index + 1


class ExtractedCell(BaseModel):
    """One value exactly as it appeared, plus where it was and how sure we are."""

    model_config = ConfigDict(frozen=True)

    raw_text: str  # the displayed text, never altered (e.g. "1,615.9" or a dash)
    location: SourceLocation
    column_label: str | None = None  # e.g. "2020", taken from the table header
    # None means "this method gives no confidence score" (embedded PDF text has
    # none). We never fake 1.0, because that would claim certainty we don't have.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ExtractedRow(BaseModel):
    """One table row: a label plus its values, with full source tracing."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    filename: str
    label: str  # the row's label exactly as displayed
    cells: list[ExtractedCell]
    location: SourceLocation  # the row as a whole
    method: ExtractionMethod
    # Issues a human reviewer should see. Empty means nothing suspicious found.
    warnings: list[str] = Field(default_factory=list)
    # Timezone-aware UTC, so timestamps are unambiguous on any machine.
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

