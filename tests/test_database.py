"""Tests for the SQLite layer."""
import sqlite3

import pytest

from audit_rag.db.database import open_database

INSERT = (
    "INSERT INTO documents (document_id, original_filename, stored_path, sha256, "
    "size_bytes, file_type, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def _row(doc_id="doc-1", sha="a" * 64, size=10):
    return (doc_id, "a.pdf", "uploads/doc-1/a.pdf", sha, size, "pdf", "2026-10-24T00:00:00+00:00")


def test_opening_creates_the_documents_table(tmp_path):
    with open_database(tmp_path / "t.db") as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "documents" in names


def test_data_survives_closing_and_reopening(tmp_path):
    db = tmp_path / "t.db"
    with open_database(db) as conn:
        conn.execute(INSERT, _row())
    with open_database(db) as conn:  # a fresh connection, like an application restart
        row = conn.execute("SELECT * FROM documents WHERE document_id = 'doc-1'").fetchone()
    assert row["original_filename"] == "a.pdf"
    assert row["status"] == "registered"


def test_a_failed_block_is_rolled_back(tmp_path):
    db = tmp_path / "t.db"
    with pytest.raises(RuntimeError):
        with open_database(db) as conn:
            conn.execute(INSERT, _row())
            raise RuntimeError("something went wrong half way")
    with open_database(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_the_same_file_hash_cannot_be_registered_twice(tmp_path):
    with open_database(tmp_path / "t.db") as conn:
        conn.execute(INSERT, _row("doc-1", sha="b" * 64))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT, _row("doc-2", sha="b" * 64))


def test_a_negative_file_size_is_rejected(tmp_path):
    with open_database(tmp_path / "t.db") as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(INSERT, _row(size=-1))
