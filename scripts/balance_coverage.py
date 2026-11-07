import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows

root = Path("samples/financebench")
questions = pd.read_json(root / "data" / "financebench_open_source.jsonl", lines=True)
doc_names = sorted(set(questions["doc_name"]))


def doc_type(name):
    for kind in ("10K", "10Q", "8K", "EARNINGS"):
        if f"_{kind}" in name:
            return kind
    return "other"


def to_decimal(raw):
    text = raw.strip()
    negative = text.startswith("(") or text.startswith("-")
    digits = re.sub(r"[^\d.]", "", text)
    if not digits:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def find_row(rows, predicate):
    for row in rows:
        if row.cells and predicate(" ".join(row.label.split()).lower()):
            return row
    return None


def is_assets(label):
    return label.startswith("total assets")


def is_liab_equity(label):
    return label.startswith("total liabilities and") and (
        "equity" in label or "deficit" in label or "capital" in label
    )


def by_column(row):
    labels = [c.column_label for c in row.cells]
    if len(set(labels)) != len(labels):
        return None
    return {c.column_label: to_decimal(c.raw_text) for c in row.cells}


def cells_of(row):
    return [(c.raw_text, c.column_label) for c in row.cells]


def evaluate(rows):
    assets = find_row(rows, is_assets)
    liab_eq = find_row(rows, is_liab_equity)
    if assets is None or liab_eq is None:
        return "NO_PAIR", ""
    a, l = by_column(assets), by_column(liab_eq)
    if a is None or l is None:
        return "UNVERIFIABLE", f"assets={cells_of(assets)} | L+E={cells_of(liab_eq)}"
    shared = sorted(set(a) & set(l))
    if not shared:
        return "UNVERIFIABLE", "no shared columns"
    bad = [
        (c, a[c], l[c]) for c in shared
        if a[c] is None or l[c] is None or a[c] != l[c]
    ]
    return ("FAIL", str(bad)) if bad else ("PASS", "")


per_doc = {}
for doc_name in doc_names:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    pages = []
    with pymupdf.open(pdf) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            if "total assets" not in " ".join(page.get_text().split()).lower():
                continue
            rows = extract_page_rows(page, index, doc_name, pdf.name)
            status, detail = evaluate(rows)
            if status == "NO_PAIR":
                assets_labels = [r.label[:40] for r in rows if "total assets" in r.label.lower()][:2]
                le_labels = [r.label[:50] for r in rows if "liabilities and" in r.label.lower()][:2]
                detail = f"rows={len(rows)} assets-like={assets_labels} L+E-like={le_labels}"
            pages.append((index, status, detail))
    per_doc[doc_name] = pages

print("coverage: filings with at least one verified balance sheet page")
by_type = {}
for name, pages in per_doc.items():
    entry = by_type.setdefault(doc_type(name), [0, 0])
    entry[0] += 1
    if any(status == "PASS" for _, status, _ in pages):
        entry[1] += 1
for kind, (total, passed) in sorted(by_type.items()):
    print(f"  {kind:9} {passed:3} of {total:3} filings have a PASS page")

print("\nFAIL pages (should be none):")
for name, pages in per_doc.items():
    for index, status, detail in pages:
        if status == "FAIL":
            print(f"  {name} p{index}: {detail}")

print("\nUNVERIFIABLE pages:")
for name, pages in per_doc.items():
    for index, status, detail in pages:
        if status == "UNVERIFIABLE":
            print(f"  {name} p{index}: {detail[:230]}")

print("\n10-K / 10-Q filings with NO verified page (first 25):")
shown = 0
for name, pages in per_doc.items():
    if doc_type(name) not in ("10K", "10Q"):
        continue
    if any(status in ("PASS", "UNVERIFIABLE", "FAIL") for _, status, _ in pages):
        continue
    shown += 1
    if shown > 25:
        break
    print(f"  {name}: {len(pages)} page(s) mention 'total assets'")
    for index, _, detail in pages[:3]:
        print(f"      p{index}: {detail[:200]}")


