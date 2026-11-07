"""Run the Phase 4 pipeline on the FinanceBench balance sheet pages and measure it.

Reports: statement types found, how many rows got a canonical label, and the balance
sheet equation (total assets = total liabilities and equity) on pages found to be balance
sheets. A failing equation is a silent-error signal, so each failure is listed.
"""
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

from audit_rag.ingestion.pdf_text import extract_page_rows
from audit_rag.normalization.pipeline import normalize_rows

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

statement_pages = Counter()
not_balance = []
rows_with_cells = rows_canonical = 0
eq_ok = eq_bad = eq_missing = 0
failures = []

for doc_name, page_no in pages:
    pdf = root / "pdfs" / f"{doc_name}.pdf"
    if not pdf.exists():
        continue
    with pymupdf.open(pdf) as doc:
        if page_no >= doc.page_count:
            continue
        page = doc[page_no]
        text = page.get_text()
        rows = extract_page_rows(page, page_no, doc_name, pdf.name)
    result = normalize_rows(rows, lambda i, t=text: t)
    guess = result.statement_by_page.get(page_no)
    kind = guess.statement if guess else "unknown"
    statement_pages[kind] += 1
    if kind != "balance_sheet":
        not_balance.append(f"{doc_name} p{page_no} -> {kind}")

    seen = set()
    for r in result.records:
        if (r.row_order) not in seen:
            seen.add(r.row_order)
            rows_with_cells += 1
            rows_canonical += r.label_canonical is not None

    if kind != "balance_sheet":
        continue
    first = {}
    for r in result.records:
        if r.label_canonical in ("total_assets", "total_liabilities_and_equity") and r.kind == "number":
            first.setdefault((r.label_canonical, r.cell_position), r)
    positions = sorted({pos for (_, pos) in first})
    if not positions:
        eq_missing += 1
        continue
    for pos in positions:
        a = first.get(("total_assets", pos))
        e = first.get(("total_liabilities_and_equity", pos))
        if a is None or e is None:
            eq_missing += 1
        elif a.displayed_value == e.displayed_value:
            eq_ok += 1
        else:
            eq_bad += 1
            failures.append(
                f"{doc_name} p{page_no} col{pos}: assets {a.raw_text} vs L+E {e.raw_text}"
            )

print("pages by statement type:", dict(statement_pages))
print(f"rows with a canonical label: {rows_canonical} of {rows_with_cells}")
print(f"balance equation: ok {eq_ok}, differs {eq_bad}, one side missing {eq_missing}")
print("\nbalance sheet questions whose page was NOT typed balance_sheet (first 20):")
for line in not_balance[:20]:
    print("  ", line)
print("\nequation failures (first 20):")
for line in failures[:20]:
    print("  ", line)
