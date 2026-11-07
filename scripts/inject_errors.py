"""Corrupt one number at a time on real pages and count how many the audit rules catch.

  python scripts\\inject_errors.py              all evidence pages
  python scripts\\inject_errors.py 30           first 30 evidence pages only
  python scripts\\inject_errors.py 30 40        ... and list up to 40 silent misses

Writes injection_results.csv (every injection) in the current folder. Counts only: the
numbers say what happened on these pages, not what would happen on other documents.
"""
import csv
import sys

from _evidence import evidence_pages
from audit_rag.audit.injection import CORRUPTIONS, OUTCOMES, inject_page, tally

limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
max_misses = int(sys.argv[2]) if len(sys.argv) > 2 else 25


SHORT = {"flagged": "flagged", "absorbed_as_rounding": "rounding", "passed_unnoticed": "SILENT",
         "cancelled_rounding": "cancel",
         "rules_error": "error", "not_applicable": "n/a"}


def table(title, grouped, order):
    print(f"\n{title}")
    print("  " + "group".ljust(26) + "".join(SHORT[o].rjust(10) for o in OUTCOMES))
    for key in order:
        if key in grouped:
            counts = grouped[key]
            print("  " + str(key).ljust(26) + "".join(str(counts[o]).rjust(10) for o in OUTCOMES))


def main():
    pages = numeric = covered = pages_with_cover = 0
    everything = []
    for doc_name, page_no, records in evidence_pages(limit):
        pages += 1
        result = inject_page(records)
        numeric += result.numeric_values
        covered += result.covered_values
        pages_with_cover += 1 if result.covered_values else 0
        everything.extend(result.injections)

    done = [i for i in everything if i.outcome != "not_applicable"]
    print(f"pages examined: {pages}; pages with at least one verified value: {pages_with_cover}")
    print(f"numbers on those pages: {numeric}; numbers inside a passing check: {covered}")
    print(f"not covered by any rule (never injected): {numeric - covered}")
    print(f"injections made: {len(done)} (not applicable: {len(everything) - len(done)})")

    table("by corruption type", tally(everything, lambda x: x.corruption), CORRUPTIONS)
    table("by corruption type and role (total line vs component)",
          tally(everything, lambda x: f"{x.corruption}/{x.role}"),
          [f"{c}/{r}" for c in CORRUPTIONS for r in ("total", "component")])
    statements = sorted({i.statement_type for i in everything})
    table("by statement type", tally(everything, lambda x: x.statement_type), statements)

    misses = [i for i in done if i.outcome in ("passed_unnoticed", "absorbed_as_rounding")]
    errors = [i for i in done if i.outcome == "rules_error"]
    print(f"\nsilent misses and rounding-absorbed: {len(misses)} (first {max_misses}):")
    for i in misses[:max_misses]:
        print(f"  {i.document_id} p{i.page_index} [{i.statement_type}] {i.label[:34]!r} "
              f"{i.corruption}: {i.original} -> {i.corrupted} = {i.outcome}")
    if errors:
        print(f"\nrules raised an exception on {len(errors)} injection(s) - look at these first:")
        for i in errors[:10]:
            print(f"  {i.document_id} p{i.page_index} {i.label[:34]!r} {i.corruption}: {i.rules}")

    with open("injection_results.csv", "w", newline="", encoding="ascii", errors="replace") as f:
        w = csv.writer(f)
        w.writerow(["document", "page", "statement", "label", "role", "corruption",
                    "original", "corrupted", "outcome", "rules"])
        for i in everything:
            w.writerow([i.document_id, i.page_index, i.statement_type, i.label, i.role,
                        i.corruption, i.original, i.corrupted, i.outcome, ";".join(i.rules)])
    print("\nwrote injection_results.csv")


main()
