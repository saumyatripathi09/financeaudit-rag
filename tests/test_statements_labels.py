from audit_rag.normalization.statements import detect_statement
from audit_rag.normalization.labels import canonical_label, normalize_label


def test_title_wins():
    g = detect_statement("CONSOLIDATED STATEMENTS OF CASH FLOWS (in millions)", ["Net income"])
    assert (g.statement, g.basis) == ("cash_flow", "title")
    assert detect_statement("Consolidated Balance Sheets", []).statement == "balance_sheet"
    assert detect_statement("Consolidated Statements of Income", []).statement == "income_statement"
    assert detect_statement("Consolidated Statements of Operations", []).statement == "income_statement"
    assert detect_statement("Consolidated Statements of Comprehensive Income", []).statement == "comprehensive_income"
    assert detect_statement("Statements of Shareholders\u2019 Equity", []).statement == "equity"


def test_labels_fallback_and_unknown():
    bs = ["Total current assets", "Total assets", "Total current liabilities", "Total liabilities"]
    g = detect_statement("", bs)
    assert (g.statement, g.basis) == ("balance_sheet", "labels")
    assert detect_statement("", ["Net income", "Revenue"]).statement == "unknown"  # thin evidence
    assert detect_statement("", []).statement == "unknown"


def test_labels_normalise_and_match():
    assert canonical_label("Total stockholders\u2019 equity").canonical == "total_parent_equity"
    assert canonical_label("Total Liabilities and Stockholders' Equity").canonical == "total_liabilities_and_equity"
    assert canonical_label("Total liabilities").canonical == "total_liabilities"
    assert canonical_label("Net sales").canonical == "revenue"
    assert canonical_label("Net income (loss)").canonical == "net_income"
    assert canonical_label("Total assets (Note 3)").canonical == "total_assets"


def test_unrecognised_stays_none():
    assert canonical_label("Other long-term assets").canonical is None
    assert canonical_label("Net income attributable to noncontrolling interests").canonical is None
    assert normalize_label("A & B.") == "a and b"


def test_equity_variants_are_kept_apart():
    assert canonical_label("Total equity").canonical == "total_equity"
    assert canonical_label("Noncontrolling interests").canonical == "noncontrolling_interest"
    assert canonical_label("Redeemable noncontrolling interest").canonical == "redeemable_interest"


def test_more_revenue_and_cost_wordings_map_to_the_same_names():
    for text in ("Net revenue", "Net operating revenues", "Total net revenue"):
        assert canonical_label(text).canonical == "revenue"
    for text in ("Total cost of revenue", "Total cost of sales"):
        assert canonical_label(text).canonical == "cost_of_revenue"
    assert canonical_label("Sales to customers").canonical is None  # company-specific, not guessed


def test_a_sentence_that_mentions_a_statement_is_not_its_title():
    # real page openings from an Amcor 10-K (none of these pages is a balance sheet)
    sentence = ("The following tables provide the location of derivative instruments in the "
                "consolidated balance sheet:\n(in millions)\nBalance Sheet Location\n2020\n2019")
    assert detect_statement(sentence, []).statement == "unknown"
    audit = ("Item 8. - Financial Statements and Supplementary Data\nReport of Independent "
             "Registered Public Accounting Firm\nOpinions on the Financial Statements\n"
             "We have audited the accompanying consolidated balance sheets of Amcor plc as of")
    assert detect_statement(audit, []).statement == "unknown"


def test_real_headings_still_count_on_multiline_pages():
    page = "Amcor plc\nConsolidated Balance Sheets\n(in millions, except per share data)\nJune 30,"
    assert detect_statement(page, []).statement == "balance_sheet"
    wrapped = "Consolidated Statements of\nCash Flows\n(in millions)"
    assert detect_statement(wrapped, []).statement == "cash_flow"
    both = "Consolidated Statements of Income\nConsolidated Statements of Comprehensive Income"
    assert detect_statement(both, []).statement == "income_statement"


def test_table_column_named_like_a_statement_is_not_a_title():
    assert detect_statement("Balance Sheet Location\n2020\n2019", []).statement == "unknown"
    assert detect_statement("Consolidated Balance Sheets as of June 30, 2020", []).statement == "balance_sheet"
    assert detect_statement("Condensed Consolidated Statements of Income (Unaudited)", []).statement == "income_statement"
    assert detect_statement("Consolidated Statements of Income and Comprehensive Income", []).statement == "income_statement"


def test_deficit_wordings_map_to_the_equity_names():
    assert canonical_label("Total stockholders' deficit").canonical == "total_parent_equity"
    assert canonical_label("Total shareholders' equity (deficit)").canonical == "total_parent_equity"
    assert canonical_label("Total liabilities and stockholders' deficit").canonical == "total_liabilities_and_equity"
    assert canonical_label("Total liabilities and shareholders' equity (deficit)").canonical == "total_liabilities_and_equity"
