import sqlite3
from decimal import Decimal
from types import SimpleNamespace as NS

from audit_rag.models.extraction import (ExtractedCell, ExtractedRow, ExtractionMethod,
                                         SourceLocation)
from audit_rag.normalization.scaling import scale_rows
from audit_rag.normalization.records import build_records
from audit_rag.normalization.records_repo import ensure_schema, save_records, load_records

MILLIONS = NS(status="ok", multiplier=Decimal(1000000), mentions_exceptions=False)


def row(label, *texts, page=3, conf=None, warnings=()):
    loc = SourceLocation(page_index=page, row_index=0)
    return ExtractedRow(
        document_id="d1", filename="a.pdf", label=label, location=loc,
        method=ExtractionMethod.OCR if conf is not None else ExtractionMethod.PDF_TEXT,
        warnings=list(warnings),
        cells=[ExtractedCell(raw_text=t, location=loc, column_label=str(2022 - i), confidence=conf)
               for i, t in enumerate(texts)],
    )


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, original_filename TEXT, status TEXT)")
    conn.execute("INSERT INTO documents VALUES ('d1','a.pdf','ingested')")
    ensure_schema(conn)
    return conn


def test_records_keep_original_and_scale():
    rows = [row("Total assets", "1,615.9", "1,500.0", conf=0.91, warnings=["w"]),
            row("Basic earnings per share", "2.10")]
    recs = build_records(scale_rows(rows, MILLIONS), {3: "balance_sheet"})
    assert len(recs) == 3
    a = recs[0]
    assert (a.raw_text, a.label_canonical, a.statement_type) == ("1,615.9", "total_assets", "balance_sheet")
    assert a.scale_status == "scaled" and a.scaled_value == Decimal("1615900000.0")
    assert a.confidence == 0.91 and a.row_warnings == ("w",) and a.method == "ocr"
    assert recs[2].scale_status == "needs_review" and recs[2].scaled_value is None
    assert recs[2].label_canonical is None


def test_unknown_statement_when_page_not_mapped():
    recs = build_records(scale_rows([row("Revenue", "5")], MILLIONS))
    assert recs[0].statement_type == "unknown"


def test_save_load_round_trip_exact_decimals_and_replace():
    conn = make_db()
    recs = build_records(scale_rows([row("Total assets", "1,615.9", "-"), row("Net sales", "12")], MILLIONS))
    with conn:
        save_records(conn, "d1", recs)
    got = load_records(conn, "d1")
    assert got == recs
    with conn:  # resaving replaces, never duplicates
        save_records(conn, "d1", recs[:1])
    assert len(load_records(conn, "d1")) == 1


def test_records_removed_with_document():
    conn = make_db()
    with conn:
        save_records(conn, "d1", build_records(scale_rows([row("Revenue", "5")], MILLIONS)))
        conn.execute("DELETE FROM documents WHERE document_id='d1'")
    assert load_records(conn, "d1") == []
