"""Store audit checks and the human review decisions on them.

Re-running the checks replaces a document's checks, but a person's review decision is
carried over to any identical check (same rule, page, column, status and numbers).
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from audit_rag.audit.checks import Check, EvidenceRef

REVIEW_STATUSES = ("open", "confirmed", "dismissed")
SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_checks (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,
    page_index INTEGER,
    column_label TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass','rounding','fail','flag','not_testable')),
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    expected TEXT,
    actual TEXT,
    difference TEXT,
    evidence_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'open'
        CHECK (review_status IN ('open','confirmed','dismissed')),
    review_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_doc ON audit_checks(document_id, status);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _d(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def fingerprint(c: Check) -> str:
    return "|".join([c.rule_id, str(c.page_index), c.column, c.status,
                     str(c.expected), str(c.actual)])


def save_checks(
    conn: sqlite3.Connection, document_id: str, checks: list[Check], *, cross_document: bool = False
) -> None:
    """Replace the document's checks. Caller controls the transaction.

    Single-document checks and cross-document checks are saved separately, so re-running
    one kind never erases the other.
    """
    flag = 1 if cross_document else 0
    previous = {
        r["fingerprint"]: (r["review_status"], r["review_note"])
        for r in conn.execute(
            "SELECT fingerprint, review_status, review_note FROM audit_checks "
            "WHERE document_id = ? AND review_status != 'open' "
            "AND (rule_id = 'cross_document') = ?", (document_id, flag))
    }
    conn.execute(
        "DELETE FROM audit_checks WHERE document_id = ? AND (rule_id = 'cross_document') = ?",
        (document_id, flag),
    )
    for c in checks:
        fp = fingerprint(c)
        status, note = previous.get(fp, ("open", None))
        conn.execute(
            "INSERT INTO audit_checks (document_id, rule_id, page_index, column_label, status, "
            "severity, message, expected, actual, difference, evidence_json, fingerprint, "
            "review_status, review_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.document_id, c.rule_id, c.page_index, c.column, c.status, c.severity, c.message,
             _s(c.expected), _s(c.actual), _s(c.difference),
             json.dumps([e.__dict__ for e in c.evidence]), fp, status, note))


def load_checks(conn: sqlite3.Connection, document_id: str) -> list[dict]:
    """Checks as dicts with check_id, review_status and review_note alongside a `check`."""
    out = []
    for r in conn.execute(
        "SELECT * FROM audit_checks WHERE document_id = ? ORDER BY check_id", (document_id,)
    ):
        check = Check(
            r["rule_id"], r["document_id"], r["page_index"], r["column_label"], r["status"],
            r["severity"], r["message"], _d(r["expected"]), _d(r["actual"]),
            _d(r["difference"]),
            tuple(EvidenceRef(**e) for e in json.loads(r["evidence_json"])))
        out.append({"check_id": r["check_id"], "check": check,
                    "review_status": r["review_status"], "review_note": r["review_note"]})
    return out


def set_review(conn: sqlite3.Connection, check_id: int, status: str, note: str | None = None) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"review status must be one of {REVIEW_STATUSES}")
    cur = conn.execute(
        "UPDATE audit_checks SET review_status = ?, review_note = ? WHERE check_id = ?",
        (status, note, check_id))
    if cur.rowcount != 1:
        raise ValueError(f"no such check: {check_id}")
