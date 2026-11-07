"""What the UI shows and saves, with no Streamlit in it so it can be tested.

Every function takes an open connection (or settings) and returns plain data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from audit_rag.audit.checks_repo import REVIEW_STATUSES, ensure_schema as ensure_checks, set_review
from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.reports.data import ReportData, ReportItem, build_report, evidence_text
from audit_rag.reports.exporters import export_all

DOCUMENTS_SQL = """
SELECT d.document_id, d.original_filename, d.file_type, d.status, d.registered_at,
  (SELECT COUNT(*) FROM audit_checks c WHERE c.document_id = d.document_id
     AND c.status = 'fail') AS fails,
  (SELECT COUNT(*) FROM audit_checks c WHERE c.document_id = d.document_id
     AND c.status IN ('flag', 'not_testable')) AS flags,
  (SELECT COUNT(*) FROM audit_checks c WHERE c.document_id = d.document_id
     AND c.status IN ('pass', 'rounding')) AS passed,
  (SELECT COUNT(*) FROM audit_checks c WHERE c.document_id = d.document_id
     AND c.status IN ('fail', 'flag', 'not_testable') AND c.review_status = 'open') AS open_items
FROM documents d
ORDER BY d.registered_at DESC, d.document_id
"""


@dataclass(frozen=True)
class DocumentRow:
    document_id: str
    filename: str
    file_type: str
    status: str
    registered_at: str
    fails: int
    flags: int
    passed: int
    open_items: int


def list_documents(conn) -> list[DocumentRow]:
    ensure_checks(conn)
    return [DocumentRow(r["document_id"], r["original_filename"], r["file_type"], r["status"],
                        r["registered_at"], r["fails"], r["flags"], r["passed"], r["open_items"])
            for r in conn.execute(DOCUMENTS_SQL)]


def findings(conn, document_id: str, *, include_dismissed: bool = False) -> list[ReportItem]:
    """Items that need a person's attention, most severe first."""
    report = build_report(conn, document_id)
    if include_dismissed:
        return report.needing_attention() + [
            i for i in report.items if i.review_status == "dismissed"
            and i.check.status in ("fail", "flag", "not_testable")]
    return report.needing_attention()


def describe(item: ReportItem) -> dict:
    """One finding as plain values for display."""
    c = item.check
    return {
        "check_id": item.check_id, "rule": c.rule_id, "status": c.status, "severity": c.severity,
        "page": None if c.page_index is None else c.page_index + 1, "column": c.column,
        "message": c.message, "expected": c.expected, "actual": c.actual,
        "difference": c.difference, "evidence": evidence_text(c),
        "review_status": item.review_status, "review_note": item.review_note or "",
    }


def save_review(conn, check_id: int, status: str, note: str | None = None) -> None:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"review status must be one of {REVIEW_STATUSES}")
    set_review(conn, check_id, status, (note or "").strip() or None)


def summary(conn, document_id: str) -> ReportData:
    return build_report(conn, document_id)


def report_files(conn, document_id: str, settings: Settings) -> dict[str, tuple[str, bytes]]:
    """Write the three report files and return {kind: (file name, bytes)} for download."""
    report = build_report(conn, document_id)
    paths = export_all(report, settings.data_dir / "reports",
                       stem=f"{Path(report.filename).stem}_{document_id[4:12]}")
    return {kind: (path.name, path.read_bytes()) for kind, path in paths.items()}


def save_upload(name: str, data: bytes, settings: Settings) -> Path:
    """Write an uploaded file under the data folder and return its path.

    Only the file name is used, never any folder part, and an existing file is never
    overwritten (a different upload with the same name gets a numbered name).
    """
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "upload"
    folder = settings.data_dir / "incoming"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / base
    n = 1
    while target.exists():
        if target.read_bytes() == data:
            return target
        target = folder / f"{Path(base).stem}_{n}{Path(base).suffix}"
        n += 1
    target.write_bytes(data)
    return target


def delete_document(settings: Settings, document_id: str) -> None:
    """Remove a document and everything derived from it (rows, records, checks cascade)."""
    import shutil

    with open_database(settings.db_path) as conn:
        conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
    shutil.rmtree(settings.uploads_dir / document_id, ignore_errors=True)
