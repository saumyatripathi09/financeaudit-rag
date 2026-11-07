"""Compare the same figures across the FinanceBench filings of one company.

Only evidence pages are available, so overlaps are limited. A difference printed here may
be a restatement or a different period basis; it is a prompt to look, not a verdict.
"""
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.audit.reconcile import reconcile
from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.pipeline import normalize_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)
pages = []
for _, q in questions.iterrows():
    for ev in q["evidence"]:
        key = (ev.get("doc_name") or q["doc_name"], ev["evidence_page_num"])
        if key not in pages:
            pages.append(key)

records = []
for doc_name, page_no in pages:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        page = doc[page_no]
        text = page.get_text()
        rows = extract_page_rows(page, page_no, doc_name, pdf.name)
    records.extend(normalize_rows(rows, lambda i, t=text: t).records)

KINDS = {"10K", "10Q", "8K", "EARNINGS"}
basis = lambda doc: next((t for t in doc.split("_") if t in KINDS), doc)  # period basis from the name
checks = reconcile(records, lambda doc: doc.split("_")[0], basis)
print("comparisons by status:", dict(Counter(c.status for c in checks)))
print("\ndifferences (first 25):")
shown = 0
for c in checks:
    if c.status == "fail" and shown < 25:
        shown += 1
        a, b = c.evidence
        print(f"  {c.message.split(' should')[0][:70]}")
        print(f"      {a.document_id} p{a.page_index} {a.raw_text!r}   vs   {b.document_id} p{b.page_index} {b.raw_text!r}"
              f"   difference {c.difference}")
