import pymupdf
from collections import defaultdict

doc = pymupdf.open("samples/financebench/pdfs/AMCOR_2020_10K.pdf")
page = doc[49]

# Each word: (x0, y0, x1, y1, text, block, line, word_no). Positions are in points.
words = page.get_text("words")

rows = defaultdict(list)
for x0, y0, x1, y1, text, *_ in words:
    rows[round(y0)].append((x0, text))

for y in sorted(rows)[:18]:
    items = sorted(rows[y])
    print(y, " | ".join(f"{t}@{round(x)}" for x, t in items))
