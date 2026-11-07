"""Tests for PDF row extraction, using the FinanceBench Amcor 10-K (page index 49)."""
from pathlib import Path

import pymupdf
import pytest

from audit_rag.ingestion.pdf_text import extract_page_rows

PDF = (
    Path(__file__).resolve().parents[1]
    / "samples" / "financebench" / "pdfs" / "AMCOR_2020_10K.pdf"
)
pytestmark = pytest.mark.skipif(not PDF.exists(), reason="FinanceBench sample PDF not downloaded")

DASH = "\u2014"


@pytest.fixture(scope="module")
def rows():
    with pymupdf.open(PDF) as doc:
        return extract_page_rows(doc[49], 49, "doc-test", PDF.name)


def values_of(rows, label):
    matches = [r for r in rows if r.label == label]
    assert len(matches) == 1, f"expected exactly one row labelled {label!r}, got {len(matches)}"
    return {cell.column_label: cell.raw_text for cell in matches[0].cells}


def test_trade_receivables_match_the_answer_key(rows):
    assert values_of(rows, "Trade receivables, net") == {"2020": "1,615.9", "2019": "1,864.3"}


def test_dashes_are_kept_as_displayed(rows):
    assert values_of(rows, "Assets held for sale") == {"2020": DASH, "2019": "416.1"}
    assert values_of(rows, "Operating lease assets") == {"2020": "525.3", "2019": DASH}


def test_every_value_is_assigned_to_a_known_column(rows):
    assert len(rows) > 10
    for row in rows:
        for cell in row.cells:
            assert cell.column_label in {"2020", "2019"}


def test_rows_remember_their_page(rows):
    assert all(r.location.page_index == 49 for r in rows)


def test_current_assets_add_up_to_the_printed_total(rows):
    """First taste of an audit rule: do the parts sum to the total on the page?

    Test-only assumption: a dash counts as zero. The real rule engine must
    record that assumption instead of making it silently.
    """
    from decimal import Decimal

    def amount(raw):
        return Decimal("0") if raw == DASH else Decimal(raw.replace(",", ""))

    parts = [
        "Cash and cash equivalents",
        "Trade receivables, net",
        "Inventories, net",
        "Prepaid expenses and other current assets",
        "Assets held for sale",
    ]
    for year in ("2020", "2019"):
        total = sum(amount(values_of(rows, label)[year]) for label in parts)
        printed = amount(values_of(rows, "Total current assets")[year])
        assert total == printed, f"{year}: parts sum to {total}, page prints {printed}"


def _row(label, column_labels):
    from audit_rag.models.extraction import (
        ExtractedCell, ExtractedRow, ExtractionMethod, SourceLocation,
    )

    loc = SourceLocation(page_index=0)
    cells = [ExtractedCell(raw_text="1", location=loc, column_label=c) for c in column_labels]
    return ExtractedRow(
        document_id="d", filename="f.pdf", label=label, cells=cells,
        location=loc, method=ExtractionMethod.PDF_TEXT,
    )


def test_amount_pattern_accepts_a_glued_dollar_sign():
    from audit_rag.ingestion.pdf_text import AMOUNT_RE

    for token in ("$117,359", "($1,234)", "(12.3", "1,615.9"):
        assert AMOUNT_RE.match(token), token
    for token in ("$", "$385,", "and", "2020x"):
        assert not AMOUNT_RE.match(token), token


def test_clean_row_has_no_warnings():
    from audit_rag.ingestion.pdf_text import _with_warnings

    assert _with_warnings(_row("Inventories, net", ["2020", "2019"]), 2).warnings == []


def test_more_values_than_columns_is_flagged():
    from audit_rag.ingestion.pdf_text import _with_warnings

    result = _with_warnings(_row("Best Buy", list("abcdefgh")), 4)
    assert any("only 4 columns" in w for w in result.warnings)


def test_two_values_in_one_column_is_flagged():
    from audit_rag.ingestion.pdf_text import _with_warnings

    result = _with_warnings(_row("Total", ["2020", "2020"]), 2)
    assert any("same column" in w for w in result.warnings)


def test_label_ending_in_several_numbers_is_flagged():
    from audit_rag.ingestion.pdf_text import _with_warnings

    result = _with_warnings(_row("Tax expense 300 300 193 193", ["2020"]), 2)
    assert any("number-like" in w for w in result.warnings)


def test_one_number_inside_a_label_is_not_flagged():
    from audit_rag.ingestion.pdf_text import _with_warnings

    row = _row("Accounts receivable, net of allowances of $95 and $103", ["2018", "2017"])
    assert _with_warnings(row, 2).warnings == []


def test_known_good_amcor_rows_have_no_warnings(rows):
    for label in ("Trade receivables, net", "Total current assets"):
        match = [r for r in rows if r.label == label]
        assert match and match[0].warnings == []


def test_boeing_total_assets_with_glued_dollar_signs():
    boeing = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / "BOEING_2018_10K.pdf"
    )
    if not boeing.exists():
        pytest.skip("Boeing sample PDF not downloaded")
    with pymupdf.open(boeing) as doc:
        boeing_rows = extract_page_rows(doc[51], 51, "doc-test", boeing.name)
    assert values_of(boeing_rows, "Total assets") == {"2018": "$117,359", "2017": "$112,362"}


def _w(text, x0, x1):
    from audit_rag.ingestion.pdf_text import Word

    return Word(x0, 10.0, x1, 18.0, text)


def test_distinct_years_keep_plain_year_labels():
    from audit_rag.ingestion.pdf_text import find_header

    row = [_w("As", 15, 25), _w("of", 27, 35), _w("2020", 474, 494), _w("2019", 554, 574)]
    index, columns = find_header([row])
    assert [c.text for c in columns] == ["2020", "2019"]
    assert not any(c.caption for c in columns)


def test_repeated_year_uses_the_full_date_phrase():
    from audit_rag.ingestion.pdf_text import find_header

    row = [
        _w("December", 399, 438), _w("31,", 440, 451), _w("2022", 453, 472),
        _w("June", 518, 537), _w("30,", 539, 550), _w("2022", 552, 570),
    ]
    _, columns = find_header([row])
    assert [c.text for c in columns] == ["December 31, 2022", "June 30, 2022"]


def test_caption_header_is_marked_and_not_guessed_by_position():
    from audit_rag.ingestion.pdf_text import _column_label, find_header

    row = [
        _w("December", 235, 284), _w("31,", 287, 302), _w("2022", 305, 330),
        _w("and", 333, 351), _w("2021", 354, 378),
    ]
    _, columns = find_header([row])
    assert [c.text for c in columns] == ["2022", "2021"]
    assert all(c.caption for c in columns)
    value = _w("38,363", 495, 530)
    assert _column_label(0, value, 2, columns) == "2022"
    assert _column_label(1, value, 2, columns) == "2021"
    assert _column_label(0, value, 1, columns) is None  # one value, two years: unknown


def test_caption_rows_carry_a_warning():
    from audit_rag.ingestion.pdf_text import _with_warnings

    both = _with_warnings(_row("Total assets", ["2022", "2021"]), 2, True)
    assert any("caption" in w for w in both.warnings)
    short = _with_warnings(_row("Total assets", [None]), 2, True)
    assert any("unknown" in w for w in short.warnings)


def test_aes_caption_page_gets_labels_by_order_and_a_warning():
    aes = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / "AES_2022_10K.pdf"
    )
    if not aes.exists():
        pytest.skip("AES sample PDF not downloaded")
    with pymupdf.open(aes) as doc:
        aes_rows = extract_page_rows(doc[129], 129, "doc-test", aes.name)
    assert values_of(aes_rows, "TOTAL ASSETS") == {"2022": "38,363", "2021": "32,963"}
    match = [r for r in aes_rows if r.label == "TOTAL ASSETS"][0]
    assert match.warnings, "caption-labelled rows must carry a warning"


def _wy(text, x0, x1, y):
    from audit_rag.ingestion.pdf_text import Word

    return Word(x0, y - 4.0, x1, y + 4.0, text)


def test_numbers_printed_slightly_off_their_label_are_rejoined():
    from audit_rag.ingestion.pdf_text import group_into_rows, merge_orphan_rows

    words = [
        _wy("TOTAL", 52, 78, 301.6), _wy("ASSETS", 80, 112, 301.6),
        _wy("$", 374, 379, 299.3), _wy("59,268", 449, 477, 299.3),
        _wy("$", 488, 493, 299.3), _wy("55,556", 563, 590, 299.3),
    ]
    rows = group_into_rows(words)
    assert len(rows) == 2  # the 2.3 point offset splits them
    merged = merge_orphan_rows(rows)
    assert len(merged) == 1
    assert [w.text for w in merged[0]][:2] == ["TOTAL", "ASSETS"]


def test_distant_rows_are_not_merged():
    from audit_rag.ingestion.pdf_text import group_into_rows, merge_orphan_rows

    words = [_wy("TOTAL", 52, 78, 301.6), _wy("59,268", 449, 477, 312.0)]
    assert len(merge_orphan_rows(group_into_rows(words))) == 2


def test_a_row_with_label_and_numbers_is_never_merged():
    from audit_rag.ingestion.pdf_text import group_into_rows, merge_orphan_rows

    words = [_wy("Cash", 35, 54, 90.0), _wy("742.6", 501, 520, 90.0), _wy("1,000", 497, 520, 92.5)]
    rows = group_into_rows(words)
    assert len(rows) == 2
    assert len(merge_orphan_rows(rows)) == 2


def test_values_without_a_label_get_a_warning():
    from audit_rag.ingestion.pdf_text import _with_warnings

    result = _with_warnings(_row("", ["2021", "2020"]), 2)
    assert any("without a row label" in w for w in result.warnings)


def test_costco_total_assets_keeps_its_numbers():
    costco = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / "COSTCO_2021_10K.pdf"
    )
    if not costco.exists():
        pytest.skip("Costco sample PDF not downloaded")
    with pymupdf.open(costco) as doc:
        costco_rows = extract_page_rows(doc[37], 37, "doc-test", costco.name)
    matches = [r for r in costco_rows if r.label == "TOTAL ASSETS"]
    assert len(matches) == 1
    assert [c.raw_text for c in matches[0].cells] == ["59,268", "55,556"]


def test_rows_with_fewer_values_than_columns_are_flagged_when_expected():
    from audit_rag.ingestion.pdf_text import _with_warnings

    flagged = _with_warnings(
        _row("Basic earnings per share", ["2017"]), 3, expect_all_values=True
    )
    assert any("1 of 3" in w and "may be missing" in w for w in flagged.warnings)


def test_complete_rows_and_section_headings_are_not_flagged_as_incomplete():
    from audit_rag.ingestion.pdf_text import _with_warnings

    full = _row("Cash", ["2017", "2016", "2015"])
    heading = _row("Current assets", [])
    assert _with_warnings(full, 3, expect_all_values=True).warnings == []
    assert _with_warnings(heading, 3, expect_all_values=True).warnings == []


def test_embedded_text_rows_are_not_checked_for_completeness():
    from audit_rag.ingestion.pdf_text import _with_warnings

    assert _with_warnings(_row("Cash", ["2017"]), 3).warnings == []
