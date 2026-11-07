"""Read-only probe: what could cross-document reconciliation (Phase 7) actually be tested on?

Lists, per company, which filings have evidence pages, which statement types were detected
per page, and how often each canonical label occurs. Changes nothing.
"""
from collections import Counter, defaultdict

from _evidence import evidence_pages

docs = defaultdict(lambda: defaultdict(set))
labels = Counter()
for doc_name, page_no, records in evidence_pages():
    docs[doc_name][page_no] = {r.statement_type for r in records}
    for r in records:
        if r.label_canonical and r.kind == "number":
            labels[r.label_canonical] += 1

companies = defaultdict(list)
for name in sorted(docs):
    companies[name.split("_")[0]].append(name)  # naming convention assumed: COMPANY_YEAR_TYPE

print(f"documents with evidence pages: {len(docs)}; companies: {len(companies)}")
multi = {c: n for c, n in companies.items() if len(n) > 1}
print(f"companies with more than one filing: {len(multi)}")
for company, names in sorted(multi.items()):
    print(f"  {company}: {', '.join(n.split('_', 1)[1] for n in names)}")

print("\ndocuments with evidence pages of more than one statement type:")
for name in sorted(docs):
    kinds = sorted({s for page in docs[name].values() for s in page} - {"unknown"})
    if len(kinds) > 1:
        print(f"  {name}: pages {sorted(docs[name])} -> {kinds}")

print("\ncanonical labels seen on numbers (count):")
for label, n in labels.most_common():
    print(f"  {label:40} {n}")
