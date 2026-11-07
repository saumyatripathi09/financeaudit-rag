import re
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def numbers_in(text):
    """Numbers worth checking: at least 3 characters, and not a bare year."""
    found = set()
    for token in NUMBER_RE.findall(text):
        token = token.rstrip(",.")
        if len(token) >= 3 and not YEAR_RE.match(token):
            found.add(token)
    return found


def extracted_values(rows):
    return {cell.raw_text.strip("()$%-") for row in rows for cell in row.cells}


checked = 0
fully_matched = 0
problems = []

for _, q in questions.iterrows():
    for ev in q["evidence"]:
        wanted = numbers_in(ev["evidence_text"])
        if not wanted:
            continue
        doc_name = ev.get("doc_name") or q["doc_name"]
        page_no = ev["evidence_page_num"]
        pdf = root / "pdfs" / f"{doc_name}.pdf"
        if not pdf.exists():
            problems.append(f"{q['financebench_id']} {doc_name} p{page_no}: PDF MISSING")
            continue
        with pymupdf.open(pdf) as doc:
            if page_no >= doc.page_count:
                problems.append(f"{q['financebench_id']} {doc_name} p{page_no}: OUT OF RANGE")
                continue
            rows = extract_page_rows(doc[page_no], page_no, doc_name, pdf.name)
        checked += 1
        if not rows:
            problems.append(f"{q['financebench_id']} {doc_name} p{page_no}: NO TABLE FOUND")
            continue
        missing = sorted(wanted - extracted_values(rows))
        if missing:
            found = len(wanted) - len(missing)
            problems.append(
                f"{q['financebench_id']} {doc_name} p{page_no}: "
                f"{found}/{len(wanted)} found, missing {missing[:6]}"
            )
        else:
            fully_matched += 1

print(f"evidence passages checked: {checked}")
print(f"all evidence numbers found in our extraction: {fully_matched}")
print(f"passages with a problem: {len(problems)}")
print()
for line in problems:
    print(line)

