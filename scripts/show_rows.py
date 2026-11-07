import sys

import pymupdf

from audit_rag.ingestion.pdf_text import Word, group_into_rows

doc_name, page_no, limit = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
needle = sys.argv[4].lower() if len(sys.argv) > 4 else None

with pymupdf.open(f"samples/financebench/pdfs/{doc_name}.pdf") as doc:
    page = doc[page_no]
    words = [Word(x0, y0, x1, y1, t) for x0, y0, x1, y1, t, *_ in page.get_text("words")]

rows = group_into_rows(words)
print(f"{doc_name} page {page_no}: {len(rows)} rows" + (f", showing rows containing '{needle}'" if needle else ""))
shown = 0
for row in rows:
    text = " ".join(w.text for w in row)
    if needle and needle not in text.lower():
        continue
    print(f"  y={row[0].y_center:6.1f} | {text[:150]}")
    shown += 1
    if shown >= limit:
        break
