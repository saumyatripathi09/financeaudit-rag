"""Error injection: corrupt one number on a copy of real records and see if the rules notice.

Nothing here touches the database or the original records. Each injection changes exactly
one value on one page, re-runs the rules on that page and records what happened:

  flagged              a rule that was not failing before now fails
  absorbed_as_rounding the affected check moved to "rounding" (inside the rounding allowance)
  cancelled_rounding   the clean page was off by rounding and the change made it exact
                       (the corrupted figures satisfy the rule, so nothing could object)
  passed_unnoticed     no new failure and no rounding status: a silent miss
  rules_error          the rules raised an exception (itself a finding)
  not_applicable       this corruption cannot be made on this value (for example 0)

Only values that take part in a check that passes (or is within rounding) on the clean page
are corrupted: those are the values the rules claim to verify. All other numbers are counted
as "not covered" and are never injected, because no rule looks at them.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal

from audit_rag.audit.checks import run_checks
from audit_rag.normalization.records import NormalizedRecord

CORRUPTIONS = ("digit", "transpose", "times1000", "div1000", "sign")
OUTCOMES = ("flagged", "absorbed_as_rounding", "cancelled_rounding", "passed_unnoticed",
            "rules_error", "not_applicable")


def corrupt_value(value: Decimal, kind: str, rng: random.Random) -> Decimal | None:
    """Return a changed copy of `value`, or None when this corruption does not apply."""
    if value == 0:
        return None
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    if kind == "digit":
        i = rng.randrange(len(digits))
        choices = [d for d in range(10) if d != digits[i]]
        if i == 0 and len(digits) > 1:
            choices = [d for d in choices if d != 0]  # keep the number of digits
        digits[i] = rng.choice(choices)
        return Decimal((sign, tuple(digits), exponent))
    if kind == "transpose":
        pairs = [i for i in range(len(digits) - 1) if digits[i] != digits[i + 1]]
        if not pairs:
            return None
        i = rng.choice(pairs)
        digits[i], digits[i + 1] = digits[i + 1], digits[i]
        return Decimal((sign, tuple(digits), exponent))
    if kind == "times1000":
        return value * Decimal(1000)
    if kind == "div1000":
        return value / Decimal(1000)
    if kind == "sign":
        return -value
    raise ValueError(f"unknown corruption: {kind}")


def role_of(record: NormalizedRecord) -> str:
    """'total' for subtotal and total lines, 'component' for everything else."""
    canonical = record.label_canonical or ""
    return "total" if canonical.startswith("total_") or canonical == "gross_profit" else "component"


@dataclass(frozen=True)
class Injection:
    document_id: str
    page_index: int | None
    row_order: int
    cell_position: int
    label: str
    role: str
    statement_type: str
    corruption: str
    original: Decimal
    corrupted: Decimal | None
    outcome: str
    rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageInjection:
    injections: list[Injection]
    numeric_values: int  # numbers on the page that could be checked at all
    covered_values: int  # numbers that take part in a passing check (and were injected)


def _fail_keys(checks) -> set[tuple]:
    keys = set()
    for c in checks:
        if c.status == "fail":
            first = c.evidence[0] if c.evidence else None
            where = (first.row_order, first.cell_position) if first else None
            keys.add((c.rule_id, c.column, where))
    return keys


def _apply(record: NormalizedRecord, new: Decimal) -> NormalizedRecord:
    scaled = record.scaled_value
    if scaled is not None and record.multiplier is not None:
        scaled = new * record.multiplier
    return replace(record, displayed_value=new, scaled_value=scaled, raw_text=str(new))


def inject_page(records: list[NormalizedRecord]) -> PageInjection:
    """Inject every corruption into every covered value of ONE page (one document, one page)."""
    baseline = run_checks(records)
    base_fails = _fail_keys(baseline)
    covered = {
        (e.row_order, e.cell_position)
        for c in baseline if c.status in ("pass", "rounding") for e in c.evidence
    }
    numeric = sum(1 for r in records if r.kind == "number" and r.displayed_value is not None)
    index = {(r.row_order, r.cell_position): i for i, r in enumerate(records)}
    out: list[Injection] = []
    for position in sorted(covered):
        i = index[position]
        target = records[i]
        for kind in CORRUPTIONS:
            rng = random.Random(
                f"{target.document_id}|{target.page_index}|{target.row_order}|"
                f"{target.cell_position}|{kind}")
            new = corrupt_value(target.displayed_value, kind, rng)
            base = dict(
                document_id=target.document_id, page_index=target.page_index,
                row_order=target.row_order, cell_position=target.cell_position,
                label=target.label, role=role_of(target), statement_type=target.statement_type,
                corruption=kind, original=target.displayed_value, corrupted=new)
            if new is None:
                out.append(Injection(**base, outcome="not_applicable"))
                continue
            changed = records[:i] + [_apply(target, new)] + records[i + 1:]
            try:
                after = run_checks(changed)
            except Exception as exc:  # a crash in the rules is a finding, not a reason to stop
                out.append(Injection(**base, outcome="rules_error", rules=(type(exc).__name__,)))
                continue
            new_fails = sorted({k[0] for k in _fail_keys(after) - base_fails})
            if new_fails:
                out.append(Injection(**base, outcome="flagged", rules=tuple(new_fails)))
                continue
            touching = [c for c in after
                        if any((e.row_order, e.cell_position) == position for e in c.evidence)]
            if any(c.status == "rounding" for c in touching):
                out.append(Injection(**base, outcome="absorbed_as_rounding"))
            else:
                was_rounding = any(
                    c.status == "rounding"
                    and any((e.row_order, e.cell_position) == position for e in c.evidence)
                    for c in baseline)
                if touching and was_rounding and all(c.status == "pass" for c in touching):
                    out.append(Injection(**base, outcome="cancelled_rounding"))
                else:
                    out.append(Injection(**base, outcome="passed_unnoticed"))
    return PageInjection(out, numeric, len(covered))


def tally(injections: list[Injection], key) -> dict:
    """Count outcomes per group, e.g. tally(items, lambda x: x.corruption)."""
    table: dict = defaultdict(Counter)
    for item in injections:
        table[key(item)][item.outcome] += 1
    return dict(table)
