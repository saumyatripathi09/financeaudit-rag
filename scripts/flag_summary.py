import sqlite3, sys
from collections import Counter

from audit_rag import config

getter = getattr(config, "get_settings", None)
settings = getter() if getter else config.Settings()
print("database:", settings.db_path)
conn = sqlite3.connect(settings.db_path)
conn.row_factory = sqlite3.Row
doc = conn.execute("SELECT document_id FROM documents WHERE original_filename LIKE ?",
                   (sys.argv[1] + "%",)).fetchone()[0]
rows = conn.execute("SELECT rule_id, status, severity, page_index, message FROM audit_checks "
                    "WHERE document_id=? AND status IN ('flag','not_testable','fail')", (doc,)).fetchall()
print(doc, len(rows))
for (rule, status, sev), n in sorted(Counter((r[0], r[1], r[2]) for r in rows).items()):
    print(f"{n:4d}  {rule:28s} {status:13s} {sev}")
print()
seen = set()
for r in rows:
    key = (r["rule_id"], r["message"][:60])
    if key not in seen and len(seen) < 12:
        seen.add(key)
        print(f"p{(r['page_index'] or 0) + 1:<4d} {r['rule_id']}: {r['message'][:110]}")
