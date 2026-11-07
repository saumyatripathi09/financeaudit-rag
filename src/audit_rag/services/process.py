"""One file through the whole pipeline: register, ingest, normalise, audit, save, report.

Each stage is a parameter so tests can substitute small fakes. Stage errors become an
outcome, never a crash. Cross-document reconciliation is separate (needs entity grouping).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from audit_rag.audit.runner import audit_document
from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.ingestion.registry import RegistrationError, register_document
from audit_rag.normalization.pipeline import normalize_document
from audit_rag.reports.data import build_report
from audit_rag.reports.exporters import export_all

log = logging.getLogger(__name__)


class _NoData(Exception):
    pass


@dataclass(frozen=True)
class ProcessResult:
    filename: str
    document_id: str | None
    outcome: str  # audited, no_data, failed
    message: str = ""
    reused_extraction: bool = False
    rows: int = 0
    records: int = 0
    check_counts: dict[str, int] = field(default_factory=dict)
    verified_share: float = 0.0
    notes: list[str] = field(default_factory=list)
    report_files: dict[str, Path] = field(default_factory=dict)


def _no_data_message(conn, document_id: str) -> str:
    """Say why nothing was found, using what the reader recorded for each sheet."""
    sheets = conn.execute(
        "SELECT sheet_name FROM sheet_outcomes WHERE document_id = ? AND outcome = 'no_table' "
        "ORDER BY sheet_order", (document_id,)).fetchall()
    if sheets:
        names = ", ".join(repr(s["sheet_name"]) for s in sheets)
        return (f"no table was found in sheet {names}: a table needs a heading row with two or more "
                "year columns (for example 2022 and 2021)")
    return "no numbers were found in this file"


def _default_ingest(document, settings):
    from audit_rag.ingestion.orchestrator import ingest_document  # heavy imports (OCR) only when needed

    return ingest_document(document, settings)


def process_file(
    source: Path,
    settings: Settings,
    *,
    reprocess: bool = False,
    make_reports: bool = True,
    register=register_document,
    ingest=None,
    normalize=normalize_document,
    audit=audit_document,
    export=export_all,
) -> ProcessResult:
    source = Path(source)
    ingest = ingest or _default_ingest
    try:
        document = register(source, settings)
    except RegistrationError as exc:
        return ProcessResult(source.name, None, "failed", str(exc))

    doc_id = document.document_id
    notes: list[str] = []
    reused = bool(document.is_duplicate and document.status in ("ingested", "audited") and not reprocess)
    rows = 0
    if reused:
        notes.append("this file was processed before; stored extraction reused")
    else:
        summary = ingest(document, settings)
        if summary.status != "ingested":
            return ProcessResult(source.name, doc_id, "failed", summary.message or "ingestion failed",
                                 notes=list(summary.notes))
        notes.extend(summary.notes)
        rows = summary.row_count

    try:
        with open_database(settings.db_path) as conn:
            if reused:
                rows = conn.execute("SELECT COUNT(*) FROM extracted_rows WHERE document_id = ?",
                                    (doc_id,)).fetchone()[0]
            result = normalize(conn, doc_id, base_dir=settings.data_dir)
            notes.extend(n for n in result.notes if n not in notes)
            if not any(r.kind == "number" for r in result.records):
                # nothing to audit: leave no half-saved records behind
                raise _NoData(_no_data_message(conn, doc_id))
            checks = audit(conn, doc_id)
            conn.execute("UPDATE documents SET status = 'audited' WHERE document_id = ?", (doc_id,))
            report = build_report(conn, doc_id)
    except _NoData as exc:
        return ProcessResult(source.name, doc_id, "no_data", str(exc),
                             reused, rows, notes=notes)
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the caller; all changes rolled back
        log.exception("processing %s failed", source.name)
        return ProcessResult(source.name, doc_id, "failed", f"{type(exc).__name__}: {exc}",
                             reused, rows, notes=notes)

    if report.rows_verified == 0:
        notes.append("no check could run on this file: none of its rows were recognised as the "
                     "totals a balance sheet or income statement check needs, so nothing was verified")
    files: dict[str, Path] = {}
    if make_reports:
        try:
            files = export(report, settings.data_dir / "reports", stem=f"{Path(source.name).stem}_{doc_id[4:12]}")
        except Exception as exc:  # noqa: BLE001 - results are saved; only the export failed
            log.exception("report export failed for %s", source.name)
            notes.append(f"report files could not be written: {exc}")
    return ProcessResult(source.name, doc_id, "audited", "", reused, rows, len(result.records),
                         dict(Counter(c.status for c in checks)), report.verified_share, notes, files)
