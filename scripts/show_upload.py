import sqlite3, sys
from audit_rag import config

getter = getattr(config, "get_settings", None)
settings = getter() if getter else config.Settings()
conn = sqlite3.connect(settings.db_path)
conn.row_factory = sqlite3.Row
doc = conn.execute("SELECT document_id FROM documents WHERE original_filename LIKE ? "
                   "ORDER BY registered_at DESC", (sys.argv[1] + "%",)).fetchone()[0]
print("document:", doc)
print("--- extracted rows (label | cell texts | column labels)")
for r in conn.execute("SELECT row_id, label FROM extracted_rows WHERE document_id=? ORDER BY row_order LIMIT 20", (doc,)):
    cells = conn.execute("SELECT raw_text, column_label FROM extracted_cells WHERE row_id=? ORDER BY position",
                         (r["row_id"],)).fetchall()
    print(f"{r['label'][:44]!r:48} {[c['raw_text'] for c in cells]} {[c['column_label'] for c in cells]}")
print("--- normalized records")
for r in conn.execute("SELECT kind, raw_text, displayed_value, scale_status, reasons FROM normalized_records "
                      "WHERE document_id=? ORDER BY row_order LIMIT 12", (doc,)):
    print(dict(r))
