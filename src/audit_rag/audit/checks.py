"""Audit checks run on normalised records. Every check keeps its evidence.

Statuses:
  pass          the figures agree exactly
  rounding      they differ by no more than rounding in the displayed digits
  fail          they differ by more than rounding
  flag          a data-quality problem a person should look at (not an arithmetic result)
  not_testable  a needed figure is missing, so nothing is claimed either way
Only the rules that can be decided from the records are here; nothing is inferred.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from audit_rag.normalization.records import NormalizedRecord

LOW_OCR_CONFIDENCE = Decimal("0.80")


@dataclass(frozen=True)
class EvidenceRef:
    row_order: int
    cell_position: int
    label: str
    raw_text: str
    page_index: int | None
    document_id: str = ""


@dataclass(frozen=True)
class Check:
    rule_id: str
    document_id: str
    page_index: int | None
    column: str  # column label if known, else "col<position>", else ""
    status: str
    severity: str  # high, medium, low or none
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None
    evidence: tuple[EvidenceRef, ...] = ()


def _ref(r: NormalizedRecord) -> EvidenceRef:
    return EvidenceRef(r.row_order, r.cell_position, r.label, r.raw_text, r.page_index, r.document_id)


def _unit(values: list[Decimal]) -> Decimal:
    """One unit of the coarsest displayed digit among the values (1615.9 -> 0.1)."""
    exponent = max(v.as_tuple().exponent for v in values)
    return Decimal(1).scaleb(exponent)


def _first_numbers(records: list[NormalizedRecord]) -> dict[tuple[str, int], NormalizedRecord]:
    """First plain number per (canonical label, column position) on a page."""
    found: dict[tuple[str, int], NormalizedRecord] = {}
    for r in sorted(records, key=lambda x: (x.row_order, x.cell_position)):
        if r.label_canonical and r.kind == "number" and r.displayed_value is not None:
            found.setdefault((r.label_canonical, r.cell_position), r)
    return found


def _column(r: NormalizedRecord) -> str:
    return r.column_label or f"col{r.cell_position}"


def _compare(rule, severity_on_fail, message, parts, expected_value, actual_value, tol_ops):
    """Build a check from the evidence records, the two numbers and the rounding allowance."""
    values = [p.displayed_value for p in parts]
    diff = actual_value - expected_value
    allowance = (_unit(values) * Decimal("0.5") * tol_ops) if tol_ops else Decimal(0)
    if diff == 0:
        status, severity = "pass", "none"
    elif abs(diff) <= allowance:
        status, severity = "rounding", "none"
    else:
        status, severity = "fail", severity_on_fail
    first = parts[0]
    return Check(
        rule, first.document_id, first.page_index, _column(first), status, severity,
        message, expected_value, actual_value, diff, tuple(_ref(p) for p in parts),
    )


_GP_MESSAGE = "Revenue minus cost of revenue should equal gross profit."
_RANK = {"pass": 0, "rounding": 1, "fail": 2}


def _gross_profit_check(rev, cogs, gp, between) -> Check:
    # cost is often printed in parentheses, so it parses negative; use its size
    base = _compare("gross_profit", "medium", _GP_MESSAGE, [rev, cogs, gp],
                    gp.displayed_value, rev.displayed_value - abs(cogs.displayed_value), 2)
    if base.status in ("pass", "rounding") or not between:
        return base
    # Some statements show more cost lines (for example restructuring charges) between
    # cost of revenue and gross profit. Count them only if that makes the figures agree.
    actual = rev.displayed_value - abs(cogs.displayed_value) - sum(abs(b.displayed_value) for b in between)
    full = _compare(
        "gross_profit", "medium",
        _GP_MESSAGE + " Other cost lines shown between cost of revenue and gross profit were included.",
        [rev, cogs, *between, gp], gp.displayed_value, actual, 2 + len(between))
    return full if full.status in ("pass", "rounding") else base


def _equity_check(tl, tle, nums, pos):
    """Total liabilities plus equity against total liabilities and equity.

    The label "Total shareholders' equity" means parent-only equity at some companies and
    equity including noncontrolling interests at others. Both readings are tried; the
    check passes if either one agrees, and says which reading was used.
    """
    g = lambda name: nums.get((name, pos))  # noqa: E731
    total, parent, nci, redeemable = (
        g("total_equity"), g("total_parent_equity"), g("noncontrolling_interest"),
        g("redeemable_interest"),
    )
    readings = []  # (summands, note)
    if total:
        readings.append(([tl, total], ""))
    elif parent:
        readings.append(([tl, parent], ""))
        if nci:
            readings.append(([tl, parent, nci], ""))
            readings[0] = (readings[0][0],
                           " (equity subtotal read as already including noncontrolling interests)")
    if not readings:
        return None
    candidates = []
    for summands, note in readings:
        if redeemable:
            summands = summands + [redeemable]
        check = _compare(
            "liabilities_plus_equity", "medium",
            "Total liabilities plus equity (including any noncontrolling or redeemable "
            "interests shown) should equal total liabilities and equity. A difference "
            "can still be legitimate, so review it." + note,
            summands + [tle], tle.displayed_value,
            sum(x.displayed_value for x in summands), len(summands))
        candidates.append(check)
    return min(candidates, key=lambda c: (_RANK[c.status], abs(c.difference)))


def _page_identity_checks(records: list[NormalizedRecord]) -> list[Check]:
    nums = _first_numbers(records)
    cells = {
        (r.row_order, r.cell_position): r
        for r in records if r.kind == "number" and r.displayed_value is not None
    }
    out: list[Check] = []
    positions = sorted({pos for (_, pos) in nums})
    for pos in positions:
        g = lambda name: nums.get((name, pos))  # noqa: E731
        ta, tle = g("total_assets"), g("total_liabilities_and_equity")
        tl = g("total_liabilities")
        tca, tcl = g("total_current_assets"), g("total_current_liabilities")
        rev, cogs, gp = g("revenue"), g("cost_of_revenue"), g("gross_profit")

        if ta and tle:
            out.append(_compare(
                "balance_equation", "high",
                "Total assets should equal total liabilities and equity.",
                [ta, tle], tle.displayed_value, ta.displayed_value, 0))
        if tl and tle:
            candidate = _equity_check(tl, tle, nums, pos)
            if candidate:
                out.append(candidate)
        if tca and ta and tca.displayed_value > ta.displayed_value:
            out.append(Check(
                "current_assets_within_total", tca.document_id, tca.page_index, _column(tca),
                "fail", "high", "Total current assets are larger than total assets.",
                ta.displayed_value, tca.displayed_value, tca.displayed_value - ta.displayed_value,
                (_ref(tca), _ref(ta))))
        if tcl and tl and tcl.displayed_value > tl.displayed_value:
            out.append(Check(
                "current_liabilities_within_total", tcl.document_id, tcl.page_index, _column(tcl),
                "fail", "high", "Total current liabilities are larger than total liabilities.",
                tl.displayed_value, tcl.displayed_value, tcl.displayed_value - tl.displayed_value,
                (_ref(tcl), _ref(tl))))
        if rev and cogs and gp:
            rows_between = sorted(
                row for (row, p) in cells if p == pos and cogs.row_order < row < gp.row_order
            )
            out.append(_gross_profit_check(rev, cogs, gp, [cells[(row, pos)] for row in rows_between]))
        out.extend(_partial_input_checks(g))
    return out


def _partial_input_checks(g) -> list[Check]:
    """Say so when a check could have run but one of its figures is missing in this column.

    Without this, a line lost by extraction removes the check and the page looks clean.
    Only cases where the missing figure is expected are flagged: a company that reports no
    gross profit at all is not flagged, so losing the gross profit line itself is not caught.
    """
    out: list[Check] = []
    tl, tle, ta = g("total_liabilities"), g("total_liabilities_and_equity"), g("total_assets")
    equity = g("total_equity") or g("total_parent_equity")
    tca, tcl = g("total_current_assets"), g("total_current_liabilities")
    rev, cogs, gp = g("revenue"), g("cost_of_revenue"), g("gross_profit")

    def flag(rule, anchor, missing):
        out.append(Check(
            rule, anchor.document_id, anchor.page_index, _column(anchor), "not_testable", "low",
            f"{rule.replace('_', ' ')} was not tested for this column: {', '.join(missing)} "
            "not found. A line may not have been extracted, or the statement may continue on "
            "another page.",
            evidence=(_ref(anchor),)))

    group = [("total_liabilities", tl), ("total_liabilities_and_equity", tle),
             ("total equity", equity)]
    present = [r for _, r in group if r]
    if present and len(present) < 3 and present[0].statement_type == "balance_sheet":
        flag("liabilities_plus_equity", present[0], [n for n, r in group if not r])
    if tca and not ta and tca.statement_type == "balance_sheet":
        flag("current_assets_within_total", tca, ["total_assets"])
    if tcl and not tl and tcl.statement_type == "balance_sheet":
        flag("current_liabilities_within_total", tcl, ["total_liabilities"])
    if gp and not (rev and cogs) and gp.statement_type == "income_statement":
        flag("gross_profit", gp, [n for n, r in (("revenue", rev), ("cost_of_revenue", cogs)) if not r])
    return out


def _page_quality_checks(records: list[NormalizedRecord]) -> list[Check]:
    first = records[0]
    page = first.page_index
    out: list[Check] = []

    low = [r for r in records
           if r.method == "ocr" and r.kind == "number" and r.confidence is not None
           and Decimal(str(r.confidence)) < LOW_OCR_CONFIDENCE]
    if low:
        out.append(Check(
            "low_ocr_confidence", first.document_id, page, "", "flag", "medium",
            f"{len(low)} number(s) read by OCR with confidence below {LOW_OCR_CONFIDENCE}; "
            "check them against the page image.",
            evidence=tuple(_ref(r) for r in low[:20])))

    unscaled = [r for r in records
                if r.kind == "number" and r.scale_status in ("scale_unknown", "scale_conflict")]
    if unscaled:
        kinds = sorted({r.scale_status for r in unscaled})
        out.append(Check(
            "scale_not_determined", first.document_id, page, "", "flag", "medium",
            f"{len(unscaled)} number(s) could not be scaled ({', '.join(kinds)}); they cannot "
            "be compared with other documents until the unit scale is confirmed.",
            evidence=tuple(_ref(r) for r in unscaled[:20])))

    warned = {}
    for r in records:
        if r.row_warnings:
            warned.setdefault(r.row_order, r)
    if warned:
        texts = sorted({w for r in warned.values() for w in r.row_warnings})[:5]
        out.append(Check(
            "extraction_warnings", first.document_id, page, "", "flag", "low",
            f"{len(warned)} row(s) carry extraction warnings: " + "; ".join(texts),
            evidence=tuple(_ref(r) for r in list(warned.values())[:20])))
    return out


def _page_coverage_checks(records: list[NormalizedRecord], identity: list[Check]) -> list[Check]:
    """Say so when a page that looks like a balance sheet could not be tested at all."""
    first = records[0]
    if first.statement_type != "balance_sheet":
        return []
    if any(c.rule_id == "balance_equation" for c in identity):
        return []
    wanted = ("total_assets", "total_liabilities_and_equity")
    found = {r.label_canonical for r in records
             if r.kind == "number" and r.displayed_value is not None}
    missing = [name for name in wanted if name not in found]
    detail = (" and ".join(missing) + " not found" if missing
              else "total assets and total liabilities and equity were not found in the same column")
    return [Check(
        "balance_equation", first.document_id, first.page_index, "", "not_testable", "medium",
        "This page looks like a balance sheet but the balance equation could not be tested: "
        + detail + ". Either the page is not a balance sheet, a line was not extracted, or the "
        "statement continues on another page.")]


def run_checks(records: list[NormalizedRecord]) -> list[Check]:
    """Run every check on the records of one or more documents."""
    groups: dict[tuple, list[NormalizedRecord]] = defaultdict(list)
    for r in records:
        groups[(r.document_id, r.page_index, r.sheet_name)].append(r)
    checks: list[Check] = []
    for key in sorted(groups, key=lambda k: (k[0], -1 if k[1] is None else k[1], k[2] or "")):
        page_records = groups[key]
        identity = _page_identity_checks(page_records)
        checks.extend(identity)
        checks.extend(_page_coverage_checks(page_records, identity))
        checks.extend(_page_quality_checks(page_records))
    return checks
