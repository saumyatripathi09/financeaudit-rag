import re
import sys
from pathlib import Path

import pymupdf

doc_name, page_no = sys.argv[1], int(sys.argv[2])
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"
pattern = re.compile(r"million|thousand|billion|lakh|crore|\bin \$|\$ in", re.I)

with pymupdf.open(pdf) as doc:
    for index in (page_no - 1, page_no):
        if index < 0:
            continue
        text = doc[index].get_text()
        hits = [" ".join(line.split()) for line in text.splitlines() if pattern.search(line)]
        print(f"--- {doc_name} page index {index}: {len(hits)} matching line(s)")
        for hit in hits[:8]:
            print("   ", hit[:140])
        print("    first lines:", " | ".join(" ".join(l.split()) for l in text.splitlines()[:6])[:200])
