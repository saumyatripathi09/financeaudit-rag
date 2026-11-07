import re
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.scale import detect_scale
from audit_rag.normalization.scaling import scale_rows

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

SUSPECT = re.compile(r"average|outstanding|weighted|units|number of|issued|authorized", re.I)
statuses, review_labels, suspects = Counter(), Counter(), Counter()
for doc_name, page_no in pages:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        page = doc[page_no]
        scale = detect_scale(page.get_text())
        rows = extract_page_rows(page, page_no, doc_name, pdf.name)
    for scaled_row in scale_rows(rows, scale):
        for amount in scaled_row.amounts:
            statuses[amount.status] += 1
            label = f"{scaled_row.context[:30]} > {scaled_row.row.label[:40]}"
            if amount.status == "needs_review":
                review_labels[label] += 1
            elif amount.status == "scaled" and SUSPECT.search(scaled_row.row.label):
                suspects[f"{doc_name} p{page_no} | {scaled_row.row.label[:60]}"] += 1

print("values by status:", dict(statuses), "total", sum(statuses.values()))
print("\nsent to review (most common):")
for label, count in review_labels.most_common(15):
    print(f"  {count:3} x {label}")
print(f"\nSCALED rows whose label suggests a share count or similar ({len(suspects)}); check these by eye:")
for label, count in list(suspects.items())[:25]:
    print(f"  {label}")
