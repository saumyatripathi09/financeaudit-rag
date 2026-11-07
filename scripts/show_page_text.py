"""Print the text of one page of a filing, or the text around a phrase on that page.

  python scripts\\show_page_text.py WALMART_2018_10K 66
  python scripts\\show_page_text.py WALMART_2018_10K 66 "adopted this ASU"

The optional third argument is a regular expression; the text 1200 characters either side
of its first match is shown. Without it the page is shown up to 6000 characters.
Page numbers are 0-based, like the other scripts. Read-only.
"""
import re
import sys
from pathlib import Path

import pymupdf

doc_name, page_no = sys.argv[1], int(sys.argv[2])
with pymupdf.open(Path("samples/financebench/pdfs") / f"{doc_name}.pdf") as doc:
    text = " ".join(doc[page_no].get_text().split())
if len(sys.argv) > 3:
    m = re.search(sys.argv[3], text, re.IGNORECASE)
    if not m:
        raise SystemExit("phrase not found on that page")
    text = text[max(0, m.start() - 1200):m.end() + 1200]
print(text[:6000])
