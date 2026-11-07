"""Run the audit checks for one stored document and save them."""
from __future__ import annotations

import sqlite3

from audit_rag.audit.checks import Check, run_checks
from audit_rag.audit.checks_repo import ensure_schema, save_checks
from audit_rag.normalization.records_repo import ensure_schema as ensure_records, load_records


def audit_document(conn: sqlite3.Connection, document_id: str) -> list[Check]:
    """Needs the document's normalised records to be saved first. Caller commits."""
    ensure_records(conn)
    ensure_schema(conn)
    records = load_records(conn, document_id)
    if not records:
        raise ValueError(f"no normalised records for {document_id}; normalise it first")
    checks = run_checks(records)
    save_checks(conn, document_id, checks)
    return checks
