"""Apply a page's unit scale to parsed amounts, without guessing.

Statuses:
  scaled         a plain number on a page with one clear scale
  needs_review   the row looks like a per-share, share-count or par-value figure,
                 or like a sentence of prose, so nothing was scaled
  scale_unknown  the page states no scale
  scale_conflict the page states two different scales
  not_applicable the value is a dash, percentage, text or ambiguous
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, localcontext

from audit_rag.models.extraction import ExtractedRow
from audit_rag.normalization.amounts import ParsedAmount, parse_amount
from audit_rag.normalization.scale import ScaleResult

# Rows whose figures are often NOT in the page's scale. Extracted labels sometimes
# lose spaces ("pershare", "parvalue"), so the patterns allow for that.
UNSCALED_HINTS = re.compile(
    r"\bper\s*(?:\w+\s+){0,2}share|\bshares?\b|\bpar\s*value|\beps\b", re.I
)
PAR_VALUE = re.compile(r"\bpar\s*value", re.I)
TOTAL_ROW = re.compile(r"^\s*total\b", re.I)
SENTENCE_BREAK = re.compile(r"[a-z]\.\s+[A-Z]")


def is_heading(label: str) -> bool:
    """A row with no numbers may set context only if it really looks like a heading.

    Wrapped fragments of long labels (with digits, or par-value wording) are not
    headings; letting them set context made later rows, even totals, look per-share.
    """
    text = label.strip()
    if not text or len(text) > 80:
        return False
    if any(ch.isdigit() for ch in text) or PAR_VALUE.search(text):
        return False
    return True


def looks_like_prose(label: str) -> bool:
    """A sentence fragment picked up as a row, not a statement line."""
    text = label.strip()
    if not text:
        return False
    if text[0].islower():
        return True
    if text[0].isdigit() and len(text.split()) >= 5:
        return True
    return bool(SENTENCE_BREAK.search(text)) or len(text.split()) >= 25


@dataclass(frozen=True)
class ScaledAmount:
    """One amount with the result of applying the page scale."""

    parsed: ParsedAmount  # the amount as displayed, always kept
    status: str
    multiplier: Decimal | None
    scaled_value: Decimal | None  # set only when status is "scaled"
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScaledRow:
    row: ExtractedRow
    amounts: tuple[ScaledAmount, ...]
    context: str  # the heading text that was checked together with the row's label


def apply_scale(parsed: ParsedAmount, label: str, scale: ScaleResult) -> ScaledAmount:
    """Apply the page scale to one amount. `label` should include any heading context."""
    if parsed.kind != "number":
        return ScaledAmount(
            parsed, "not_applicable", None, None, (f"a {parsed.kind} value is not scaled",)
        )
    if scale.status == "unknown":
        return ScaledAmount(parsed, "scale_unknown", None, None, ("the page states no scale",))
    if scale.status == "conflict":
        return ScaledAmount(
            parsed, "scale_conflict", None, None, ("the page states more than one scale",)
        )
    if UNSCALED_HINTS.search(label):
        reasons = [
            "the label looks like a per-share, share-count or par-value figure; whether "
            "the page scale applies depends on the company, so it was not applied"
        ]
        if scale.mentions_exceptions:
            reasons.append("the page says the scale has exceptions")
        return ScaledAmount(parsed, "needs_review", None, None, tuple(reasons))
    with localcontext() as context:
        context.prec = 60  # exact for any realistic amount
        scaled = parsed.value * scale.multiplier
    return ScaledAmount(parsed, "scaled", scale.multiplier, scaled)


def scale_rows(
    rows: list[ExtractedRow], scale: ScaleResult, number_format: str = "us"
) -> list[ScaledRow]:
    """Parse and scale every value in the rows, using the latest heading as context.

    Context is set only by real headings, and ends at a total row.
    """
    context = ""
    result: list[ScaledRow] = []
    for row in rows:
        if not row.cells:
            if is_heading(row.label):
                context = row.label.strip()
            result.append(ScaledRow(row, (), context))
            continue
        prose = looks_like_prose(row.label)
        total = bool(TOTAL_ROW.match(row.label))
        label = row.label if total else f"{context} {row.label}".strip()
        amounts = []
        for cell in row.cells:
            parsed = parse_amount(cell.raw_text, number_format)
            if prose and parsed.kind == "number" and scale.status not in ("unknown", "conflict"):
                amounts.append(
                    ScaledAmount(
                        parsed, "needs_review", None, None,
                        ("the label looks like a sentence, not a statement line",),
                    )
                )
            else:
                amounts.append(apply_scale(parsed, label, scale))
        result.append(ScaledRow(row, tuple(amounts), context))
        if total:
            context = ""  # a total closes its section
    return result
