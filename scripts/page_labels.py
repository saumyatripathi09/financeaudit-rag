import sqlite3, sys
from audit_rag import config

getter = getattr(config, "get_settings", None)
settings = getter() if getter else config.Settings()
conn = sqlite3.connect(settings.db_path)
conn.row_factory = sqlite3.Row
doc = conn.execute("SELECT document_id FROM documents WHERE original_filename LIKE ?",
                   (sys.argv[1] + "%",)).fetchone()[0]
page = int(sys.argv[2]) - 1
seen = set()
for r in conn.execute("SELECT statement_type, label, label_canonical FROM normalized_records "
                      "WHERE document_id=? AND page_index=? AND kind='number' ORDER BY row_order",
                      (doc, page)):
    key = (r["label"], r["label_canonical"])
    if key not in seen:
        seen.add(key)
        print(f"{r['statement_type']:18s} {str(r['label_canonical']):20s} {r['label'][:70]}")
