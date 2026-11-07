"""Normalise one stored document and verify the saved records.

  python scripts\normalize_db.py <db_path>              list stored documents
  python scripts\normalize_db.py <db_path> <document_id>   normalise that document
"""
import sys
from pathlib import Path

from audit_rag.db.database import open_database
from audit_rag.normalization.pipeline import normalize_document
from audit_rag.normalization.records_repo import load_records

if len(sys.argv) < 2:
    raise SystemExit(__doc__)
db_path = Path(sys.argv[1])
with open_database(db_path) as conn:
    if len(sys.argv) == 2:
        for d in conn.execute(
            "SELECT document_id, original_filename, file_type, status FROM documents"
        ):
            print(d["document_id"], d["original_filename"], d["file_type"], d["status"])
        raise SystemExit(0)
    result = normalize_document(conn, sys.argv[2], base_dir=Path.cwd())
    print("records:", len(result.records), "by status:", result.status_counts())
    print("statement by page:", {p + 1: g.statement for p, g in result.statement_by_page.items()})
    for note in result.notes:
        print("note:", note)
    saved = load_records(conn, sys.argv[2])
    print("saved and reloaded identical:", saved == result.records)
