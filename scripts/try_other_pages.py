import pandas as pd
import pymupdf
from pathlib import Path

from audit_rag.ingestion.pdf_text import extract_page_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)
mask = questions["question"].str.contains(
    "balance sheet|statement of financial position", case=False
)

for _, row in questions[mask].iterrows():
    doc_name = row["doc_name"]
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        print(f"{doc_name:35} PDF MISSING")
        continue
    with pymupdf.open(pdf) as doc:
        for evidence in row["evidence"]:
            page = evidence["evidence_page_num"]
            if page >= doc.page_count:
                print(f"{doc_name:35} page {page:3} OUT OF RANGE")
                continue
            try:
                rows = extract_page_rows(doc[page], page, doc_name, pdf.name)
            except Exception as exc:
                print(f"{doc_name:35} page {page:3} ERROR {type(exc).__name__}: {exc}")
                continue
            cells = sum(len(r.cells) for r in rows)
            print(f"{doc_name:35} page {page:3} rows={len(rows):3} cells={cells:3}")
