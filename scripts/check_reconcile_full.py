"""Phase 7 on whole 10-K filings: every page of every filing of the named companies.

  python scripts\\check_reconcile_full.py WALMART
  python scripts\\check_reconcile_full.py WALMART NIKE LOCKHEEDMARTIN

Reads samples/financebench/pdfs/<COMPANY>_<YEAR>_10K.pdf, normalises every page the same way
as check_rules.py, then reconciles the filings against each other. Read-only; writes
reconcile_full_results.csv. Slow on purpose: it processes every page. A "differs" is a
question for a person (restatement or extraction error), not a verdict.
"""
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from audit_rag.audit.reconcile import observations, parse_filing_name, reconcile
from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.pipeline import normalize_rows

ROOT = Path("samples/financebench")
companies = sys.argv[1:] or ["WALMART"]


def document_records(doc_name):
    pdf = ROOT / "pdfs" / f"{doc_name}.pdf"
    records = []
    with pymupdf.open(pdf) as doc:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            text = page.get_text()
            rows = extract_page_rows(page, page_no, doc_name, pdf.name)
            if rows:
                records.extend(normalize_rows(rows, lambda i, t=text: t).records)
    return records


names = []
for company in companies:
    found = sorted(p.stem for p in (ROOT / "pdfs").glob(f"{company}_*_10K.pdf"))
    if not found:
        print(f"no 10-K PDFs found for {company}")
    names.extend(n for n in found if parse_filing_name(n))

records_by_doc = {}
for name in names:
    start = time.time()
    records_by_doc[name] = document_records(name)
    obs, _ = observations(records_by_doc[name])
    print(f"{name}: {len(records_by_doc[name])} records, {len(obs)} comparable values, "
          f"{time.time() - start:.0f}s", flush=True)

comparisons, stats = reconcile(records_by_doc)
print(f"\nfilings: {len(records_by_doc)}; comparisons made: {len(comparisons)}")
print("skipped or ignored:", dict(stats))
by_status = Counter(c.status for c in comparisons)
print("status:", dict(by_status))
print("\nstatement type / status:")
for key, n in sorted(Counter((c.statement_type, c.status) for c in comparisons).items()):
    print(f"  {key[0]:18} {key[1]:9} {n}")
print("\nper pair of filings:")
pairs = defaultdict(Counter)
for c in comparisons:
    pairs[(c.earlier_doc, c.later_doc)][c.status] += 1
for (a, b), counts in sorted(pairs.items()):
    print(f"  {a} vs {b}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
print("\nby line:")
by_label = defaultdict(Counter)
for c in comparisons:
    by_label[c.canonical][c.status] += 1
for label, counts in sorted(by_label.items()):
    print(f"  {label:30} " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

differs = [c for c in comparisons if c.status == "differs"]
print(f"\ndiffers: {len(differs)} (showing up to 60):")
for c in differs[:60]:
    print(f"  {c.statement_type} {c.canonical} {c.year}: {c.earlier_doc} p{c.earlier_page} "
          f"{c.earlier_raw!r} vs {c.later_doc} p{c.later_page} {c.later_raw!r} "
          f"(diff {c.difference})")

with open("reconcile_full_results.csv", "w", newline="", encoding="ascii", errors="replace") as f:
    w = csv.writer(f)
    w.writerow(["company", "statement", "line", "year", "earlier_doc", "earlier_page",
                "earlier_raw", "later_doc", "later_page", "later_raw", "earlier_value",
                "later_value", "difference", "status"])
    for c in comparisons:
        w.writerow([c.company, c.statement_type, c.canonical, c.year, c.earlier_doc,
                    c.earlier_page, c.earlier_raw, c.later_doc, c.later_page, c.later_raw,
                    c.earlier_value, c.later_value, c.difference, c.status])
print("\nwrote reconcile_full_results.csv")
