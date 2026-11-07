from dataclasses import dataclass, field

import pytest

from audit_rag.config import Settings
from audit_rag.db.database import open_database
from audit_rag.db.repository import save_grid_result
from audit_rag.ingestion.orchestrator import IngestSummary
from audit_rag.models.extraction import ExtractedCell, ExtractedRow, ExtractionMethod, SourceLocation
from audit_rag.services.process import process_file

CSV = "item,2022\nCash,5\nTotal assets,105\nTotal liabilities,60\nTotal equity,40\nTotal liabilities and equity,100\n"


@dataclass
class Grid:
    document_id: str
    rows: list
    outcomes: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def make_rows(doc_id, table):
    out = []
    for i, (label, value) in enumerate(table):
        loc = SourceLocation(sheet_name="S", row_index=i, cell_ref=f"A{i + 1}")
        cell = ExtractedCell(raw_text=value, location=loc, column_label="2022")
        out.append(ExtractedRow(document_id=doc_id, filename="a.csv", label=label, cells=[cell],
                                location=loc, method=ExtractionMethod.CSV))
    return out


BALANCE = [("Cash", "5"), ("Total assets", "105"), ("Total liabilities", "60"),
           ("Total equity", "40"), ("Total liabilities and equity", "100")]


def fake_ingest(table=BALANCE, calls=None, outcomes=None):
    def run(document, settings):
        if calls is not None:
            calls.append(document.document_id)
        with open_database(settings.db_path) as conn:
            save_grid_result(conn, Grid(document.document_id, make_rows(document.document_id, table),
                                        outcomes or []))
        return IngestSummary(document.document_id, "ingested", len(table))
    return run


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir()
    src = tmp_path / "bs.csv"
    src.write_text(CSV)
    return settings, src


def count(settings, table):
    with open_database(settings.db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def status(settings, doc_id):
    with open_database(settings.db_path) as conn:
        return conn.execute("SELECT status FROM documents WHERE document_id=?", (doc_id,)).fetchone()[0]


def test_file_goes_through_every_stage(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest())
    assert r.outcome == "audited" and r.document_id.startswith("doc-")
    assert r.rows == 5 and r.records == 5 and sum(r.check_counts.values()) > 0
    assert status(settings, r.document_id) == "audited"
    assert count(settings, "audit_checks") == sum(r.check_counts.values())
    assert set(r.report_files) == {"csv", "xlsx", "pdf"}
    assert all(p.exists() and p.stat().st_size > 0 for p in r.report_files.values())


def test_same_file_again_reuses_extraction_and_does_not_double_save(env):
    settings, src = env
    calls = []
    first = process_file(src, settings, ingest=fake_ingest(calls=calls))
    second = process_file(src, settings, ingest=fake_ingest(calls=calls))
    assert second.reused_extraction and len(calls) == 1
    assert second.check_counts == first.check_counts
    assert count(settings, "audit_checks") == sum(first.check_counts.values())
    assert count(settings, "documents") == 1


def test_reprocess_reads_the_file_again(env):
    settings, src = env
    calls = []
    process_file(src, settings, ingest=fake_ingest(calls=calls))
    r = process_file(src, settings, ingest=fake_ingest(calls=calls), reprocess=True)
    assert len(calls) == 2 and not r.reused_extraction and r.outcome == "audited"


def test_failed_ingestion_is_reported_and_nothing_is_audited(env):
    settings, src = env
    def failing(document, settings):
        return IngestSummary(document.document_id, "failed", 0, message="cannot read it")
    r = process_file(src, settings, ingest=failing)
    assert (r.outcome, r.message) == ("failed", "cannot read it")
    assert status(settings, r.document_id) == "registered"


def test_rejected_file_is_a_failed_result_not_a_crash(env, tmp_path):
    settings, _ = env
    bad = tmp_path / "x.txt"
    bad.write_text("hello")
    r = process_file(bad, settings, ingest=fake_ingest())
    assert r.outcome == "failed" and "unsupported" in r.message and r.document_id is None


def test_file_without_numbers_is_no_data_and_leaves_no_records(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest([("Notes", "n/a"), ("Remarks", "see text")]))
    assert r.outcome == "no_data" and r.records == 0
    assert count(settings, "normalized_records") == 0
    assert status(settings, r.document_id) == "ingested"


def test_audit_error_rolls_back_the_records(env):
    settings, src = env
    def boom(conn, doc_id):
        raise RuntimeError("rule exploded")
    r = process_file(src, settings, ingest=fake_ingest(), audit=boom)
    assert r.outcome == "failed" and "rule exploded" in r.message
    assert count(settings, "normalized_records") == 0
    assert status(settings, r.document_id) == "ingested"


def test_export_error_keeps_results_and_says_so(env):
    settings, src = env
    def broken(report, folder, stem=None):
        raise OSError("disk full")
    r = process_file(src, settings, ingest=fake_ingest(), export=broken)
    assert r.outcome == "audited" and r.report_files == {}
    assert any("report files could not be written" in n for n in r.notes)
    assert status(settings, r.document_id) == "audited"


def test_make_reports_false_writes_nothing(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest(), make_reports=False)
    assert r.report_files == {} and not (settings.data_dir / "reports").exists()


def test_pdf_is_opened_through_the_data_folder_not_the_working_folder(env, monkeypatch, tmp_path):
    import sys
    import types

    from audit_rag.ingestion.registry import register_document
    from audit_rag.normalization.pipeline import normalize_document

    settings, _ = env
    pdf = tmp_path / "bs.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    document = register_document(pdf, settings)
    fake_ingest(BALANCE)(document, settings)

    opened = []

    class Page:
        def get_text(self):
            return "Consolidated Balance Sheets (in millions)"

    class Pdf:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __getitem__(self, i):
            return Page()

    fake = types.ModuleType("pymupdf")
    fake.open = lambda path: opened.append(path) or Pdf()
    monkeypatch.setitem(sys.modules, "pymupdf", fake)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    decoy = elsewhere / document.stored_path  # a different file at the same relative path
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes(b"%PDF-1.4 decoy")
    monkeypatch.chdir(elsewhere)
    with open_database(settings.db_path) as conn:
        normalize_document(conn, document.document_id, base_dir=settings.data_dir)
    assert opened == [settings.data_dir / document.stored_path]


def test_no_data_explains_a_sheet_the_reader_found_no_table_in(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest([], outcomes=[("Balance Sheet", "no_table", None)]))
    assert r.outcome == "no_data"
    assert "Balance Sheet" in r.message and "two or more year columns" in r.message


def test_no_data_without_sheet_information_keeps_the_plain_message(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest([("Notes", "n/a")]))
    assert r.outcome == "no_data" and r.message == "no numbers were found in this file"


def test_audited_file_where_no_check_could_run_says_so(env):
    settings, src = env
    r = process_file(src, settings, ingest=fake_ingest([("Account 1000 Cash", "5"), ("Account 2000 Payables", "3")]),
                     make_reports=False)
    assert r.outcome == "audited" and r.verified_share == 0.0
    assert any("no check could run" in n for n in r.notes)
