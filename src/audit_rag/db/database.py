"""SQLite access for the audit RAG project.

One file on disk holds document records, extracted rows, page and sheet outcomes
(later: findings and review status). Use open_database() so every block either
fully saves or fully rolls back, and the connection is always closed.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id        TEXT PRIMARY KEY,
    original_filename  TEXT NOT NULL,
    stored_path        TEXT NOT NULL,
    sha256             TEXT NOT NULL UNIQUE,
    size_bytes         INTEGER NOT NULL CHECK (size_bytes >= 0),
    file_type          TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'registered',
    registered_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extracted_rows (
    row_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id    TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    row_order      INTEGER NOT NULL,
    label          TEXT NOT NULL,
    method         TEXT NOT NULL,
    location_json  TEXT NOT NULL,
    warnings_json  TEXT NOT NULL,
    extracted_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rows_document ON extracted_rows(document_id, row_order);

CREATE TABLE IF NOT EXISTS extracted_cells (
    cell_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id         INTEGER NOT NULL REFERENCES extracted_rows(row_id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    raw_text       TEXT NOT NULL,
    column_label   TEXT,
    confidence     REAL,
    location_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cells_row ON extracted_cells(row_id, position);

CREATE TABLE IF NOT EXISTS page_outcomes (
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    page_index   INTEGER NOT NULL,
    outcome      TEXT NOT NULL
                 CHECK (outcome IN ('table', 'no_table', 'ocr_candidate', 'ocr_unparsed', 'blank',
                                 'error')),
    detail       TEXT,
    PRIMARY KEY (document_id, page_index)
);

CREATE TABLE IF NOT EXISTS sheet_outcomes (
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    sheet_order  INTEGER NOT NULL,
    sheet_name   TEXT NOT NULL,
    outcome      TEXT NOT NULL CHECK (outcome IN ('table', 'no_table', 'ocr_unparsed', 'blank', 'error')),
    detail       TEXT,
    PRIMARY KEY (document_id, sheet_name)
);

CREATE TABLE IF NOT EXISTS document_notes (
    document_id  TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    note_order   INTEGER NOT NULL,
    note         TEXT NOT NULL,
    PRIMARY KEY (document_id, note_order)
);
"""


@contextmanager
def open_database(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the database, create missing tables, and yield a connection.

    On normal exit the changes are committed. If the block raises, they are rolled
    back and the error is re-raised, so a half-finished write never persists.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # rows can be read by column name
    conn.execute("PRAGMA foreign_keys = ON")  # without this, cascades do not happen
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
