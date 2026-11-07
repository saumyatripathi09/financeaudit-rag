"""List the structural faults that still lose a check silently (outcome check_lost)."""
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.audit.structural import inject_structure
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

lost = []
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
    records = normalize_rows(rows, lambda i, t=text: t).records
    for inj in inject_structure(records).injections:
        if inj.outcome == "check_lost":
            lost.append((inj.label, inj.fault, tuple(inj.rules), doc_name, page_no))

print("silently lost checks:", len(lost))
print("\nby label and fault:")
for (label, fault, rules), n in Counter((l, f, r) for l, f, r, _, _ in lost).most_common():
    print(f"  {n:3} x {label[:40]:40} {fault:11} {rules}")
print("\nfirst 10 pages:")
for label, fault, rules, doc_name, page_no in lost[:10]:
    print("  ", doc_name, page_no, label[:40].encode("ascii", "replace").decode())
