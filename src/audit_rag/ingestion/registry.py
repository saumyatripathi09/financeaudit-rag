"""Document registry: validate an upload, fingerprint it, store a copy, record it."""
from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from audit_rag.config import Settings
from audit_rag.db.database import open_database

EXTENSION_TO_TYPE = {
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
}
HEAD_BYTES = 8192  # how much of the start of a file we inspect
CHUNK_BYTES = 1024 * 1024  # hash big files piece by piece, never all at once


class RegistrationError(Exception):
    """A file could not be registered. The message is safe to show the user."""


class UnsupportedFileTypeError(RegistrationError):
    pass


class EmptyFileError(RegistrationError):
    pass


class FileTooLargeError(RegistrationError):
    pass


class ContentMismatchError(RegistrationError):
    """The file's contents do not match its extension."""


def _matches_content(file_type: str, source: Path, head: bytes) -> bool:
    """Check the start of the file against what its extension claims."""
    if file_type == "pdf":
        return b"%PDF-" in head[:1024]
    if file_type == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if file_type == "jpg":
        return head.startswith(b"\xff\xd8\xff")
    if file_type == "xls":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if file_type == "xlsx":
        # An .xlsx is a zip archive, but so is a .docx. Look for the workbook part.
        if not head.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(source) as archive:
                return "xl/workbook.xml" in archive.namelist()
        except zipfile.BadZipFile:
            return False
    if file_type == "csv":
        # CSV has no signature. Plain text never contains null bytes.
        return b"\x00" not in head
    return False


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(name: str, suffix: str) -> str:
    """A filename safe to store: no folders, no odd characters, correct extension."""
    base = Path(name).name  # drops any "../../" or folder part
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "document"
    if not cleaned.lower().endswith(suffix):
        cleaned += suffix
    return cleaned


@dataclass(frozen=True)
class RegisteredDocument:
    """What the rest of the system needs to know about a stored document."""

    document_id: str
    original_filename: str
    stored_path: str  # relative to the data folder, never an absolute machine path
    sha256: str
    size_bytes: int
    file_type: str
    status: str
    registered_at: str
    is_duplicate: bool = False


INSERT = (
    "INSERT INTO documents (document_id, original_filename, stored_path, sha256, "
    "size_bytes, file_type, status, registered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


def register_document(source: Path, settings: Settings) -> RegisteredDocument:
    """Validate and register one file. Raises RegistrationError if it is not acceptable.

    If the same content was registered before, the existing record is returned with
    is_duplicate=True and nothing new is stored.
    """
    source = Path(source)
    if not source.is_file():
        raise RegistrationError(f"file not found: {source.name}")

    suffix = source.suffix.lower()
    file_type = EXTENSION_TO_TYPE.get(suffix)
    if file_type is None:
        shown = suffix or "(none)"
        supported = ", ".join(sorted(EXTENSION_TO_TYPE))
        raise UnsupportedFileTypeError(f"unsupported file type {shown}; supported: {supported}")

    size = source.stat().st_size
    if size == 0:
        raise EmptyFileError(f"{source.name} is empty")
    if size > settings.max_upload_mb * 1024 * 1024:
        raise FileTooLargeError(f"{source.name} is larger than the {settings.max_upload_mb} MB limit")

    with source.open("rb") as handle:
        head = handle.read(HEAD_BYTES)
    if not _matches_content(file_type, source, head):
        raise ContentMismatchError(f"{source.name} does not look like a real {file_type} file")

    sha = _sha256_of(source)
    document_id = f"doc-{sha[:16]}"
    dest_dir = settings.uploads_dir / document_id
    dest = dest_dir / _safe_filename(source.name, suffix)
    record = RegisteredDocument(
        document_id=document_id,
        original_filename=source.name,
        stored_path=dest.relative_to(settings.data_dir).as_posix(),
        sha256=sha,
        size_bytes=size,
        file_type=file_type,
        status="registered",
        registered_at=datetime.now(timezone.utc).isoformat(),
    )

    created_dir = False
    try:
        with open_database(settings.db_path) as conn:
            existing = conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha,)).fetchone()
            if existing is not None:
                return RegisteredDocument(
                    document_id=existing["document_id"],
                    original_filename=existing["original_filename"],
                    stored_path=existing["stored_path"],
                    sha256=existing["sha256"],
                    size_bytes=existing["size_bytes"],
                    file_type=existing["file_type"],
                    status=existing["status"],
                    registered_at=existing["registered_at"],
                    is_duplicate=True,
                )
            conn.execute(
                INSERT,
                (
                    record.document_id, record.original_filename, record.stored_path,
                    record.sha256, record.size_bytes, record.file_type, record.status,
                    record.registered_at,
                ),
            )
            created_dir = not dest_dir.exists()
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            # Prove the stored copy is identical (the file could change mid-upload).
            if _sha256_of(dest) != sha:
                raise RegistrationError(
                    f"stored copy of {source.name} differs from the original; try again"
                )
    except Exception:
        # The insert is rolled back by open_database. Remove the folder only if WE
        # created it, so we never delete another document's files.
        if created_dir:
            shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return record
