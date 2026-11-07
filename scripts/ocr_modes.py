"""Compare Tesseract page-segmentation modes on real pages.

usage: python scripts/ocr_modes.py [pages, default 10] [modes, default 3 4 6 11]
Keep the number of pages at 20 or fewer: each rendered page is held in memory.
"""
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion import ocr_ingest
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
modes = [int(m) for m in sys.argv[2:]] or [3, 4, 6, 11]
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

offset = int(os.environ.get("OCR_OFFSET", "0"))
print(f"starting at candidate page {offset}")
cases = []
for doc_name, page_no in pages[offset:]:
    if len(cases) >= limit:
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
    truth = {norm(c.raw_text) for r in truth_rows for c in r.cells}
    cases.append((doc_name, page_no, pdf.name, image, truth))

print(f"{len(cases)} pages; low-confidence threshold {LOW_CONFIDENCE:.0%}\n")
print(f"{'mode':>5} {'read':>6} {'wrong':>6} {'flagged':>8} {'SILENT':>7} {'missed':>7} {'of':>5} {'seconds':>8}")
for mode in modes:
    ocr_ingest.OCR_CONFIG = f"--psm {mode}"
    read = wrong = flagged = missed = total = 0
    started = time.perf_counter()
    for doc_name, page_no, filename, image, truth in cases:
        rows, _ = read_image_rows(
            image, page_index=page_no, document_id=doc_name, filename=filename,
            scale=72.0 / RENDER_DPI,
        )
        cells = [c for r in rows for c in r.cells]
        bad = [c for c in cells if norm(c.raw_text) not in truth]
        read += len(cells)
        wrong += len(bad)
        flagged += sum(1 for c in bad if c.confidence is not None and c.confidence < LOW_CONFIDENCE)
        missed += len(truth - {norm(c.raw_text) for c in cells})
        total += len(truth)
    elapsed = time.perf_counter() - started
    print(f"{mode:>5} {read:>6} {wrong:>6} {flagged:>8} {wrong - flagged:>7} {missed:>7} {total:>5} {elapsed:>8.0f}")
