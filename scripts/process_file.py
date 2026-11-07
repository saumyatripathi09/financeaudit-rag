"""Run one file through the whole pipeline: python scripts/process_file.py <path> [--reprocess]"""
import sys
from pathlib import Path

from audit_rag import config
from audit_rag.services.process import process_file


def load_settings():
    getter = getattr(config, "get_settings", None)
    return getter() if getter else config.Settings()


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit("usage: python scripts/process_file.py <file> [--reprocess]")
    settings = load_settings()
    print(f"data folder: {settings.data_dir}")
    r = process_file(Path(args[0]), settings, reprocess="--reprocess" in argv)
    print(f"{r.filename}: {r.outcome}" + (f" - {r.message}" if r.message else ""))
    if r.outcome == "audited":
        print(f"  rows {r.rows}{' (reused)' if r.reused_extraction else ''}, records {r.records}")
        print(f"  checks {r.check_counts}")
        print(f"  rows backed by a check that ran: {r.verified_share:.1%}")
        for kind, path in r.report_files.items():
            print(f"  {kind}: {path}")
    for note in r.notes:
        print(f"  note: {note}")


main(sys.argv[1:])
