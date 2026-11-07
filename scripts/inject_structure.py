"""Drop or shift whole rows on real pages and see whether the audit rules notice.

  python scripts\\inject_structure.py            all evidence pages
  python scripts\\inject_structure.py 30         first 30 evidence pages only

drop_row: a row is lost. shift_right: a row's cells land one column to the right. One fault
at a time on a copy of the page; only rows that the rules verify are touched. Writes
structure_results.csv. Counts only: they describe these pages, not other documents.
"""
import csv
import sys
from collections import Counter

from _evidence import evidence_pages
from audit_rag.audit.injection import tally
from audit_rag.audit.structural import FAULTS, OUTCOMES, inject_structure

limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
SHORT = {"flagged": "flagged", "check_lost": "LOST", "lost_but_reported": "reported",
         "no_visible_effect": "no effect",
         "rules_error": "error"}


def table(title, grouped, order):
    print(f"\n{title}")
    print("  " + "group".ljust(24) + "".join(SHORT[o].rjust(11) for o in OUTCOMES))
    for key in order:
        if key in grouped:
            counts = grouped[key]
            print("  " + str(key).ljust(24) + "".join(str(counts[o]).rjust(11) for o in OUTCOMES))


pages = rows = covered = 0
everything = []
for doc_name, page_no, records in evidence_pages(limit):
    result = inject_structure(records)
    pages += 1
    rows += result.rows_total
    covered += result.rows_covered
    everything.extend(result.injections)

print(f"pages examined: {pages}; rows on them: {rows}; rows used by a verified check: {covered}")
print(f"faults injected: {len(everything)}")
table("by fault", tally(everything, lambda x: x.fault), FAULTS)
table("by fault and role (total line vs component)",
      tally(everything, lambda x: f"{x.fault}/{x.role}"),
      [f"{f}/{r}" for f in FAULTS for r in ("total", "component")])
table("by statement type", tally(everything, lambda x: f"{x.statement_type}"),
      sorted({i.statement_type for i in everything}))

lost = Counter(rule for i in everything if i.outcome == "check_lost" for rule in i.rules)
print("\nchecks that silently disappeared, by rule:", dict(lost))
quiet = [i for i in everything if i.outcome == "no_visible_effect"]
print(f"no visible effect: {len(quiet)}")
for i in quiet[:15]:
    print(f"  {i.document_id} p{i.page_index} {i.label[:40]!r} {i.fault}")

with open("structure_results.csv", "w", newline="", encoding="ascii", errors="replace") as f:
    w = csv.writer(f)
    w.writerow(["document", "page", "statement", "label", "role", "fault", "outcome", "rules"])
    for i in everything:
        w.writerow([i.document_id, i.page_index, i.statement_type, i.label, i.role, i.fault,
                    i.outcome, ";".join(i.rules)])
print("\nwrote structure_results.csv")
