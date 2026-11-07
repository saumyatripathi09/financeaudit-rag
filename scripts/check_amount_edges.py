import re
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

ambiguous, footnote_like, bracket_negatives = [], [], 0
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
            where = (doc_name, page_no, row.label[:50], cell.raw_text, cell.column_label)
            if parsed.kind == "ambiguous":
                ambiguous.append(where)
            elif re.fullmatch(r"\(\d\)", cell.raw_text.strip()):
                footnote_like.append(where)
            elif parsed.value is not None and parsed.value < 0 and cell.raw_text.startswith("("):
                bracket_negatives += 1

print(f"negative values written in brackets: {bracket_negatives}")
print(f"\nambiguous cells: {len(ambiguous)}")
for item in ambiguous:
    print("  ", item)
print(f"\ncells shaped like a footnote marker, a single digit in brackets: {len(footnote_like)}")
for item in footnote_like[:15]:
    print("  ", item)
