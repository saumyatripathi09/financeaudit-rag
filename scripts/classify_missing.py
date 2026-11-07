import re
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import Word, extract_page_rows, group_into_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
STRIP = "()$%-,;:"


def clean(text):
    return text.strip(STRIP)


def numbers_in(text):
    found = set()
    for token in NUMBER_RE.findall(text):
        token = token.rstrip(",.")
        if len(token) >= 3 and not YEAR_RE.match(token):
            found.add(token)
    return found


IN_LABEL = "in a row label (par value, share counts)"
NOT_EXTRACTED = "ON THE PAGE but not extracted: investigate"
NOT_ON_PAGE = "not on the page as its own word (evidence text quirk?)"

counts = Counter()
to_investigate = []
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
            rows = extract_page_rows(page, page_no, doc_name, pdf.name)
            words = [
                Word(x0, y0, x1, y1, text)
                for x0, y0, x1, y1, text, *_ in page.get_text("words")
            ]
        if not rows:
            counts["passages skipped: no table found"] += 1
            continue

        extracted = {clean(c.raw_text) for r in rows for c in r.cells}
        label_tokens = {clean(t) for r in rows for t in r.label.split()}
        page_tokens = {clean(w.text) for w in words}

        for number in sorted(wanted - extracted):
            if number in label_tokens:
                counts[IN_LABEL] += 1
            elif number in page_tokens:
                counts[NOT_EXTRACTED] += 1
                if (doc_name, page_no, number) not in seen:
                    seen.add((doc_name, page_no, number))
                    row_words = next(
                        rw for rw in group_into_rows(words)
                        if any(clean(w.text) == number for w in rw)
                    )
                    context = " ".join(w.text for w in row_words)[:110]
                    to_investigate.append((doc_name, page_no, number, context))
            else:
                counts[NOT_ON_PAGE] += 1

print("outcome for each missing number:")
for label, n in counts.most_common():
    print(f"{n:5}  {label}")
print()
print(f"numbers that are on the page but were NOT extracted ({len(to_investigate)} unique):")
for doc_name, page_no, number, context in to_investigate[:60]:
    print(f"{doc_name} p{page_no} {number:>10} | {context}")
