import sqlite3, sys
from audit_rag import config

getter = getattr(config, "get_settings", None)
settings = getter() if getter else config.Settings()
conn = sqlite3.connect(settings.db_path)
conn.row_factory = sqlite3.Row
doc = conn.execute("SELECT document_id FROM documents WHERE original_filename LIKE ?",
                   (sys.argv[1] + "%",)).fetchone()[0]
for r in conn.execute("SELECT rule_id, page_index, column_label, message FROM audit_checks "
                      "WHERE document_id=? AND status='not_testable' ORDER BY page_index", (doc,)):
    print(f"p{r['page_index'] + 1:<4d} {r['rule_id']:18s} {r['column_label'] or '-':8s} {r['message']}")
