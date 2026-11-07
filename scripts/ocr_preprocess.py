"""Compare image preprocessing before OCR, on real pages.

usage: python scripts/ocr_preprocess.py [pages, default 10]
Set OCR_OFFSET to start further down the list of candidate pages.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import pymupdf
from PIL import Image, ImageOps

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import (
    LOW_CONFIDENCE,
    RENDER_DPI,
    _prepare_tesseract,
    read_image_rows,
)
from audit_rag.ingestion.pdf_text import extract_page_rows

VARIANTS = {
    "none": lambda im: im,
    "gray+autocontrast": lambda im: ImageOps.autocontrast(ImageOps.grayscale(im)),
    "b/w at 170": lambda im: ImageOps.grayscale(im).point(lambda p: 255 if p > 170 else 0),
    "b/w at 200": lambda im: ImageOps.grayscale(im).point(lambda p: 255 if p > 200 else 0),
}


def norm(text):
    """Compare values ignoring a dollar sign, which OCR attaches to the number."""
    return text.replace("$", "").strip()


limit = int(sys.argv[1]) if len(sys.argv) > 1 else 10
offset = int(os.environ.get("OCR_OFFSET", "0"))
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

print(f"starting at candidate page {offset}; {len(pages)} candidates in total\n")
totals = {name: dict(read=0, wrong=0, flagged=0, missed=0) for name in VARIANTS}
names = list(VARIANTS)
done = truth_total = 0
print("missed values per page, by variant (columns follow the legend below):")

for doc_name, page_no in pages[offset:]:
    if done >= limit:
        break
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        page = doc[page_no]
        truth_rows = extract_page_rows(page, page_no, doc_name, pdf.name)
        if not truth_rows:
            continue
        pixmap = page.get_pixmap(dpi=RENDER_DPI)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    done += 1
    truth = {norm(c.raw_text) for r in truth_rows for c in r.cells}
    truth_total += len(truth)
    line = []
    for name in names:
        rows, _ = read_image_rows(
            VARIANTS[name](image), page_index=page_no, document_id=doc_name,
            filename=pdf.name, scale=72.0 / RENDER_DPI,
        )
        cells = [c for r in rows for c in r.cells]
        bad = [c for c in cells if norm(c.raw_text) not in truth]
        missed = len(truth - {norm(c.raw_text) for c in cells})
        t = totals[name]
        t["read"] += len(cells)
        t["wrong"] += len(bad)
        t["flagged"] += sum(1 for c in bad if c.confidence is not None and c.confidence < LOW_CONFIDENCE)
        t["missed"] += missed
        line.append(missed)
    print(f"  {doc_name:28} p{page_no:<4} " + "  ".join(f"{m:4}" for m in line) + f"   of {len(truth)}")

print("\nlegend: " + " | ".join(f"column {i + 1} = {n}" for i, n in enumerate(names)))
print(f"\n{done} pages\n")
print(f"{'variant':20} {'read':>6} {'wrong':>6} {'flagged':>8} {'SILENT':>7} {'missed':>7} {'of':>5}")
for name in names:
    t = totals[name]
    print(f"{name:20} {t['read']:>6} {t['wrong']:>6} {t['flagged']:>8} "
          f"{t['wrong'] - t['flagged']:>7} {t['missed']:>7} {truth_total:>5}")
