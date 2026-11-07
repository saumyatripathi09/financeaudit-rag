"""Measure OCR accuracy on real pages: render a FinanceBench page as a picture, OCR
it, and compare with the values read from the PDF's embedded text.

usage: python scripts/ocr_accuracy.py [number of pages, default 10]
"""
import os
import sys
from pathlib import Path

import pandas as pd
import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import (
    LOW_CONFIDENCE,
    RENDER_DPI,
    _prepare_tesseract,
    read_image_rows,
)
from audit_rag.ingestion.pdf_text import extract_page_rows

def norm(text):
    """Compare values ignoring a dollar sign, which OCR attaches to the number."""
    return text.replace("$", "").strip()


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
_prepare_tesseract(get_settings())

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)
mask = questions["question"].str.contains(
    "balance sheet|statement of financial position", case=False
)

pages = []
for _, q in questions[mask].iterrows():
    for ev in q["evidence"]:
        key = (ev.get("doc_name") or q["doc_name"], ev["evidence_page_num"])
        if key not in pages:
            pages.append(key)

checked = ocr_total = wrong_total = flagged_total = missed_total = truth_total = 0
silent = []

offset = int(os.environ.get("OCR_OFFSET", "0"))
print(f"starting at candidate page {offset}")
for doc_name, page_no in pages[offset:]:
    if checked >= limit:
        break
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        page = doc[page_no]
        truth_rows = extract_page_rows(page, page_no, doc_name, pdf.name)
        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if not truth_rows:
        continue
    checked += 1

    truth = {norm(c.raw_text) for r in truth_rows for c in r.cells}
    ocr_rows, _ = read_image_rows(
        image, page_index=page_no, document_id=doc_name, filename=pdf.name,
        scale=72.0 / RENDER_DPI,
    )
    ocr_cells = [c for r in ocr_rows for c in r.cells]
    wrong = [c for c in ocr_cells if norm(c.raw_text) not in truth]
    flagged = [c for c in wrong if c.confidence is not None and c.confidence < LOW_CONFIDENCE]
    unflagged = [c for c in wrong if c not in flagged]
    missed = truth - {norm(c.raw_text) for c in ocr_cells}

    ocr_total += len(ocr_cells)
    wrong_total += len(wrong)
    flagged_total += len(flagged)
    missed_total += len(missed)
    truth_total += len(truth)
    silent.extend((doc_name, page_no, c.raw_text, c.confidence) for c in unflagged)
    print(
        f"{doc_name:30} p{page_no:<4} ocr values={len(ocr_cells):4}  "
        f"not in embedded text={len(wrong):3} (flagged {len(flagged):3})  "
        f"embedded values OCR missed={len(missed):3} of {len(truth)}"
    )

print()
print(f"pages compared:                              {checked}")
print(f"values read by OCR:                          {ocr_total}")
print(f"  not found in the embedded text:            {wrong_total}")
print(f"    caught by the low-confidence flag:       {flagged_total}")
print(f"    NOT flagged (silent errors):             {wrong_total - flagged_total}")
print(f"embedded values OCR never produced:          {missed_total} of {truth_total}")
print()
for doc_name, page_no, text, confidence in silent[:25]:
    shown = "n/a" if confidence is None else f"{confidence:.0%}"
    print(f"  silent: {doc_name} p{page_no}: {text!r} (confidence {shown})")
