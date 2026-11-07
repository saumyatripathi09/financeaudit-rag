"""Shared loader: normalised records for every FinanceBench evidence page (read-only)."""
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.pipeline import normalize_rows

ROOT = Path("samples/financebench")


def evidence_keys():
    questions = pd.read_json(ROOT / "data" / "financebench_open_source.jsonl", lines=True)
    keys = []
    for _, q in questions.iterrows():
        for ev in q["evidence"]:
            key = (ev.get("doc_name") or q["doc_name"], ev["evidence_page_num"])
            if key not in keys:
                keys.append(key)
    return keys


def evidence_pages(limit=None):
    """Yield (doc_name, page_no, records) for each evidence page that can be read."""
    for doc_name, page_no in evidence_keys()[:limit]:
        pdf = ROOT / "pdfs" / f"{doc_name}.pdf"
        if not pdf.exists():
            continue
        with pymupdf.open(pdf) as doc:
            if page_no >= doc.page_count:
                continue
            page = doc[page_no]
            text = page.get_text()
            rows = extract_page_rows(page, page_no, doc_name, pdf.name)
        result = normalize_rows(rows, lambda i, t=text: t)
        if result.records:
            yield doc_name, page_no, result.records


def document_records(doc_name):
    """Normalised records for every page of one filing (slow: reads the whole PDF)."""
    pdf = ROOT / "pdfs" / f"{doc_name}.pdf"
    records = []
    with pymupdf.open(pdf) as doc:
        for page_no in range(doc.page_count):
            page = doc[page_no]
            text = page.get_text()
            rows = extract_page_rows(page, page_no, doc_name, pdf.name)
            if rows:
                records.extend(normalize_rows(rows, lambda i, t=text: t).records)
    return records
