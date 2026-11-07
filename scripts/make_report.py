"""Write the CSV, Excel and PDF audit report for one stored document.

  python scripts/make_report.py <db_path> <document_id> [output_folder]
"""
import sys
from pathlib import Path

from audit_rag.db.database import open_database
from audit_rag.reports.data import build_report
from audit_rag.reports.exporters import export_all

if len(sys.argv) < 3:
    raise SystemExit(__doc__)
folder = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("reports")
with open_database(Path(sys.argv[1])) as conn:
    report = build_report(conn, sys.argv[2])
files = export_all(report, folder)
print("checks:", report.status_counts(), "| rows verified:", f"{report.verified_share:.1%}")
for kind, path in files.items():
    print(f"{kind}: {path}")
