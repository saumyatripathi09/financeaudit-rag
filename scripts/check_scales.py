from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.normalization.scale import detect_scale

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

statuses = Counter()
for doc_name, page_no in pages:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        text = doc[page_no].get_text()
        previous = doc[page_no - 1].get_text() if page_no > 0 else ""
    result = detect_scale(text)
    statuses[result.status] += 1
    phrases = "; ".join(s.snippet[:55] for s in result.statements) or "-"
    print(f"{doc_name:28} p{page_no:<4} {result.status:9} {phrases}")
    if result.status != "found":
        earlier = detect_scale(previous)
        if earlier.statements:
            print(f"{'':36} previous page says: {earlier.statements[0].snippet[:70]}")

print("\n", dict(statuses))
