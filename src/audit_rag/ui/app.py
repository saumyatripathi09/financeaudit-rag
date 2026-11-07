"""Streamlit front end. All logic lives in audit_rag.services; this file only displays it.

Run:  streamlit run src/audit_rag/ui/app.py
"""
from __future__ import annotations

import streamlit as st

from audit_rag import config
from audit_rag.db.database import open_database
from audit_rag.reports.data import LIMITATIONS
from audit_rag.services import queries
from audit_rag.services.process import process_file

OUTCOME_TEXT = {
    "audited": "audited",
    "no_data": "nothing to audit",
    "failed": "could not be processed",
}
SEVERITY_MARK = {"high": "HIGH", "medium": "MEDIUM", "low": "low", "none": "info"}


@st.cache_resource
def get_settings():
    getter = getattr(config, "get_settings", None)
    settings = getter() if getter else config.Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def document_label(doc: queries.DocumentRow) -> str:
    return f"{doc.filename}  [{doc.status}]  fail {doc.fails} | flags {doc.flags} | open {doc.open_items}"


def sidebar(settings):
    st.sidebar.header("Documents")
    with open_database(settings.db_path) as conn:
        docs = queries.list_documents(conn)
    if not docs:
        st.sidebar.caption("No documents yet. Add a file in the first tab.")
        return None
    ids = [d.document_id for d in docs]
    wanted = st.session_state.get("selected")
    index = ids.index(wanted) if wanted in ids else 0
    chosen = st.sidebar.selectbox("Open a document", ids, index=index,
                                  format_func=lambda i: document_label(docs[ids.index(i)]))
    st.session_state["selected"] = chosen
    with st.sidebar.expander("Remove this document"):
        st.caption("Deletes the stored file, its extracted rows, records, checks and review notes.")
        if st.checkbox("I understand", key=f"sure-{chosen}") and st.button("Delete", key=f"del-{chosen}"):
            queries.delete_document(settings, chosen)
            st.session_state.pop("selected", None)
            st.rerun()
    return chosen


def tab_add(settings):
    st.write("Accepted: PDF, XLSX, XLS, CSV, PNG, JPG. Files are read on this computer.")
    uploads = st.file_uploader("Choose files", type=["pdf", "xlsx", "xls", "csv", "png", "jpg", "jpeg"],
                               accept_multiple_files=True)
    reprocess = st.checkbox("Read the file again even if it was processed before")
    if st.button("Process", disabled=not uploads, type="primary"):
        last = None
        for upload in uploads:
            with st.spinner(f"Processing {upload.name} (scanned pages can take a few minutes)..."):
                path = queries.save_upload(upload.name, upload.getvalue(), settings)
                result = process_file(path, settings, reprocess=reprocess)
            label = OUTCOME_TEXT.get(result.outcome, result.outcome)
            if result.outcome == "audited":
                last = result.document_id
                st.success(f"{upload.name}: {label}. Checks: {result.check_counts}. "
                           f"Rows backed by a check that ran: {result.verified_share:.1%}.")
            else:
                st.error(f"{upload.name}: {label}. {result.message}")
            for note in result.notes:
                st.caption(f"{upload.name}: {note}")
        if last:
            st.session_state["selected"] = last
            st.info("Open the Findings tab to review the results.")


def finding_card(settings, d: dict):
    page = f"p{d['page']}" if d["page"] else "-"
    title = f"{SEVERITY_MARK.get(d['severity'], d['severity'])} | {d['rule']} | {page} {d['column']} | {d['status']}"
    if d["review_status"] != "open":
        title += f" | {d['review_status']}"
    with st.expander(title):
        st.write(d["message"])
        if d["expected"] is not None or d["actual"] is not None:
            st.write(f"Expected: {d['expected']}   Actual: {d['actual']}   Difference: {d['difference']}")
        if d["evidence"]:
            st.caption("Evidence: " + d["evidence"])
        with st.form(f"review-{d['check_id']}"):
            options = ["open", "confirmed", "dismissed"]
            status = st.radio("Your decision", options, index=options.index(d["review_status"]),
                              horizontal=True)
            note = st.text_input("Note", value=d["review_note"])
            if st.form_submit_button("Save decision"):
                with open_database(settings.db_path) as conn:
                    queries.save_review(conn, d["check_id"], status, note)
                st.rerun()


def tab_findings(settings, document_id):
    if not document_id:
        st.write("Add a file first.")
        return
    with open_database(settings.db_path) as conn:
        report = queries.summary(conn, document_id)
        show_dismissed = st.session_state.get("show_dismissed", False)
        items = queries.findings(conn, document_id, include_dismissed=show_dismissed)
    counts = report.status_counts()
    cols = st.columns(5)
    cols[0].metric("Passed", counts.get("pass", 0) + counts.get("rounding", 0))
    cols[1].metric("Failed", counts.get("fail", 0))
    cols[2].metric("Flags", counts.get("flag", 0))
    cols[3].metric("Not testable", counts.get("not_testable", 0))
    cols[4].metric("Rows checked", f"{report.verified_share:.1%}")
    st.caption(
        f"{report.filename}: {report.numbers_total} numbers; {report.rows_verified} of "
        f"{report.rows_total} rows with a number were used by a check that ran."
        + (f" {report.ocr_numbers} numbers were read by OCR; confirm them against the page." if report.ocr_numbers else ""))
    with st.expander("What this audit does not do"):
        for line in LIMITATIONS:
            st.write("- " + line)
    st.checkbox("Also show dismissed items", key="show_dismissed")
    if not items:
        st.success("Nothing needs attention. This is not proof the statements are correct; see the limits above.")
        return
    st.subheader(f"{len(items)} item(s) for review")
    for item in items:
        finding_card(settings, queries.describe(item))


def tab_reports(settings, document_id):
    if not document_id:
        st.write("Add a file first.")
        return
    if st.button("Prepare report files"):
        with open_database(settings.db_path) as conn:
            st.session_state["reports"] = (document_id, queries.report_files(conn, document_id, settings))
    prepared = st.session_state.get("reports")
    if prepared and prepared[0] == document_id:
        mime = {"csv": "text/csv", "pdf": "application/pdf",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        for kind, (name, data) in prepared[1].items():
            st.download_button(f"Download {kind.upper()}", data=data, file_name=name, mime=mime[kind],
                               key=f"dl-{kind}")
        st.caption("Reports include every check, its evidence and your review decisions.")


def main():
    st.set_page_config(page_title="Financial statement audit", layout="wide")
    st.title("Financial statement audit")
    settings = get_settings()
    document_id = sidebar(settings)
    add, findings, reports = st.tabs(["Add files", "Findings", "Reports"])
    with add:
        tab_add(settings)
    with findings:
        tab_findings(settings, document_id)
    with reports:
        tab_reports(settings, document_id)


main()
