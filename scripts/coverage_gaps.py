"""How many pages does each statement type actually get a verifying check on? Read-only.

  python scripts\\coverage_gaps.py

For every evidence page: its statement type, whether any arithmetic check (pass, rounding or
fail) was produced, and, for balance sheet pages that got no balance equation, which of the
lines the rule needs were found. This is the measurement behind a possible "not testable"
flag: it shows how often a page is silently unchecked before any fault is injected.
"""
from collections import Counter

from _evidence import evidence_pages
from audit_rag.audit.checks import run_checks

NEEDED = ["total_assets", "total_liabilities_and_equity", "total_liabilities",
          "total_equity", "total_parent_equity"]
ARITHMETIC = ("pass", "rounding", "fail")

pages = Counter()
with_check = Counter()
gaps = []
for doc, page_no, records in evidence_pages():
    statement = records[0].statement_type
    rules = {c.rule_id for c in run_checks(records) if c.status in ARITHMETIC}
    pages[statement] += 1
    if rules:
        with_check[statement] += 1
    if statement == "balance_sheet" and "balance_equation" not in rules:
        present = {r.label_canonical for r in records
                   if r.kind == "number" and r.displayed_value is not None and r.label_canonical}
        gaps.append((doc, page_no, [n for n in NEEDED if n in present]))

print(f"{'statement type':20} {'pages':>6} {'with a check':>13} {'without':>8}")
for statement, n in sorted(pages.items()):
    print(f"{statement:20} {n:6} {with_check[statement]:13} {n - with_check[statement]:8}")

print(f"\nbalance sheet pages with no balance equation check: {len(gaps)}")
shapes = Counter(
    ("neither total" if not {"total_assets", "total_liabilities_and_equity"} & set(found)
     else "total assets only" if "total_liabilities_and_equity" not in found
     else "total liabilities and equity only" if "total_assets" not in found
     else "both totals found")
    for _, _, found in gaps)
print("by what was found:", dict(shapes))
for doc, page_no, found in gaps[:40]:
    print(f"  {doc} p{page_no}: found {found or 'none of the lines'}")
