from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)


def kind(text):
    if "values but the header" in text:
        return "more values than columns"
    if "same column" in text:
        return "two values in one column"
    return "label ends with numbers"


seen = set()
total_rows = flagged = 0
kinds = Counter()
examples = []

for _, q in questions.iterrows():
    for ev in q["evidence"]:
        doc_name = ev.get("doc_name") or q["doc_name"]
        page_no = ev["evidence_page_num"]
        if (doc_name, page_no) in seen:
            continue
        seen.add((doc_name, page_no))
        pdf = root / "pdfs" / f"{doc_name}.pdf"
        if not pdf.exists():
            continue
        with pymupdf.open(pdf) as doc:
            if page_no >= doc.page_count:
                continue
            rows = extract_page_rows(doc[page_no], page_no, doc_name, pdf.name)
        for row in rows:
            total_rows += 1
            if row.warnings:
                flagged += 1
                for text in row.warnings:
                    kinds[kind(text)] += 1
                if len(examples) < 14:
                    examples.append((doc_name, page_no, row.label[:55], row.warnings[0][:75]))

print(f"pages: {len(seen)}  rows: {total_rows}  rows with warnings: {flagged}")
for name, n in kinds.most_common():
    print(f"{n:5}  {name}")
print()
for doc_name, page_no, label, text in examples:
    print(f"{doc_name} p{page_no} | {label} | {text}")
