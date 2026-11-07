import re
import sys
from pathlib import Path

import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import RENDER_DPI, _prepare_tesseract, read_image_rows
from audit_rag.ingestion.pdf_text import extract_page_rows

def norm(text):
    """Compare values ignoring a dollar sign, which OCR attaches to the number."""
    return text.replace("$", "").strip()


def label_key(label):
    """A label without its dot leader, so '.....' and OCR's garbled version match."""
    return re.split(r"\.{2,}", label)[0].strip().lower()


doc_name, page_no = sys.argv[1], int(sys.argv[2])
_prepare_tesseract(get_settings())
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"

with pymupdf.open(pdf) as doc:
    page = doc[page_no]
    truth_rows = extract_page_rows(page, page_no, doc_name, pdf.name)
    pixmap = page.get_pixmap(dpi=RENDER_DPI)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

ocr_rows, _ = read_image_rows(
    image, page_index=page_no, document_id=doc_name, filename=pdf.name,
    scale=72.0 / RENDER_DPI,
)

truth = {norm(c.raw_text) for r in truth_rows for c in r.cells}
ocr = {norm(c.raw_text) for r in ocr_rows for c in r.cells}
ocr_labels = {label_key(r.label) for r in ocr_rows}

print(f"{doc_name} p{page_no}: {len(truth_rows)} rows from embedded text, {len(ocr_rows)} from OCR")
print("\nOCR values that are not in the embedded text:")
for row in ocr_rows:
    for cell in row.cells:
        if norm(cell.raw_text) not in truth:
            conf = "n/a" if cell.confidence is None else f"{cell.confidence:.0%}"
            print(f"  {row.label[:45]!r}: {cell.raw_text!r} ({cell.column_label}, confidence {conf})")

print("\nembedded values that OCR did not produce:")
for row in truth_rows:
    for cell in row.cells:
        if norm(cell.raw_text) not in ocr:
            print(f"  {row.label[:45]!r}: {cell.raw_text!r} ({cell.column_label})")

print("\nrows (by label) that OCR did not produce:")
for row in truth_rows:
    if label_key(row.label) not in ocr_labels:
        print(f"  {row.label[:70]!r} with {len(row.cells)} value(s)")
