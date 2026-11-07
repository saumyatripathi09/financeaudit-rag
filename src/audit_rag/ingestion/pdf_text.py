"""Extract table-like rows from text-based PDF pages using word positions.

PyMuPDF returns each word with a bounding box. We group words that share a
vertical position into rows, find the year header, and assign each amount to the
nearest column. Amounts stay as displayed text; conversion happens in Phase 4.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

import pymupdf

from audit_rag.models.extraction import (
    BoundingBox,
    ExtractedCell,
    ExtractedRow,
    ExtractionMethod,
    SourceLocation,
)

# Words whose vertical centers differ by no more than this many points are on
# the same row. Rows on the Amcor page are about 8 points apart.
Y_TOLERANCE = 2.0
# Words closer together than this belong to one phrase. Observed: gaps inside a
# header phrase were 3 points or less, and between real columns 46 or more.
HEADER_GAP = 15.0
# A numbers-only row and a label-only row closer than this many points are treated
# as one row. Observed: Costco prints a total's numbers 2.3 points from its label,
# while neighbouring rows were 8 or more points apart.
ORPHAN_GAP = 4.0
# Amounts must start at least this far left of the first year header's left edge.
VALUE_ZONE_MARGIN = 50.0

# Matches 1,615.9  742.6  (12.3)  -5  12.5%  and the pieces "(12.3" / "12.3)".
AMOUNT_RE = re.compile(r"^\(?\$?-?[\d,]*\d(?:\.\d+)?\)?%?$")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
# em dash, en dash and a plain hyphen: how statements show a nil value.
# Written as escapes so the file stays plain ASCII.
DASHES = {"\u2014", "\u2013", "-"}


@dataclass(frozen=True)
class Word:
    """One word with its position on the page, in points."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    # True for a year taken from a caption such as 'December 31, 2022 and 2021'.
    caption: bool = False
    # Per-word confidence from OCR, 0.0 to 1.0. None for embedded PDF text, which has no score.
    confidence: float | None = None

    @property
    def x_center(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def y_center(self) -> float:
        return (self.y0 + self.y1) / 2


def _bbox(words: list[Word]) -> BoundingBox:
    """The smallest rectangle containing all the given words."""
    return BoundingBox(
        x0=min(w.x0 for w in words),
        y0=min(w.y0 for w in words),
        x1=max(w.x1 for w in words),
        y1=max(w.y1 for w in words),
    )


def group_into_rows(words: list[Word], y_tolerance: float = Y_TOLERANCE) -> list[list[Word]]:
    """Group words into rows by vertical position, each row sorted left to right."""
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.y_center, w.x0)):
        # Compare with the FIRST word of the current row, so a long row cannot
        # slowly drift away from where it started.
        if rows and abs(word.y_center - rows[-1][0].y_center) <= y_tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w.x0) for row in rows]


def _row_y(row: list[Word]) -> float:
    """Average vertical center of a row."""
    return sum(w.y_center for w in row) / len(row)


def _is_values_only(row: list[Word]) -> bool:
    """True if every word is an amount, a dash, or a currency symbol."""
    return all(
        w.text in ("$", ")") or w.text in DASHES or AMOUNT_RE.match(w.text)
        for w in row
    )


def _is_label_only(row: list[Word]) -> bool:
    """True if no word looks like an amount or a dash."""
    return not any(w.text in DASHES or AMOUNT_RE.match(w.text) for w in row)


def merge_orphan_rows(rows: list[list[Word]]) -> list[list[Word]]:
    """Rejoin a label and its numbers when they were split into two close rows.

    Bold or larger text can sit a couple of points above or below the numbers on
    the same printed line. Only a numbers-only row next to a label-only row, closer
    than ORPHAN_GAP, is merged. A row with both a label and numbers is never touched.
    """
    merged: list[list[Word]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if index + 1 < len(rows):
            following = rows[index + 1]
            close = abs(_row_y(following) - _row_y(row)) <= ORPHAN_GAP
            complementary = (_is_values_only(row) and _is_label_only(following)) or (
                _is_label_only(row) and _is_values_only(following)
            )
            if close and complementary:
                merged.append(sorted(row + following, key=lambda w: w.x0))
                index += 2
                continue
        merged.append(row)
        index += 1
    return merged


def _header_segments(row: list[Word]) -> list[list[Word]]:
    """Split a row into phrases: words closer than HEADER_GAP stay together."""
    segments = [[row[0]]]
    for previous, word in zip(row, row[1:]):
        if word.x0 - previous.x1 > HEADER_GAP:
            segments.append([word])
        else:
            segments[-1].append(word)
    return segments


def _merge(words: list[Word]) -> Word:
    """One Word spanning several words, with their texts joined."""
    return Word(
        min(w.x0 for w in words),
        min(w.y0 for w in words),
        max(w.x1 for w in words),
        max(w.y1 for w in words),
        " ".join(w.text for w in words),
    )


def find_header(rows: list[list[Word]]) -> tuple[int, list[Word]] | None:
    """Find the first row containing two or more years, e.g. '2020 2019'.

    Returns (row index, header words) or None if no header is found. The words are
    plain years when each column has its own year, full date phrases when two
    columns share a year, and caption years (caption=True) when one phrase names
    several years.
    """
    for index, row in enumerate(rows):
        segments = [
            s for s in _header_segments(row) if any(YEAR_RE.match(w.text) for w in s)
        ]
        year_counts = [sum(1 for w in s if YEAR_RE.match(w.text)) for s in segments]
        if sum(year_counts) < 2:
            continue
        if all(n == 1 for n in year_counts):
            year_words = [next(w for w in s if YEAR_RE.match(w.text)) for s in segments]
            if len({w.text for w in year_words}) == len(year_words):
                return index, year_words  # plain years such as "2020", "2019"
            # The same year twice (31 Dec 2022 and 30 Jun 2022): keep the full phrase.
            return index, [_merge(s) for s in segments]
        # One phrase names several years ("December 31, 2022 and 2021"): a caption,
        # not column headings. Mark the years so values are not guessed by position.
        years = [
            replace(w, caption=True) for s in segments for w in s if YEAR_RE.match(w.text)
        ]
        return index, years
    return None


def _nearest_header(word: Word, headers: list[Word]) -> Word:
    """The header whose horizontal center is closest to this word's center."""
    return min(headers, key=lambda h: abs(h.x_center - word.x_center))


def _column_label(
    position: int, value: Word, value_count: int, headers: list[Word]
) -> str | None:
    """Choose the column label for one value in a row."""
    if headers[0].caption:
        # The header was one caption naming several years. Use the order the years
        # are listed, but only if the row has exactly one value per year.
        return headers[position].text if value_count == len(headers) else None
    return _nearest_header(value, headers).text


def _build_row(
    row_words: list[Word],
    headers: list[Word],
    zone_start: float,
    page_index: int,
    document_id: str,
    filename: str,
    method: ExtractionMethod = ExtractionMethod.PDF_TEXT,
) -> ExtractedRow | None:
    """Turn one row of words into an ExtractedRow, or None if the row is empty."""
    label_parts: list[str] = []
    values: list[Word] = []

    for word in row_words:
        in_zone = word.x0 >= zone_start
        if word.text == "$":
            continue  # currency symbol: shown, but not part of the amount
        if (
            in_zone
            and word.text == ")"
            and values
            and values[-1].text.startswith("(")
            and not values[-1].text.endswith(")")
        ):
            # A closing bracket printed as its own word: rejoin "(12.3" + ")".
            prev = values[-1]
            scores = [s for s in (prev.confidence, word.confidence) if s is not None]
            values[-1] = Word(
                prev.x0, prev.y0, word.x1, max(prev.y1, word.y1), prev.text + ")",
                confidence=min(scores) if scores else None,
            )
        elif in_zone and (AMOUNT_RE.match(word.text) or word.text in DASHES):
            values.append(word)
        else:
            # Anything else counts as label text, so nothing is silently dropped.
            label_parts.append(word.text)

    label = " ".join(label_parts)
    if not label and not values:
        return None

    cells = [
        ExtractedCell(
            raw_text=value.text,
            location=SourceLocation(page_index=page_index, bbox=_bbox([value])),
            column_label=_column_label(position, value, len(values), headers),
            confidence=value.confidence,
        )
        for position, value in enumerate(values)
    ]
    return ExtractedRow(
        document_id=document_id,
        filename=filename,
        label=label,
        cells=cells,
        location=SourceLocation(page_index=page_index, bbox=_bbox(row_words)),
        method=method,
    )


def extract_page_rows(
    page: pymupdf.Page, page_index: int, document_id: str, filename: str
) -> list[ExtractedRow]:
    """Extract the rows of a table-like page.

    Returns an empty list if no year header is found. That is an honest
    "no table recognised", not a guess.
    """
    words = [
        Word(x0, y0, x1, y1, text)
        for x0, y0, x1, y1, text, *_ in page.get_text("words")
    ]
    return rows_from_words(words, page_index, document_id, filename)



def _with_warnings(
    row: ExtractedRow,
    header_count: int,
    caption: bool = False,
    expect_all_values: bool = False,
) -> ExtractedRow:
    """Return the row with review warnings attached (or unchanged if clean)."""
    warnings: list[str] = []
    labels = [cell.column_label for cell in row.cells]

    if caption:
        if row.cells and len(row.cells) == header_count:
            warnings.append(
                "column labels follow the order of the years in a page caption, "
                "not real column headings; verify against the page"
            )
        elif row.cells:
            warnings.append(
                f"row has {len(row.cells)} values but the caption names "
                f"{header_count} years; column labels are unknown"
            )
    elif len(row.cells) > header_count:
        warnings.append(
            f"row has {len(row.cells)} values but the header has only "
            f"{header_count} columns; column labels are unreliable"
        )
    elif len(set(labels)) < len(labels):
        warnings.append("two values were assigned to the same column; column labels are unreliable")

    if row.cells and not row.label.strip():
        warnings.append("values found without a row label; they may belong to a nearby row")

    if expect_all_values and not caption and 0 < len(row.cells) < header_count:
        warnings.append(
            f"row has {len(row.cells)} of {header_count} expected values; "
            "a value may be missing"
        )

    trailing = 0
    for token in reversed(row.label.split()):
        if AMOUNT_RE.match(token) or token in DASHES:
            trailing += 1
        else:
            break
    if trailing >= 2:
        warnings.append(
            f"label ends with {trailing} number-like tokens; "
            "values may sit outside the expected value area"
        )

    return row.model_copy(update={"warnings": warnings}) if warnings else row



def rows_from_words(
    words: list[Word],
    page_index: int,
    document_id: str,
    filename: str,
    method: ExtractionMethod = ExtractionMethod.PDF_TEXT,
) -> list[ExtractedRow]:
    """Build rows from positioned words (embedded PDF text, or words read by OCR).

    Returns an empty list if no year header is found.
    """
    rows = merge_orphan_rows(group_into_rows(words))
    header = find_header(rows)
    if header is None:
        return []

    header_index, headers = header
    zone_start = min(h.x0 for h in headers) - VALUE_ZONE_MARGIN

    extracted: list[ExtractedRow] = []
    for row_words in rows[header_index + 1 :]:
        built = _build_row(
            row_words, headers, zone_start, page_index, document_id, filename, method
        )
        if built is not None:
            extracted.append(
                _with_warnings(
                    built,
                    len(headers),
                    headers[0].caption,
                    expect_all_values=method == ExtractionMethod.OCR,
                )
            )
    return extracted
