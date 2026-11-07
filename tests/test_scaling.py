"""Tests for applying the page scale to amounts."""
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.models.extraction import (
    ExtractedCell,
    ExtractedRow,
    ExtractionMethod,
    SourceLocation,
)
from audit_rag.normalization.amounts import parse_amount
from audit_rag.normalization.scale import detect_scale
from audit_rag.normalization.scaling import apply_scale, scale_rows

MILLIONS = detect_scale("(in millions)")
THOUSANDS = detect_scale("(in thousands)")
WITH_EXCEPTIONS = detect_scale("(in millions, except per share data)")


def make_row(label, raws):
    location = SourceLocation(page_index=0)
    return ExtractedRow(
        document_id="d", filename="f.pdf", label=label, location=location,
        method=ExtractionMethod.PDF_TEXT,
        cells=[ExtractedCell(raw_text=raw, location=location, column_label="2020") for raw in raws],
    )


def test_a_plain_number_is_scaled_exactly():
    result = apply_scale(parse_amount("1,615.9"), "Trade receivables, net", MILLIONS)
    assert result.status == "scaled" and result.multiplier == Decimal(1000000)
    assert result.scaled_value == Decimal("1615900000")
    assert result.parsed.value == Decimal("1615.9")  # the displayed amount is kept


def test_thousands_and_no_float_error():
    result = apply_scale(parse_amount("0.1"), "Other", THOUSANDS)
    assert result.scaled_value == Decimal("100")


def test_a_negative_amount_stays_negative():
    result = apply_scale(parse_amount("(1,049.3)"), "Accumulated other comprehensive loss", MILLIONS)
    assert result.scaled_value == Decimal("-1049300000")


def test_a_per_share_label_is_not_scaled_and_needs_review():
    result = apply_scale(parse_amount("6.15"), "Diluted earnings per share", WITH_EXCEPTIONS)
    assert result.status == "needs_review" and result.scaled_value is None
    assert any("exceptions" in reason for reason in result.reasons)


def test_labels_that_lost_their_spaces_are_still_caught():
    for label in ("Earnings pershare", "Common stock parvalue", "Basic EPS", "Shares outstanding"):
        assert apply_scale(parse_amount("1.5"), label, MILLIONS).status == "needs_review", label


def test_a_per_share_row_is_flagged_even_when_the_page_mentions_no_exception():
    result = apply_scale(parse_amount("6.15"), "Diluted earnings per share", MILLIONS)
    assert result.status == "needs_review"


def test_unknown_and_conflicting_scales_give_no_value():
    unknown = apply_scale(parse_amount("1.5"), "Cash", detect_scale("no statement here"))
    conflict = apply_scale(parse_amount("1.5"), "Cash", detect_scale("(in thousands) (in millions)"))
    assert unknown.status == "scale_unknown" and unknown.scaled_value is None
    assert conflict.status == "scale_conflict" and conflict.scaled_value is None


def test_dashes_percentages_text_and_ambiguous_values_are_not_scaled():
    for raw in ("\u2014", "12.5%", "Audited", "12,5"):
        result = apply_scale(parse_amount(raw), "Cash", MILLIONS)
        assert result.status == "not_applicable" and result.scaled_value is None, raw


def test_a_heading_gives_context_to_the_rows_below_it():
    rows = [
        make_row("Net income", ["10.0"]),
        make_row("Weighted-average shares used in computation of earnings per share:", []),
        make_row("Diluted", ["477"]),
        make_row("Total assets", ["900.0"]),
    ]
    scaled = scale_rows(rows, MILLIONS)
    assert [s.amounts[0].status for s in scaled if s.amounts] == [
        "scaled", "needs_review", "scaled",  # a total row ends the heading context
    ]


def test_a_new_heading_replaces_the_context():
    rows = [
        make_row("Weighted-average shares:", []),
        make_row("Diluted", ["477"]),
        make_row("Current assets:", []),
        make_row("Cash", ["10.0"]),
    ]
    scaled = scale_rows(rows, MILLIONS)
    assert [s.amounts[0].status for s in scaled if s.amounts] == ["needs_review", "scaled"]


def _real_rows(name, page_index):
    pdf = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / f"{name}.pdf"
    )
    if not pdf.exists():
        pytest.skip("FinanceBench sample PDF not downloaded")
    with pymupdf.open(pdf) as doc:
        scale = detect_scale(doc[page_index].get_text())
        rows = extract_page_rows(doc[page_index], page_index, "d", pdf.name)
    return scale, scale_rows(rows, scale)


def test_amcor_trade_receivables_match_the_financebench_answer():
    scale, scaled = _real_rows("AMCOR_2020_10K", 49)
    row = [s for s in scaled if s.row.label == "Trade receivables, net"][0]
    first = row.amounts[0]
    assert first.status == "scaled" and first.scaled_value == Decimal("1615900000")
    assert round(first.scaled_value / Decimal(1000000)) == 1616  # FinanceBench's gold answer


def test_amazon_per_share_and_share_count_rows_are_not_scaled():
    scale, scaled = _real_rows("AMAZON_2017_10K", 37)
    assert scale.status == "found" and scale.multiplier == Decimal(1000000)
    wanted = [s for s in scaled if s.row.label in ("Diluted earnings per share", "Diluted")]
    assert len(wanted) == 2
    for row in wanted:
        assert all(a.status == "needs_review" and a.scaled_value is None for a in row.amounts)
    assert any(a.status == "scaled" for s in scaled for a in s.amounts)  # other rows are
