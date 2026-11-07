"""Show how one page was read, scaled, labelled and checked.

  python scripts\trace_page.py BESTBUY_2017_10K 55

The page number is the zero-based index used by the FinanceBench answer key.
"""
import sys
from pathlib import Path

import pymupdf

from audit_rag.audit.checks import run_checks
from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.pipeline import normalize_rows
from audit_rag.normalization.scale import detect_scale


def ascii_(text):
    return str(text).encode("ascii", "replace").decode()


if len(sys.argv) != 3:
    raise SystemExit(__doc__)
doc_name, page_no = sys.argv[1], int(sys.argv[2])
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"
with pymupdf.open(pdf) as doc:
    page = doc[page_no]
    text = page.get_text()
    rows = extract_page_rows(page, page_no, doc_name, pdf.name)

scale = detect_scale(text)
print("scale:", scale.status, scale.multiplier, "exceptions:", scale.mentions_exceptions)
for s in scale.statements:
    print("   statement:", ascii_(s.snippet))
result = normalize_rows(rows, lambda i: text)
guess = result.statement_by_page.get(page_no)
print("statement type:", guess.statement if guess else None, guess.basis if guess else "")
print("\nfirst lines of the page text:")
for line in [x for x in text.splitlines() if x.strip()][:12]:
    print("   ", ascii_(line[:100]))

print(f"\n{len(rows)} rows extracted; rows with a canonical label:")
for r in result.records:
    if r.cell_position == 0 and r.label_canonical:
        cells = [x.raw_text for x in rows[r.row_order].cells]
        print(f"   row {r.row_order:3} {r.label_canonical:30} {ascii_(r.label[:40]):40} {cells}")

print("\nchecks on this page:")
for c in run_checks(result.records):
    print(f"   {c.rule_id:28} {c.status:9} col {c.column:5} expected {c.expected} actual {c.actual}")
    for e in c.evidence[:6]:
        print(f"        row {e.row_order} {ascii_(e.label[:40])!r} -> {e.raw_text}")
