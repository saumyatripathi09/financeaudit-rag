from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.amounts import parse_amount

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)
mask = questions["question"].str.contains(
    "balance sheet|statement of financial position", case=False
)
pages = []
for _, q in questions[mask].iterrows():
    for ev in q["evidence"]:
        key = (ev.get("doc_name") or q["doc_name"], ev["evidence_page_num"])
        if key not in pages:
            pages.append(key)

kinds = Counter()
examples = {"ambiguous": Counter(), "text": Counter(), "percent": Counter()}
for doc_name, page_no in pages:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        rows = extract_page_rows(doc[page_no], page_no, doc_name, pdf.name)
    for row in rows:
        for cell in row.cells:
            parsed = parse_amount(cell.raw_text)
            kinds[parsed.kind] += 1
            if parsed.kind in examples:
                examples[parsed.kind][cell.raw_text] += 1

print("values by kind:", dict(kinds), "total", sum(kinds.values()))
for kind, counter in examples.items():
    print(f"\n{kind} (most common):")
    for text, count in counter.most_common(12):
        print(f"  {count:4} x {text!r}")
