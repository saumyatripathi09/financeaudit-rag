"""Per-page OCR quality signals with the default settings, on all candidate pages.

usage: python scripts/ocr_signals.py
"""
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

records = []
silent = []
print(
    f"{'document':28} {'page':>5} {'values':>6} {'meanconf':>8} {'rows':>5} "
    f"{'incompl':>7} {'wrong':>5} {'flagged':>7} {'SILENT':>6} {'missed':>6} {'of':>4}"
)
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
    rows, status = read_image_rows(
        image, page_index=page_no, document_id=doc_name, filename=pdf.name,
        scale=72.0 / RENDER_DPI,
    )
    cells = [c for r in rows for c in r.cells]
    value_rows = [r for r in rows if r.cells]
    incomplete = sum(1 for r in value_rows if any("may be missing" in w for w in r.warnings))
    scores = [c.confidence for c in cells if c.confidence is not None]
    mean_conf = sum(scores) / len(scores) if scores else 0.0

    wrong = [(r, c) for r in rows for c in r.cells if norm(c.raw_text) not in truth]
    flagged = 0
    for r, c in wrong:
        if c.confidence is not None and c.confidence < LOW_CONFIDENCE:
            flagged += 1
        else:
            silent.append((doc_name, page_no, r.label[:40], c.raw_text, c.confidence))
    missed = len(truth - {norm(c.raw_text) for c in cells})
    records.append(dict(missed=missed, incomplete=incomplete, conf=mean_conf, values=len(cells)))
    print(
        f"{doc_name:28} {page_no:>5} {len(cells):>6} {mean_conf:>8.0%} {len(value_rows):>5} "
        f"{incomplete:>7} {len(wrong):>5} {flagged:>7} {len(wrong) - flagged:>6} "
        f"{missed:>6} {len(truth):>4}   [{status}]"
    )

print()
for title, group in (
    ("pages with 5 or more missed values", [r for r in records if r["missed"] >= 5]),
    ("pages with fewer than 5 missed values", [r for r in records if r["missed"] < 5]),
):
    if not group:
        continue
    confs = [r["conf"] for r in group]
    incs = [r["incomplete"] for r in group]
    print(
        f"{title}: {len(group)} pages | mean confidence {min(confs):.0%} to {max(confs):.0%} "
        f"| rows flagged incomplete {min(incs)} to {max(incs)}"
    )

print("\nwrong values that were NOT flagged:")
for doc_name, page_no, label, text, confidence in silent:
    print(f"  {doc_name} p{page_no}: {label!r} -> {text!r} (confidence {confidence:.0%})")
if not silent:
    print("  none")
