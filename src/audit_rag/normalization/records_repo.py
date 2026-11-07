"""Store and load normalised records in SQLite (replace-on-resave per document)."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

from audit_rag.normalization.records import NormalizedRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS normalized_records (
    record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    row_order INTEGER NOT NULL,
    cell_position INTEGER NOT NULL,
    page_index INTEGER,
    statement_type TEXT NOT NULL,
    label TEXT NOT NULL,
    label_canonical TEXT,
    column_label TEXT,
    raw_text TEXT NOT NULL,
    kind TEXT NOT NULL,
    displayed_value TEXT,
    scaled_value TEXT,
    scale_status TEXT NOT NULL,
    multiplier TEXT,
    reasons_json TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL,
    row_warnings_json TEXT NOT NULL,
    sheet_name TEXT,
    cell_ref TEXT,
    UNIQUE (document_id, row_order, cell_position)
);
CREATE INDEX IF NOT EXISTS idx_records_doc ON normalized_records(document_id);
CREATE INDEX IF NOT EXISTS idx_records_canon ON normalized_records(document_id, label_canonical);
"""
COLUMNS = (
    "document_id, row_order, cell_position, page_index, statement_type, label, label_canonical, "
    "column_label, raw_text, kind, displayed_value, scaled_value, scale_status, multiplier, "
    "reasons_json, method, confidence, row_warnings_json, sheet_name, cell_ref"
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)  # idempotent; safe to call at every start


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)  # text keeps every digit exactly


def _undec(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def save_records(
    conn: sqlite3.Connection, document_id: str, records: list[NormalizedRecord]
) -> None:
    """Replace all stored records of the document. Caller controls the transaction."""
    conn.execute("DELETE FROM normalized_records WHERE document_id = ?", (document_id,))
    conn.executemany(
        f"INSERT INTO normalized_records ({COLUMNS}) VALUES ({','.join('?' * 20)})",
        [
            (
                r.document_id, r.row_order, r.cell_position, r.page_index, r.statement_type,
                r.label, r.label_canonical, r.column_label, r.raw_text, r.kind,
                _dec(r.displayed_value), _dec(r.scaled_value), r.scale_status,
                _dec(r.multiplier), json.dumps(list(r.reasons)), r.method, r.confidence,
                json.dumps(list(r.row_warnings)), r.sheet_name, r.cell_ref,
            )
            for r in records
        ],
    )


def load_records(conn: sqlite3.Connection, document_id: str) -> list[NormalizedRecord]:
    out = []
    for c in conn.execute(
        "SELECT * FROM normalized_records WHERE document_id = ? "
        "ORDER BY row_order, cell_position", (document_id,)
    ):
        out.append(
            NormalizedRecord(
                document_id=c["document_id"], row_order=c["row_order"],
                cell_position=c["cell_position"], page_index=c["page_index"],
                statement_type=c["statement_type"], label=c["label"],
                label_canonical=c["label_canonical"], column_label=c["column_label"],
                raw_text=c["raw_text"], kind=c["kind"],
                displayed_value=_undec(c["displayed_value"]),
                scaled_value=_undec(c["scaled_value"]), scale_status=c["scale_status"],
                multiplier=_undec(c["multiplier"]), reasons=tuple(json.loads(c["reasons_json"])),
                method=c["method"], confidence=c["confidence"],
                row_warnings=tuple(json.loads(c["row_warnings_json"])),
                sheet_name=c["sheet_name"], cell_ref=c["cell_ref"],
            )
        )
    return out
