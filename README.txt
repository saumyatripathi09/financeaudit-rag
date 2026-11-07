Phase 8/9 package: workflow service, CSV/Excel/PDF export, Streamlit app, tests.

1. Extract this zip into E:\audit_rag (it only adds new files).
2. From E:\audit_rag with the virtual environment active:
       python apply_reconcile_patch.py
       python -m pytest -q
       python scripts\smoke_workflow.py
3. If the smoke test shows status='audited':
       streamlit run src\audit_rag\ui\app.py
