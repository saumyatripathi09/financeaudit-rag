"""List sentences in a filing that mention reclassification, recasting or restatement.

  python scripts\\find_notes.py WALMART_2018_10K
  python scripts\\find_notes.py WALMART_2018_10K "Accounting Standards Update" 40

Arguments: filing name, optional regular expression, optional maximum number of hits,
optional first page (0-based) to search from.
Read-only. Page numbers are 0-based, the same as in the other scripts.
"""
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path("samples/financebench")
doc_name = sys.argv[1]
pattern = re.compile(
    sys.argv[2] if len(sys.argv) > 2 else r"reclassif|recast|restat|retrospective|retroactive",
    re.IGNORECASE)
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30
first_page = int(sys.argv[4]) if len(sys.argv) > 4 else 0

hits = 0
seen = set()
with pymupdf.open(ROOT / "pdfs" / f"{doc_name}.pdf") as doc:
    for page_no in range(first_page, doc.page_count):
        text = " ".join(doc[page_no].get_text().split())
        for m in pattern.finditer(text):
            before = text.rfind(". ", 0, m.start())
            start = 0 if before < 0 else before + 2
            after = text.find(". ", m.end())
            end = len(text) if after < 0 else after + 1
            if (page_no, start) in seen:
                continue
            seen.add((page_no, start))
            print(f"p{page_no}: {text[start:end][:300]}")
            hits += 1
            if hits >= limit:
                print(f"stopped after {limit} hits")
                sys.exit(0)
print(f"{hits} hit(s)")
