"""How many wrong values does each confidence threshold catch, and at what cost?

usage: python scripts/ocr_thresholds.py
"""
from pathlib import Path

import pandas as pd
import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import RENDER_DPI, _prepare_tesseract, read_image_rows
from audit_rag.ingestion.pdf_text import extract_page_rows


def norm(text):
    """Compare values ignoring a dollar sign, which OCR attaches to the number."""
    return text.replace("$", "").strip()


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

correct, wrong = [], []
for doc_name, page_no in pages:
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
    rows, _ = read_image_rows(
        image, page_index=page_no, document_id=doc_name, filename=pdf.name,
        scale=72.0 / RENDER_DPI,
    )
    for row in rows:
        for cell in row.cells:
            if cell.confidence is None:
                continue
            (correct if norm(cell.raw_text) in truth else wrong).append(cell.confidence)

print(f"{len(correct)} values matched the embedded text, {len(wrong)} did not\n")
print(f"{'threshold':>9} {'wrong caught':>14} {'correct values flagged':>26}")
for threshold in (0.80, 0.85, 0.90, 0.95):
    caught = sum(1 for c in wrong if c < threshold)
    flagged = sum(1 for c in correct if c < threshold)
    print(f"{threshold:>9.0%} {caught:>6} of {len(wrong):<5} {flagged:>10} of {len(correct)} ({flagged / len(correct):.1%})")
