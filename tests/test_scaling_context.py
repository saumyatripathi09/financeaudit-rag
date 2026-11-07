from decimal import Decimal
from types import SimpleNamespace as NS

from audit_rag.normalization.scaling import scale_rows, is_heading, looks_like_prose


def R(label, *v):
    return NS(label=label, cells=[NS(raw_text=x) for x in v])


S = NS(status="ok", multiplier=Decimal(1000), mentions_exceptions=False)


def st(rows):
    return [[a.status for a in r.amounts] for r in scale_rows(rows, S)]


def test_par_value_fragment_is_not_a_heading():
    assert not is_heading("Common stock, $0.0001 par value; 500,000 shares")
    assert not is_heading("Treasury stock, at cost (150,046,537 and")
    assert is_heading("Earnings per common share:")
    assert is_heading("Operating expenses:")


def test_par_fragment_does_not_leak_to_total():
    out = st([R("Common stock, $0.0001 par valu"),
              R("Accumulated other comprehensive income", "5"),
              R("Total liabilities and stockholders equity", "100")])
    assert out == [[], ["scaled"], ["scaled"]]


def test_per_share_heading_still_flags_rows_below():
    out = st([R("Earnings per common share:"), R("Basic", "1.20"), R("Diluted", "1.10")])
    assert out == [[], ["needs_review"], ["needs_review"]]


def test_total_closes_section():
    out = st([R("Earnings per share:"), R("Basic", "1.2"), R("Total", "9"), R("Revenue", "50")])
    assert out[2] == ["scaled"] and out[3] == ["scaled"]


def test_share_label_itself_still_flagged():
    assert st([R("Weighted-average shares", "10")]) == [["needs_review"]]


def test_prose_row_not_scaled():
    assert looks_like_prose("borrowings. The net proceeds of approximately")
    assert looks_like_prose("31, 2022 and 818,717,043 issued")
    assert not looks_like_prose("Long-term debt")
    assert st([R("borrowings. The net proceeds", "5")]) == [["needs_review"]]


def test_normal_rows_scaled():
    assert st([R("Revenue", "1,000"), R("Cost of sales", "400")]) == [["scaled"], ["scaled"]]


def test_maturity_bucket_label_is_not_prose():
    assert not looks_like_prose("5-10 Years")
    assert not looks_like_prose("1-5 Years")
    assert st([R("Interest"), R("5-10 Years", "300")]) == [[], ["scaled"]]
