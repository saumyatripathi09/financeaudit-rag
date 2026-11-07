"""Tests for scale detection."""
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from audit_rag.normalization.scale import detect_scale, find_scale_statements

MILLION = Decimal(1000000)
THOUSAND = Decimal(1000)


def test_in_millions():
    result = detect_scale("Consolidated Balance Sheet (in millions) As of June 30")
    assert result.status == "found" and result.multiplier == MILLION
    assert result.mentions_exceptions is False


def test_dollars_in_millions_except_per_share():
    result = detect_scale("(Dollars in millions, except per share amount) 2018 2017")
    assert result.status == "found" and result.multiplier == MILLION
    assert result.mentions_exceptions is True


def test_in_thousands_except_share_data():
    result = detect_scale("(In thousands, except share and per share data)")
    assert result.multiplier == THOUSAND and result.mentions_exceptions is True


def test_dollar_sign_before_the_phrase():
    assert detect_scale("($ in thousands)").multiplier == THOUSAND


def test_leading_scale_word():
    assert detect_scale("(Millions of dollars)").multiplier == MILLION
    assert detect_scale("(Thousands)").multiplier == THOUSAND


def test_amounts_in_millions_of_us_dollars():
    assert detect_scale("(amounts in millions of U.S. dollars)").multiplier == MILLION


def test_a_phrase_wrapped_across_lines_with_a_non_breaking_space():
    text = "(Dollars in\nmillions,\xa0except per share amount)"
    result = detect_scale(text)
    assert result.multiplier == MILLION and result.mentions_exceptions is True


def test_indian_units():
    assert detect_scale("(\u20b9 in crore)").multiplier == Decimal(10000000)
    assert detect_scale("(Rs. in lakhs)").multiplier == Decimal(100000)


def test_a_sentence_about_millions_is_not_a_scale_statement():
    text = "Revenue increased by $5 million in 2020 (an increase of $5 million) versus 2019."
    result = detect_scale(text)
    assert result.status == "unknown" and result.multiplier is None


def test_two_different_scales_are_a_conflict_with_no_multiplier():
    result = detect_scale("(in millions) ... Shares (in thousands)")
    assert result.status == "conflict" and result.multiplier is None
    assert {s.multiplier for s in result.statements} == {MILLION, THOUSAND}


def test_the_same_scale_stated_twice_is_still_found():
    result = detect_scale("(in millions) 2020 ... (Dollars in millions)")
    assert result.status == "found" and result.multiplier == MILLION
    assert len(result.statements) == 2


def test_no_statement_is_unknown_and_never_defaults_to_one():
    result = detect_scale("Cash and cash equivalents 742.6 601.6")
    assert result.status == "unknown" and result.multiplier is None


def test_matching_ignores_case():
    assert detect_scale("(IN MILLIONS)").multiplier == MILLION


def test_the_printed_phrase_is_kept():
    statements = find_scale_statements("Balance Sheet (in millions)")
    assert [s.snippet for s in statements] == ["(in millions)"]
    assert statements[0].word == "million"


def test_amcor_balance_sheet_is_in_millions():
    pdf = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / "AMCOR_2020_10K.pdf"
    )
    if not pdf.exists():
        pytest.skip("FinanceBench sample PDF not downloaded")
    with pymupdf.open(pdf) as doc:
        result = detect_scale(doc[49].get_text())
    assert result.status == "found" and result.multiplier == MILLION


def test_a_standalone_line_with_a_dollar_sign_and_no_brackets():
    text = "Consolidated Balance Sheets\n$ in millions, except per share and share amounts\nFebruary 2, 2019"
    result = detect_scale(text)
    assert result.status == "found" and result.multiplier == MILLION
    assert result.mentions_exceptions is True


def test_a_standalone_in_millions_line():
    result = detect_scale("Consolidated Statements of Operations\nIn millions, except per share amounts\n2018")
    assert result.multiplier == MILLION and result.mentions_exceptions is True


def test_standalone_dollars_and_amounts_lines():
    assert detect_scale("Dollars in thousands").multiplier == THOUSAND
    assert detect_scale("Amounts in millions").multiplier == MILLION
    assert detect_scale("In millions").multiplier == MILLION


def test_an_unbracketed_phrase_inside_a_sentence_is_not_a_scale_statement():
    assert detect_scale("Sales were reported in millions of dollars for each segment.").status == "unknown"
    assert detect_scale("Net sales in millions grew in 2020").status == "unknown"


def test_a_line_about_billions_of_shares_is_not_a_scale_statement():
    text = "Common stock, $0.10 par value: Authorized \u2014 1.0 billion shares; Issued and outstanding"
    assert detect_scale(text).status == "unknown"


def test_a_bracketed_and_a_standalone_statement_can_conflict():
    result = detect_scale("(in thousands)\nIn millions, except per share amounts")
    assert result.status == "conflict" and result.multiplier is None


def test_the_same_scale_in_both_forms_is_found():
    result = detect_scale("(in millions)\nIn millions, except per share amounts")
    assert result.status == "found" and result.multiplier == MILLION
    assert len(result.statements) == 2


def _page_text(name, page_index):
    pdf = (
        Path(__file__).resolve().parents[1]
        / "samples" / "financebench" / "pdfs" / f"{name}.pdf"
    )
    if not pdf.exists():
        pytest.skip("FinanceBench sample PDF not downloaded")
    with pymupdf.open(pdf) as doc:
        return doc[page_index].get_text()


def test_best_buy_balance_sheet_is_in_millions():
    result = detect_scale(_page_text("BESTBUY_2019_10K", 51))
    assert result.status == "found" and result.multiplier == MILLION
    assert result.mentions_exceptions is True


def test_cvs_statement_of_operations_is_in_millions():
    result = detect_scale(_page_text("CVSHEALTH_2018_10K", 301))
    assert result.status == "found" and result.multiplier == MILLION
