from audit_rag.audit.structural import FAULTS, inject_structure
from audit_rag.audit.injection import tally
from test_injection import balance_page, rec


def two_column_page():
    return [
        rec(0, 0, "Total assets", "total_assets", "1000"),
        rec(1, 0, "Total liabilities", "total_liabilities", "600"),
        rec(2, 0, "Total equity", "total_equity", "400"),
        rec(3, 0, "Total liabilities and equity", "total_liabilities_and_equity", "1000"),
        rec(0, 1, "Total assets", "total_assets", "900"),
        rec(1, 1, "Total liabilities", "total_liabilities", "500"),
        rec(2, 1, "Total equity", "total_equity", "400"),
        rec(3, 1, "Total liabilities and equity", "total_liabilities_and_equity", "900"),
    ]


def test_dropping_a_total_row_is_reported_only_when_the_balance_equation_is_lost():
    result = inject_structure(balance_page())
    drops = {i.label: i for i in result.injections if i.fault == "drop_row"}
    assert set(drops) == {"Total assets", "Total liabilities", "Total equity",
                          "Total liabilities and equity"}  # the Cash row is in no check
    assert drops["Total assets"].outcome == "lost_but_reported"
    assert drops["Total liabilities and equity"].outcome == "lost_but_reported"
    # These two remove liabilities_plus_equity, which now says so with a not_testable flag.
    assert drops["Total liabilities"].outcome == "lost_but_reported"
    assert drops["Total equity"].outcome == "lost_but_reported"
    assert "liabilities_plus_equity" in drops["Total equity"].rules


def test_shifting_a_row_into_the_wrong_column_is_flagged_when_columns_exist():
    result = inject_structure(two_column_page())
    shifted = [i for i in result.injections if i.fault == "shift_right" and i.label == "Total assets"]
    assert len(shifted) == 1 and shifted[0].outcome == "flagged"
    assert "balance_equation" in shifted[0].rules


def test_single_column_shift_loses_the_check_and_says_so():
    result = inject_structure(balance_page())
    shifted = [i for i in result.injections if i.fault == "shift_right" and i.label == "Total assets"]
    assert shifted[0].outcome == "lost_but_reported"


def test_original_page_is_not_changed_and_counts_are_right():
    records = balance_page()
    before = list(records)
    result = inject_structure(records)
    assert records == before
    assert result.rows_total == 5 and result.rows_covered == 4


def test_page_without_checks_has_nothing_to_inject():
    result = inject_structure([rec(0, 0, "Cash", "cash", "5")])
    assert result.injections == [] and result.rows_covered == 0


def test_tally_works_on_structural_results():
    result = inject_structure(two_column_page())
    table = tally(result.injections, lambda x: x.fault)
    assert set(table) == set(FAULTS)
    assert sum(sum(c.values()) for c in table.values()) == len(result.injections)
