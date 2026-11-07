import sys
import pymupdf

pdf = pymupdf.open(sys.argv[1])
for shown in map(int, sys.argv[2:]):
    text = pdf[shown - 1].get_text()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    print(f"--- page {shown}: 'total assets' in text: {'total assets' in text.lower()}")
    print("   " + " | ".join(lines[:6])[:260])
