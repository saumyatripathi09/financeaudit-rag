"""Cross-document reconciliation: does the same figure agree between filings?

The same company reports a prior-year figure again in the next filing. This compares those
figures. A difference is a reason to look, not proof of an error: restated figures, a
different period basis (quarter or year to date) and reclassifications are common and legal.

Matching is deliberately strict. Two figures are compared only if they have the same
entity, period basis, statement type, canonical label and exactly the same column heading,
they come from different documents, and both were scaled safely. The period basis matters:
in a quarterly release the column headed "2022" is a quarter, and in an annual report it is
a full year, so the two must never be compared. `basis_of(document_id)` names that basis
(for example "annual" or "quarterly"); without it every document counts as the same basis.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Callable

from audit_rag.audit.checks import Check, _ref
from audit_rag.normalization.records import NormalizedRecord

RULE_ID = "cross_document"


def _key(r: NormalizedRecord, entity: str, basis: str):
    return (entity, basis, r.statement_type, r.label_canonical,
            " ".join((r.column_label or "").split()))


def _eligible(r: NormalizedRecord) -> bool:
    return (
        r.kind == "number" and r.scale_status == "scaled" and r.scaled_value is not None
        and r.label_canonical is not None and r.statement_type != "unknown"
        and bool(r.column_label) and r.multiplier is not None
    )


def _tolerance(r: NormalizedRecord) -> Decimal:
    """Half a unit of the last displayed digit, in scaled units."""
    unit = Decimal(1).scaleb(r.displayed_value.as_tuple().exponent)
    return unit * r.multiplier * Decimal("0.5")


def reconcile(
    records: list[NormalizedRecord],
    entity_of: Callable[[str], str],
    basis_of: Callable[[str], str] | None = None,
) -> list[Check]:
    """`entity_of(document_id)` names the company a document belongs to."""
    firsts: dict[tuple, dict[str, NormalizedRecord]] = defaultdict(dict)
    for r in sorted(records, key=lambda x: (x.document_id, x.page_index or 0, x.row_order, x.cell_position)):
        if _eligible(r):
            firsts[_key(r, entity_of(r.document_id), basis_of(r.document_id) if basis_of else "")
               ].setdefault(r.document_id, r)

    checks: list[Check] = []
    for key in sorted(firsts, key=str):
        by_doc = firsts[key]
        docs = sorted(by_doc)
        for i, first_doc in enumerate(docs):
            for second_doc in docs[i + 1:]:
                a, b = by_doc[first_doc], by_doc[second_doc]
                diff = b.scaled_value - a.scaled_value
                allowance = max(_tolerance(a), _tolerance(b))
                if diff == 0:
                    status, severity = "pass", "none"
                elif abs(diff) <= allowance:
                    status, severity = "rounding", "none"
                else:
                    status, severity = "fail", "medium"
                entity, _basis, statement, canonical, column = key
                checks.append(Check(
                    RULE_ID, first_doc, a.page_index, column, status, severity,
                    f"{entity}: {canonical} for '{column}' in {first_doc} and {second_doc} "
                    "should agree. A difference may be a restatement or a different period "
                    "basis, so review it.",
                    expected=a.scaled_value, actual=b.scaled_value, difference=diff,
                    evidence=(_ref(a), _ref(b)),
                ))
    return checks
