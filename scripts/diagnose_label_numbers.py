import re
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import (
    VALUE_ZONE_MARGIN, Word, extract_page_rows, find_header, group_into_rows,
)

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
STRIP = "()$%-,;:"
LABEL_WORDS = ("par", "share", "authorized", "issued", "outstanding", "stated")


def clean(text):
    return text.strip(STRIP)


def numbers_in(text):
    found = set()
    for token in NUMBER_RE.findall(text):
        token = token.rstrip(",.")
        if len(token) >= 3 and not YEAR_RE.match(token):
            found.add(token)
    return found


counts = Counter()
examples = []
seen = set()

for _, q in questions.iterrows():
    for ev in q["evidence"]:
        doc_name = ev.get("doc_name") or q["doc_name"]
        page_no = ev["evidence_page_num"]
        wanted = numbers_in(ev["evidence_text"])
        pdf = root / "pdfs" / f"{doc_name}.pdf"
        if not wanted or not pdf.exists():
            continue
        with pymupdf.open(pdf) as doc:
            if page_no >= doc.page_count:
                continue
            page = doc[page_no]
            extracted_rows = extract_page_rows(page, page_no, doc_name, pdf.name)
            words = [
                Word(x0, y0, x1, y1, text)
                for x0, y0, x1, y1, text, *_ in page.get_text("words")
            ]
        if not extracted_rows:
            continue
        grouped = group_into_rows(words)
        header = find_header(grouped)
        if header is None:
            continue
        h_index, years = header
        zone_start = min(y.x0 for y in years) - VALUE_ZONE_MARGIN
        header_text = " ".join(w.text for w in grouped[h_index])
        extracted = {clean(c.raw_text) for r in extracted_rows for c in r.cells}
        label_tokens = {clean(t) for r in extracted_rows for t in r.label.split()}

        for number in sorted(wanted - extracted):
            if number not in label_tokens:
                continue
            row_words = next(
                (rw for rw in grouped[h_index + 1:]
                 if any(clean(w.text) == number for w in rw)),
                None,
            )
            if row_words is None:
                counts["label number not found in a row below the header"] += 1
                continue
            word = next(w for w in row_words if clean(w.text) == number)
            row_text = " ".join(w.text for w in row_words)
            if any(key in row_text.lower() for key in LABEL_WORDS):
                counts["harmless: par/share wording in the label"] += 1
                continue
            category = (
                "LEFT of the value zone" if word.x0 < zone_start
                else "inside the zone but kept as label"
            )
            counts[category] += 1
            key = (doc_name, page_no, number)
            if key not in seen:
                seen.add(key)
                examples.append((category, doc_name, page_no, number,
                                 word.x0, zone_start, header_text, row_text))

print("why numbers ended up in labels:")
for label, n in counts.most_common():
    print(f"{n:5}  {label}")
print(f"\n{len(examples)} unique examples that are NOT par/share wording (first 40):\n")
for category, doc_name, page_no, number, x0, zone, header_text, row_text in examples[:40]:
    print(f"[{category}] {doc_name} p{page_no} {number}: x0={x0:.0f}, zone starts {zone:.0f}")
    print(f"    header chosen: {header_text[:90]}")
    print(f"    row:           {row_text[:90]}")
