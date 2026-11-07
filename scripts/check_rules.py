"""Run the audit checks on the FinanceBench pages and list what fires.

These are real filings that were not altered, so a "fail" is either a genuine anomaly or
a mistake in our extraction. Each one printed here must be looked at before it is trusted.
"""
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.audit.checks import run_checks
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

tally = Counter()
shown = {"fail": [], "flag": [], "rounding": []}
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
    result = normalize_rows(rows, lambda i, t=text: t)
    for c in run_checks(result.records):
        tally[(c.rule_id, c.status)] += 1
        if c.status in shown:
            shown[c.status].append(
                f"{doc_name} p{page_no} {c.rule_id} {c.column}: expected {c.expected} "
                f"actual {c.actual} | {c.message[:70]}"
            )

print(f"pages examined: {len(pages)} (all evidence pages, every statement type)")
for (rule, status), n in sorted(tally.items()):
    print(f"  {rule:34} {status:9} {n}")
for status in ("fail", "rounding", "flag"):
    print(f"\n{status} (first 15):")
    for line in shown[status][:15]:
        print("  ", line)
