"""Environment check: confirms each local component works.

Run with:  python scripts/check_env.py
Exit code is 0 only if every check passes.
"""
from __future__ import annotations

import sys
from typing import Callable


def run_check(name: str, check: Callable[[], str]) -> bool:
    """Run one check and print PASS or FAIL. Never lets an error escape."""
    try:
        detail = check()
        print(f"[PASS] {name}: {detail}")
        return True
    except Exception as exc:  # deliberate: report every failure, don't crash
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return False


def check_python() -> str:
    version = sys.version_info
    if (version.major, version.minor) != (3, 12):
        raise RuntimeError(f"expected Python 3.12, found {sys.version.split()[0]}")
    return sys.version.split()[0]


def check_config() -> str:
    from audit_rag.config import get_settings

    settings = get_settings()
    settings.ensure_directories()
    return f"data dir {settings.data_dir}"


def check_tesseract() -> str:
    import pytesseract

    from audit_rag.config import get_settings

    cmd = get_settings().tesseract_cmd
    if cmd is None:
        raise RuntimeError("TESSERACT_CMD is not set in .env")
    pytesseract.pytesseract.tesseract_cmd = str(cmd)
    return f"tesseract {pytesseract.get_tesseract_version()}"


def check_sqlite() -> str:
    import sqlite3
    import tempfile
    from pathlib import Path

    # Test FTS5 (full-text search), which our keyword retrieval will rely on.
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "test.db")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
            conn.execute("INSERT INTO t VALUES ('total assets')")
            hit = conn.execute("SELECT count(*) FROM t WHERE t MATCH 'assets'").fetchone()[0]
        finally:
            conn.close()
    if hit != 1:
        raise RuntimeError("FTS5 search returned no match")
    return f"sqlite {sqlite3.sqlite_version} with FTS5"


def check_chromadb() -> str:
    import tempfile

    import chromadb

    # Use a throwaway folder so this test never touches real project data.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        client = chromadb.PersistentClient(path=tmp)
        collection = client.get_or_create_collection("check")
        collection.add(ids=["1"], documents=["hello"], embeddings=[[0.1, 0.2, 0.3]])
        count = collection.count()
    return f"chromadb {chromadb.__version__}, stored {count} item"


def check_embeddings() -> str:
    from sentence_transformers import SentenceTransformer

    from audit_rag.config import get_settings

    model = SentenceTransformer(get_settings().embedding_model)
    vector = model.encode(["total assets"])
    return f"vector shape {vector.shape}"


def check_ollama() -> str:
    import ollama

    from audit_rag.config import get_settings

    settings = get_settings()
    client = ollama.Client(host=settings.ollama_host)
    names = [m.model for m in client.list().models]
    if settings.ollama_model not in names:
        raise RuntimeError(f"model {settings.ollama_model!r} not found; have {names}")
    return f"{settings.ollama_model} available"


def main() -> int:
    checks = [
        ("Python", check_python),
        ("Config", check_config),
        ("Tesseract", check_tesseract),
        ("SQLite", check_sqlite),
        ("ChromaDB", check_chromadb),
        ("Embeddings", check_embeddings),
        ("Ollama", check_ollama),
    ]
    results = [run_check(name, fn) for name, fn in checks]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
