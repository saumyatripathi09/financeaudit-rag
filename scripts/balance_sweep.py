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


def to_decimal(raw):
    """Throwaway parser for this check only. The real normaliser comes in Phase 4."""
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


counts = Counter()
failures = []

for doc_name in doc_names:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            if "total assets" not in " ".join(page.get_text().split()).lower():
                continue
            rows = extract_page_rows(page, index, doc_name, pdf.name)
            assets = find_row(rows, is_assets)
            liab_eq = find_row(rows, is_liab_equity)
            if assets is None or liab_eq is None:
                counts["not a balance sheet (rows not both found)"] += 1
                continue
            flagged = bool(assets.warnings or liab_eq.warnings)
            a_cols, l_cols = by_column(assets), by_column(liab_eq)
            if a_cols is None or l_cols is None:
                counts["unverifiable: two values in one column"] += 1
                continue
            shared = sorted(set(a_cols) & set(l_cols))
            if not shared:
                counts["unverifiable: no shared columns"] += 1
                continue
            bad = [
                (c, a_cols[c], l_cols[c]) for c in shared
                if a_cols[c] is None or l_cols[c] is None or a_cols[c] != l_cols[c]
            ]
            if not bad:
                counts["PASS (row flagged)" if flagged else "PASS"] += 1
            else:
                kind = "FAIL (row flagged)" if flagged else "FAIL, NO WARNING (silent error)"
                counts[kind] += 1
                failures.append((kind, doc_name, index, bad))

print("balance sheet equation across every page that mentions 'total assets':")
for label, n in counts.most_common():
    print(f"{n:5}  {label}")
print()
for kind, doc_name, index, bad in failures[:30]:
    print(f"[{kind}] {doc_name} page index {index}")
    for column, assets_value, le_value in bad:
        print(f"     column {column}: assets={assets_value} liabilities+equity={le_value}")


