from audit_rag.normalization.scale import detect_scale


def test_dollars_and_shares_line_is_a_scale_statement():
    result = detect_scale("Consolidated Statements of Earnings\n$ and shares in millions, except per share amounts\nRevenue")
    assert result.status == "found" and int(result.multiplier) == 1000000
    assert result.mentions_exceptions


def test_prose_with_the_same_words_is_not_a_scale_statement():
    assert detect_scale("Revenue and shares in millions were higher this year").status == "unknown"
    assert detect_scale("and shares in millions grew.").status == "unknown"
