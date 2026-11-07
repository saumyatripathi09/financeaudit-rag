import sys
from pathlib import Path

import pymupdf
from PIL import Image

from audit_rag.config import get_settings
from audit_rag.ingestion.ocr_ingest import RENDER_DPI, _prepare_tesseract, read_image_rows

doc_name, page_no = sys.argv[1], int(sys.argv[2])
_prepare_tesseract(get_settings())
pdf = Path("samples/financebench/pdfs") / f"{doc_name}.pdf"
with pymupdf.open(pdf) as doc:
    pixmap = doc[page_no].get_pixmap(dpi=RENDER_DPI)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

rows, _ = read_image_rows(
    image, page_index=page_no, document_id=doc_name, filename=pdf.name,
    scale=72.0 / RENDER_DPI,
)
flagged = [r for r in rows if r.warnings]
print(f"{len(rows)} rows read by OCR, {len(flagged)} with warnings")
for row in flagged:
    print(f"\n  {row.label[:60]!r}  values={[c.raw_text for c in row.cells]}")
    for warning in row.warnings:
        print(f"     - {warning[:110]}")
