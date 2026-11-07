"""Which column labels stop the year from being read? (explains column_year_unclear)

  python scripts\\unclear_years.py WALMART_2018_10K

Lists the most common column labels and the pages they occur on, per statement type, for
scaled numbers whose column label does not name exactly one year. Read-only.
"""
import sys
from collections import Counter

from _evidence import document_records
from audit_rag.audit.reconcile import column_year

doc_name = sys.argv[1]
counts = Counter()
total = 0
for r in document_records(doc_name):
    if r.kind != "number" or not r.label_canonical or r.statement_type == "unknown":
        continue
    if r.scaled_value is None or r.multiplier is None or r.displayed_value is None:
        continue
    total += 1
    if column_year(r.column_label) is None:
        counts[(r.statement_type, r.column_label, r.page_index)] += 1
print(f"{doc_name}: {total} scaled numbers on known statements; "
      f"{sum(counts.values())} without a single year in the column label")
for (statement, label, page), n in counts.most_common(25):
    print(f"  {n:4} {statement:18} page {page}  column label {label!r}")
