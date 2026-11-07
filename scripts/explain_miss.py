"""Show the checks before and after one injection, to explain a silent miss.

  python scripts\\explain_miss.py PFIZER_2021_10K 58 "Total liabilities" digit

Arguments: document, page, start of the label (any case), corruption type (default digit).
Read-only. The corruption is the same one inject_errors.py made (same seed).
"""
import random
import sys

from _evidence import evidence_pages
from audit_rag.audit.checks import run_checks
from audit_rag.audit.injection import _apply, corrupt_value

doc, page_no, label = sys.argv[1], int(sys.argv[2]), sys.argv[3].lower()
kind = sys.argv[4] if len(sys.argv) > 4 else "digit"


def show(title, checks, positions):
    print(f"  {title}")
    for c in checks:
        mine = any((e.row_order, e.cell_position) in positions for e in c.evidence)
        print(f"   {'*' if mine else ' '} {c.rule_id:26} {c.column:12} {c.status:9} "
              f"expected={c.expected} actual={c.actual} diff={c.difference}")
        if mine:
            for e in c.evidence:
                print(f"         r{e.row_order} c{e.cell_position} {e.label[:40]!r} {e.raw_text!r}")


records = None
for d, p, recs in evidence_pages():
    if d == doc and p == page_no:
        records = recs
        break
if records is None:
    raise SystemExit("page not found among the evidence pages")

targets = [r for r in records if r.kind == "number" and r.displayed_value is not None
           and r.label.lower().startswith(label)]
print(f"{len(targets)} matching value(s) on {doc} p{page_no}; * marks checks that use the value")
baseline = run_checks(records)
for t in targets:
    rng = random.Random(f"{t.document_id}|{t.page_index}|{t.row_order}|{t.cell_position}|{kind}")
    new = corrupt_value(t.displayed_value, kind, rng)
    print(f"\nrow {t.row_order} col {t.cell_position} [{t.column_label}] "
          f"{t.raw_text!r}: {t.displayed_value} -> {new}")
    if new is None:
        continue
    pos = {(t.row_order, t.cell_position)}
    changed = [_apply(t, new) if r is t else r for r in records]
    show("before:", baseline, pos)
    show("after:", run_checks(changed), pos)
