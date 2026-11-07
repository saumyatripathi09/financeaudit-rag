"""Write a report as CSV, Excel or PDF."""
from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape

from audit_rag.reports.data import LIMITATIONS, ReportData, ReportItem, evidence_text

COLUMNS = ["check_id", "rule", "status", "severity", "page", "column", "expected", "actual",
           "difference", "message", "evidence", "review_status", "review_note"]


def _row(i: ReportItem) -> list:
    c = i.check
    return [i.check_id, c.rule_id, c.status, c.severity,
            "" if c.page_index is None else c.page_index + 1, c.column,
            "" if c.expected is None else str(c.expected),
            "" if c.actual is None else str(c.actual),
            "" if c.difference is None else str(c.difference),
            c.message, evidence_text(c), i.review_status, i.review_note or ""]


def _safe(cell):
    """Stop a spreadsheet program from running text that starts with a formula character."""
    if isinstance(cell, str) and cell[:1] in ("=", "+", "-", "@"):
        return "'" + cell
    return cell


def summary_lines(report: ReportData) -> list[tuple[str, str]]:
    counts = report.status_counts()
    return [
        ("Document", report.filename),
        ("Document id", report.document_id),
        ("Generated (UTC)", report.generated_at.strftime("%Y-%m-%d %H:%M")),
        ("Numbers extracted", str(report.numbers_total)),
        ("Numbers read by OCR", str(report.ocr_numbers)),
        ("Rows with a number", str(report.rows_total)),
        ("Rows used by a check that ran",
         f"{report.rows_verified} ({report.verified_share:.1%})"),
        ("Checks passed", str(counts.get("pass", 0) + counts.get("rounding", 0))),
        ("Checks failed", str(counts.get("fail", 0))),
        ("Flags (data quality)", str(counts.get("flag", 0))),
        ("Not testable", str(counts.get("not_testable", 0))),
    ]


def to_csv(report: ReportData, path: Path) -> Path:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for item in report.items:
            writer.writerow([_safe(x) for x in _row(item)])
    return path


def to_excel(report: ReportData, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for label, value in summary_lines(report):
        ws.append([label, _safe(value)])
    ws.append([])
    ws.append(["What this audit did and did not verify"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for text in LIMITATIONS:
        ws.append([text])
    ws.append([])
    ws.append(["Rule", "pass", "rounding", "fail", "flag", "not_testable"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for rule, counts in report.rule_counts().items():
        ws.append([rule] + [counts.get(s, 0) for s in ("pass", "rounding", "fail", "flag", "not_testable")])
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 60

    for title, items in (("Needs attention", report.needing_attention()), ("All checks", report.items)):
        sheet = wb.create_sheet(title)
        sheet.append(COLUMNS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for item in items:
            sheet.append([_safe(x) for x in _row(item)])
        for column, width in zip("ABCDEFGHIJKLM", (9, 28, 13, 10, 6, 9, 14, 14, 12, 60, 60, 12, 30)):
            sheet.column_dimensions[column].width = width
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.freeze_panes = "A2"
    wb.save(path)
    return path


def to_pdf(report: ReportData, path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=12.5)

    def p(text, style=body):
        return Paragraph(escape(str(text)), style)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Audit report: {report.filename}", author="Local audit RAG",
    )
    story = [Paragraph(escape(f"Audit report: {report.filename}"), styles["Title"])]
    rows = [[p(k, small), p(v, small)] for k, v in summary_lines(report)]
    table = Table(rows, colWidths=[55 * mm, 125 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [table, Spacer(1, 6 * mm), Paragraph("What this audit did and did not verify", styles["Heading2"])]
    story += [p(f"- {text}") for text in LIMITATIONS]

    attention = report.needing_attention()
    story += [Spacer(1, 4 * mm), Paragraph(f"Needs attention ({len(attention)})", styles["Heading2"])]
    if not attention:
        story.append(p("Nothing is waiting for review."))
    else:
        data = [[p(h, small) for h in ("Status", "Rule / page", "Figures", "What to check")]]
        for i in attention:
            c = i.check
            where = c.rule_id + ("" if c.page_index is None else f" p{c.page_index + 1}") + (f" {c.column}" if c.column else "")
            figures = ("" if c.expected is None else f"expected {c.expected}\nactual {c.actual}\ndifference {c.difference}")
            detail = c.message + ("\nEvidence: " + evidence_text(c) if c.evidence else "")
            if i.review_status != "open":
                detail += f"\nReview: {i.review_status} {i.review_note or ''}"
            data.append([p(f"{c.status}\n{c.severity}", small), p(where, small),
                         p(figures, small), p(detail, small)])
        t = Table(data, colWidths=[20 * mm, 38 * mm, 38 * mm, 84 * mm], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)

    rule_heading = [Spacer(1, 4 * mm), Paragraph("Checks by rule", styles["Heading2"])]
    head = [p(h, small) for h in ("Rule", "pass", "rounding", "fail", "flag", "not testable")]
    data = [head] + [[p(rule, small)] + [p(counts.get(s, 0), small) for s in
                      ("pass", "rounding", "fail", "flag", "not_testable")]
                     for rule, counts in report.rule_counts().items()]
    t = Table(data, colWidths=[70 * mm] + [22 * mm] * 5)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story.append(KeepTogether(rule_heading + [t]))  # heading and table never split across pages
    doc.build(story)
    return path


def export_all(report: ReportData, folder: Path, stem: str | None = None) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    stem = stem or Path(report.filename).stem
    stem = "".join(ch if ch.isalnum() or ch in "-_. " else "_" for ch in stem).strip(" .") or "report"
    return {
        "csv": to_csv(report, folder / f"{stem}_audit.csv"),
        "xlsx": to_excel(report, folder / f"{stem}_audit.xlsx"),
        "pdf": to_pdf(report, folder / f"{stem}_audit.pdf"),
    }
