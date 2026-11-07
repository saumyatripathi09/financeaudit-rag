"""What do the partial-input not_testable flags say is missing, and on which pages?

Many of these are not extraction losses: some companies print no total liabilities or no
gross profit, and our label list may not know a company's wording. This lists the cases so
they can be sorted into "line absent in the filing", "wording we do not know" and
"line lost by extraction".
"""
from collections import Counter, defaultdict
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

what = Counter()
samples = defaultdict(list)
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
    seen = {(r.row_order): r.label for r in result.records if r.cell_position == 0}
    for c in run_checks(result.records):
        if c.status != "not_testable" or c.rule_id == "balance_equation":
            continue
        missing = c.message.split("column: ")[-1].split(" not found")[0]
        what[(c.rule_id, missing)] += 1
        if len(samples[(c.rule_id, missing)]) < 4:
            have = [e.label[:35] for e in c.evidence]
            samples[(c.rule_id, missing)].append(f"{doc_name} p{page_no} (has: {have})")

for (rule, missing), n in what.most_common():
    print(f"{n:3} x {rule}: missing {missing}")
    for line in samples[(rule, missing)]:
        print("       ", line.encode("ascii", "replace").decode())
