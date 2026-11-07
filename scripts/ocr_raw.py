import sys
from pathlib import Path

import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import RENDER_DPI, _prepare_tesseract, words_from_image
from audit_rag.ingestion.pdf_text import (
    Word, find_header, group_into_rows, merge_orphan_rows,
)

doc_name, page_no = sys.argv[1], int(sys.argv[2])
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 12
_prepare_tesseract(get_settings())
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"

with pymupdf.open(pdf) as doc:
    page = doc[page_no]
    print(f"page size {page.rect.width:.0f} x {page.rect.height:.0f} points, rotation {page.rotation}")
    embedded = [Word(x0, y0, x1, y1, t) for x0, y0, x1, y1, t, *_ in page.get_text("words")]
    pixmap = page.get_pixmap(dpi=RENDER_DPI)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

ocr = words_from_image(image, 72.0 / RENDER_DPI)
print(f"image {image.width} x {image.height} pixels")
print(f"words: embedded text {len(embedded)}, OCR {len(ocr)}")
if ocr:
    mean = sum(w.confidence for w in ocr) / len(ocr)
    print(f"OCR mean confidence {mean:.0%}")


def show(name, words):
    rows = merge_orphan_rows(group_into_rows(words))
    header = find_header(rows)
    print(f"\n=== {name}: {len(rows)} rows")
    if header is None:
        print("no year header found; first rows:")
        start = 0
    else:
        index, columns = header
        print("header words:", [(c.text, round(c.x0)) for c in columns], f"(row {index})")
        start = max(0, index - 1)
    for row in rows[start:start + limit]:
        print("  " + " ".join(w.text for w in row)[:130])


show("embedded text", embedded)
show("OCR", ocr)
