import sys
from pathlib import Path

import pymupdf

from audit_rag.ingestion.pdf_text import (
    VALUE_ZONE_MARGIN, Word, extract_page_rows, find_header, group_into_rows,
)

doc_name, page_no = sys.argv[1], int(sys.argv[2])
target = sys.argv[3] if len(sys.argv) > 3 else None
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"

with pymupdf.open(pdf) as doc:
    page = doc[page_no]
    words = [Word(x0, y0, x1, y1, t) for x0, y0, x1, y1, t, *_ in page.get_text("words")]
    width = page.rect.width
    extracted = extract_page_rows(page, page_no, doc_name, pdf.name)

rows = group_into_rows(words)
header = find_header(rows)
print(f"page width {width:.0f} points, {len(rows)} rows")
if header is None:
    sys.exit("no header found")

index, years = header
zone_start = min(y.x0 for y in years) - VALUE_ZONE_MARGIN
print("header row:", " ".join(f"{w.text}[x0={w.x0:.0f},x1={w.x1:.0f}]" for w in rows[index]))
print(f"value zone starts at x={zone_start:.0f}")


def show(row_words):
    print("  " + " ".join(f"{w.text}@{w.x0:.0f}" for w in row_words)[:220])


print("\nrows just before the header:")
for r in rows[max(0, index - 2):index]:
    show(r)

if target:
    print(f"\nrows containing {target}:")
    for r in rows:
        if any(w.text.strip("()$%") == target for w in r):
            show(r)
    print("\nwhat the extractor made of rows containing it:")
    for er in extracted:
        if target in er.label or any(target in c.raw_text for c in er.cells):
            print("  label:", er.label[:100])
            print("  cells:", [(c.raw_text, c.column_label) for c in er.cells])
else:
    print("\nfirst 6 rows after the header:")
    for r in rows[index + 1:index + 7]:
        show(r)
