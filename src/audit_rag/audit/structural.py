"""Structural faults: what the rules do when extraction loses or shifts a whole row.

injection.py changes one value. Real extraction faults are different: a row can be dropped,
or its cells can land one column to the right. For each row that holds a value the rules
verify, one fault at a time is applied to a copy of the page and the rules are re-run:

  flagged            a rule that was not failing before now fails
  lost_but_reported  a check disappeared but a not_testable flag now says so
  check_lost         a check that passed before no longer exists and nothing was reported:
                     the page silently stops being verified
  no_visible_effect  the same checks still pass
  rules_error        the rules raised an exception (itself a finding)
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from audit_rag.audit.checks import run_checks
from audit_rag.audit.injection import _fail_keys, role_of
from audit_rag.normalization.records import NormalizedRecord

FAULTS = ("drop_row", "shift_right")
OUTCOMES = ("flagged", "lost_but_reported", "check_lost", "no_visible_effect", "rules_error")


@dataclass(frozen=True)
class StructuralInjection:
    document_id: str
    page_index: int | None
    row_order: int
    label: str
    role: str
    statement_type: str
    fault: str
    outcome: str
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageStructural:
    injections: list[StructuralInjection]
    rows_total: int
    rows_covered: int


def _check_key(check) -> tuple:
    first = check.evidence[0] if check.evidence else None
    where = (first.row_order, first.cell_position) if first else None
    return (check.rule_id, check.column, where)


def _shift_right(records: list[NormalizedRecord], row: int) -> list[NormalizedRecord]:
    """Move every cell of one row one column to the right, taking that column's label."""
    labels: dict[int, str] = {}
    for r in records:
        if r.column_label:
            labels.setdefault(r.cell_position, r.column_label)
    out = []
    for r in records:
        if r.row_order == row:
            new_position = r.cell_position + 1
            out.append(replace(r, cell_position=new_position, column_label=labels.get(new_position)))
        else:
            out.append(r)
    return out


def inject_structure(records: list[NormalizedRecord]) -> PageStructural:
    """Apply each structural fault to each covered row of ONE page. Originals are untouched."""
    baseline = run_checks(records)
    base_fails = _fail_keys(baseline)
    passing = [c for c in baseline if c.status in ("pass", "rounding")]
    base_ok = {_check_key(c) for c in passing}
    covered_rows = {e.row_order for c in passing for e in c.evidence}
    first_of_row: dict[int, NormalizedRecord] = {}
    for r in records:
        first_of_row.setdefault(r.row_order, r)
    out: list[StructuralInjection] = []
    for row in sorted(covered_rows):
        head = first_of_row[row]
        info = dict(document_id=head.document_id, page_index=head.page_index, row_order=row,
                    label=head.label, role=role_of(head), statement_type=head.statement_type)
        for fault in FAULTS:
            changed = ([r for r in records if r.row_order != row] if fault == "drop_row"
                       else _shift_right(records, row))
            try:
                after = run_checks(changed)
            except Exception as exc:
                out.append(StructuralInjection(**info, fault=fault, outcome="rules_error",
                                               rules=(type(exc).__name__,)))
                continue
            new_fails = _fail_keys(after) - base_fails
            # a check is retained only if a real test of it still exists; a not_testable
            # flag with the same key means the check was lost, and `reported` below says so
            lost = base_ok - {_check_key(c) for c in after if c.status != "not_testable"}
            reported = ({_check_key(c) for c in after if c.status == "not_testable"}
                        - {_check_key(c) for c in baseline if c.status == "not_testable"})
            if new_fails:
                out.append(StructuralInjection(
                    **info, fault=fault, outcome="flagged",
                    rules=tuple(sorted({k[0] for k in new_fails}))))
            elif lost and reported:
                out.append(StructuralInjection(
                    **info, fault=fault, outcome="lost_but_reported",
                    rules=tuple(sorted({k[0] for k in lost}))))
            elif lost:
                out.append(StructuralInjection(
                    **info, fault=fault, outcome="check_lost",
                    rules=tuple(sorted({k[0] for k in lost}))))
            else:
                out.append(StructuralInjection(**info, fault=fault, outcome="no_visible_effect"))
    return PageStructural(out, len({r.row_order for r in records}), len(covered_rows))
