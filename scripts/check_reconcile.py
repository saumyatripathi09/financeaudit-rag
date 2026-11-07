"""Compare the same figure across different filings of one company (Phase 7), read-only.

  python scripts\\check_reconcile.py         summary and the first 40 differences
  python scripts\\check_reconcile.py 100     list up to 100 differences

A "differs" is a question for a person, not an error: companies restate prior years. Writes
reconcile_results.csv (every comparison) in the current folder. Counts only.
"""
import csv
import sys
from collections import Counter, defaultdict

from _evidence import evidence_pages
from audit_rag.audit.reconcile import observations, parse_filing_name, reconcile

max_lines = int(sys.argv[1]) if len(sys.argv) > 1 else 40

records_by_doc = defaultdict(list)
for doc_name, page_no, records in evidence_pages():
    records_by_doc[doc_name].extend(records)

comparisons, stats = reconcile(dict(records_by_doc))
tenk = [d for d in records_by_doc if parse_filing_name(d)]
print(f"documents: {len(records_by_doc)}; 10-K filings: {len(tenk)}")
print(f"comparisons made: {len(comparisons)}")
print("skipped or ignored:", dict(stats))

# Does the year in the file name match the newest year found in the columns?
year_check = Counter()
for d in tenk:
    obs, _ = observations(records_by_doc[d])
    if not obs:
        year_check["no comparable values"] += 1
        continue
    newest = max(o.year for o in obs)
    filing_year = parse_filing_name(d).year
    year_check["newest column = name year" if newest == filing_year
               else "newest column = name year - 1" if newest == filing_year - 1
               else "other"] += 1
print("file-name year versus newest column year:", dict(year_check))

by_status = Counter((c.statement_type, c.status) for c in comparisons)
print("\nstatement type / status:")
for (statement, status), n in sorted(by_status.items()):
    print(f"  {statement:18} {status:9} {n}")
by_label = defaultdict(Counter)
for c in comparisons:
    by_label[c.canonical][c.status] += 1
print("\nby line:")
for label, counts in sorted(by_label.items()):
    print(f"  {label:30} " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

differs = [c for c in comparisons if c.status == "differs"]
print(f"\ndiffers: {len(differs)} (showing {min(len(differs), max_lines)}):")
for c in differs[:max_lines]:
    print(f"  {c.company} {c.statement_type} {c.canonical} {c.year}: "
          f"{c.earlier_doc} p{c.earlier_page} {c.earlier_raw!r} vs "
          f"{c.later_doc} p{c.later_page} {c.later_raw!r}  (diff {c.difference})")

with open("reconcile_results.csv", "w", newline="", encoding="ascii", errors="replace") as f:
    w = csv.writer(f)
    w.writerow(["company", "statement", "line", "year", "earlier_doc", "earlier_page",
                "earlier_raw", "later_doc", "later_page", "later_raw", "earlier_value",
                "later_value", "difference", "status"])
    for c in comparisons:
        w.writerow([c.company, c.statement_type, c.canonical, c.year, c.earlier_doc,
                    c.earlier_page, c.earlier_raw, c.later_doc, c.later_page, c.later_raw,
                    c.earlier_value, c.later_value, c.difference, c.status])
print("\nwrote reconcile_results.csv")
