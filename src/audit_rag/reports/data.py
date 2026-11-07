"""Collect everything a report needs about one document, from the database.

The report never recomputes an audit result: it reads the stored records and checks, and
adds only counts. It also states, in numbers, how little of the document the checks cover.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from audit_rag.audit.checks import Check
from audit_rag.audit.checks_repo import ensure_schema as ensure_checks, load_checks
from audit_rag.normalization.records import NormalizedRecord
from audit_rag.normalization.records_repo import ensure_schema as ensure_records, load_records

ARITHMETIC = ("pass", "rounding", "fail")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}
STATUS_ORDER = {"fail": 0, "flag": 1, "not_testable": 2, "rounding": 3, "pass": 4}


@dataclass(frozen=True)
class ReportItem:
    check_id: int
    check: Check
    review_status: str
    review_note: str | None


@dataclass
class ReportData:
    document_id: str
    filename: str
    file_type: str
    document_status: str
    generated_at: datetime
    records_total: int
    numbers_total: int
    rows_total: int  # rows holding at least one number
    rows_verified: int  # rows used as evidence by a check that ran (pass, rounding or fail)
    scale_status_counts: dict[str, int]
    ocr_numbers: int
    items: list[ReportItem] = field(default_factory=list)

    def status_counts(self) -> dict[str, int]:
        return dict(Counter(i.check.status for i in self.items))

    def rule_counts(self) -> dict[str, dict[str, int]]:
        table: dict[str, Counter] = {}
        for i in self.items:
            table.setdefault(i.check.rule_id, Counter())[i.check.status] += 1
        return {rule: dict(c) for rule, c in sorted(table.items())}

    def needing_attention(self) -> list[ReportItem]:
        """Failures, flags and untested checks that a person has not dismissed."""
        wanted = [i for i in self.items
                  if i.check.status in ("fail", "flag", "not_testable") and i.review_status != "dismissed"]
        return sorted(wanted, key=lambda i: (
            SEVERITY_ORDER.get(i.check.severity, 9), STATUS_ORDER.get(i.check.status, 9),
            i.check.page_index if i.check.page_index is not None else -1, i.check.rule_id))

    @property
    def verified_share(self) -> float:
        return self.rows_verified / self.rows_total if self.rows_total else 0.0


def _coverage(records: list[NormalizedRecord], checks: list[Check]) -> tuple[int, int]:
    """(rows with a number, rows used as evidence by a check that actually ran)."""
    rows = {(r.page_index, r.sheet_name, r.row_order) for r in records if r.kind == "number"}
    evidence_rows = set()
    by_page_sheet = {(r.page_index, r.sheet_name) for r in records}
    for c in checks:
        if c.status not in ARITHMETIC:
            continue
        for e in c.evidence:
            if e.document_id and e.document_id != c.document_id:
                continue  # cross-document evidence belongs to another document's rows
            for page, sheet in by_page_sheet:
                if page == e.page_index:
                    evidence_rows.add((page, sheet, e.row_order))
    return len(rows), len(rows & evidence_rows)


def build_report(conn: sqlite3.Connection, document_id: str) -> ReportData:
    ensure_records(conn)
    ensure_checks(conn)
    doc = conn.execute(
        "SELECT original_filename, file_type, status FROM documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if doc is None:
        raise ValueError(f"unknown document id: {document_id}")
    records = load_records(conn, document_id)
    stored = load_checks(conn, document_id)
    checks = [s["check"] for s in stored]
    rows_total, rows_verified = _coverage(records, checks)
    numbers = [r for r in records if r.kind == "number"]
    return ReportData(
        document_id=document_id, filename=doc["original_filename"], file_type=doc["file_type"],
        document_status=doc["status"], generated_at=datetime.now(timezone.utc),
        records_total=len(records), numbers_total=len(numbers),
        rows_total=rows_total, rows_verified=rows_verified,
        scale_status_counts=dict(Counter(r.scale_status for r in numbers)),
        ocr_numbers=sum(1 for r in numbers if r.method == "ocr"),
        items=[ReportItem(s["check_id"], s["check"], s["review_status"], s["review_note"])
               for s in stored],
    )


LIMITATIONS = (
    "Only totals and a few identities are checked: balance sheet equation, liabilities plus "
    "equity, current subtotals within totals, and gross profit. Individual line items are "
    "not verified by any check.",
    "A check that could not run because a figure was not found is listed as not testable. "
    "It is not a pass. A filing that does not print a line (for example total liabilities) "
    "cannot be tested for it.",
    "If a gross profit line is lost during extraction, nothing is reported, because many "
    "companies print no gross profit.",
    "A pass shows that figures agree with each other, not that they are correct.",
    "Figures are compared in the units shown on the page. Values read by OCR are less "
    "certain than embedded text and are flagged when their confidence is low.",
    "Differences between filings can be restatements or a different reporting basis; they "
    "call for review and are not proof of an error.",
)


def evidence_text(check: Check) -> str:
    parts = []
    for e in check.evidence:
        page = f"p{e.page_index + 1} " if e.page_index is not None else ""
        doc = f"{e.document_id} " if e.document_id and e.document_id != check.document_id else ""
        parts.append(f"{doc}{page}{e.label} = {e.raw_text}")
    return "; ".join(parts)
