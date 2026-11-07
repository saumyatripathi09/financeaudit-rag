"""Map statement row labels to canonical names. Unmatched labels stay unmatched."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Exact normalised labels (lowercase, letters and digits only). Whole-label match on purpose:
# "total liabilities and equity" must never fall into "total liabilities".
EXACT = {
    "total assets": "total_assets",
    "total current assets": "total_current_assets",
    "total liabilities": "total_liabilities",
    "total current liabilities": "total_current_liabilities",
    "total equity": "total_equity",
    "total stockholders equity": "total_parent_equity",
    "total shareholders equity": "total_parent_equity",
    "total shareowners equity": "total_parent_equity",
    "noncontrolling interest": "noncontrolling_interest",
    "noncontrolling interests": "noncontrolling_interest",
    "non controlling interest": "noncontrolling_interest",
    "non controlling interests": "noncontrolling_interest",
    "redeemable interest": "redeemable_interest",
    "redeemable noncontrolling interest": "redeemable_interest",
    "redeemable noncontrolling interests": "redeemable_interest",
    "temporary equity": "redeemable_interest",
    "total liabilities and stockholders equity": "total_liabilities_and_equity",
    "total liabilities and shareholders equity": "total_liabilities_and_equity",
    "total liabilities and equity": "total_liabilities_and_equity",
    "total liabilities and shareowners equity": "total_liabilities_and_equity",
    "total liabilities and stockholders deficit": "total_liabilities_and_equity",
    "total liabilities and shareholders deficit": "total_liabilities_and_equity",
    "total liabilities and stockholders equity deficit": "total_liabilities_and_equity",
    "total liabilities and shareholders equity deficit": "total_liabilities_and_equity",
    "total stockholders deficit": "total_parent_equity",
    "total shareholders deficit": "total_parent_equity",
    "total stockholders equity deficit": "total_parent_equity",
    "total shareholders equity deficit": "total_parent_equity",
    "cash and cash equivalents": "cash_and_equivalents",
    "revenue": "revenue",
    "revenues": "revenue",
    "total revenue": "revenue",
    "total revenues": "revenue",
    "net sales": "revenue",
    "net revenues": "revenue",
    "total net sales": "revenue",
    "net revenue": "revenue",
    "net operating revenues": "revenue",
    "total net revenue": "revenue",
    "total net revenues": "revenue",
    "cost of sales": "cost_of_revenue",
    "cost of revenue": "cost_of_revenue",
    "cost of revenues": "cost_of_revenue",
    "cost of goods sold": "cost_of_revenue",
    "total cost of revenue": "cost_of_revenue",
    "total cost of revenues": "cost_of_revenue",
    "total cost of sales": "cost_of_revenue",
    "gross profit": "gross_profit",
    "gross margin": "gross_profit",
    "operating income": "operating_income",
    "income from operations": "operating_income",
    "net income": "net_income",
    "net earnings": "net_income",
    "net income loss": "net_income",
    "net cash provided by operating activities": "net_cash_operating",
    "net cash provided by used in operating activities": "net_cash_operating",
    "net cash from operating activities": "net_cash_operating",
    "net cash used in investing activities": "net_cash_investing",
    "net cash provided by used in investing activities": "net_cash_investing",
    "net cash used in financing activities": "net_cash_financing",
    "net cash provided by used in financing activities": "net_cash_financing",
}
_NOTE_REF = re.compile(r"\(?\bnotes?\s*[\d.,a-z ]*\)?\s*$", re.I)


@dataclass(frozen=True)
class CanonicalLabel:
    original: str
    normalized: str
    canonical: str | None  # None when the label is not recognised


def normalize_label(label: str) -> str:
    text = _NOTE_REF.sub("", label.strip()) if re.search(r"\bnotes?\s*\d", label, re.I) else label
    text = text.replace("&", " and ").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)  # apostrophes, dots, dashes and so on
    return " ".join(text.split())


def canonical_label(label: str) -> CanonicalLabel:
    norm = normalize_label(label)
    return CanonicalLabel(label, norm, EXACT.get(norm))
