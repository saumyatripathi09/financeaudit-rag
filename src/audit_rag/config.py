"""Central configuration for the Audit RAG project.

Every setting is read from environment variables (optionally loaded from the
.env file in the project root). No other module should read .env directly.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# This file is src/audit_rag/config.py, so the project root is two levels up.
# Computing it from __file__ means no machine-specific path is hardcoded.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Load .env from the project root, not from wherever the command was run.
# load_dotenv does NOT override variables already set in the real environment.
load_dotenv(PROJECT_ROOT / ".env")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseModel):
    """All application settings, validated once at startup."""

    # frozen=True: settings cannot be changed after creation, which avoids
    # surprising bugs where one module quietly alters a shared setting.
    model_config = ConfigDict(frozen=True)

    data_dir: Path
    tesseract_cmd: Path | None = None
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    allow_remote_llm: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_upload_mb: int = Field(default=50, gt=0, le=1024)
    log_level: str = "INFO"

    @field_validator("data_dir")
    @classmethod
    def _make_data_dir_absolute(cls, value: Path) -> Path:
        """A relative data dir (like 'data') is anchored at the project root."""
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of {sorted(VALID_LOG_LEVELS)}, got {value!r}"
            )
        return level

    @model_validator(mode="after")
    def _refuse_remote_llm(self) -> "Settings":
        """Privacy guard: documents must not be sent to a non-local LLM by accident."""
        host = urlparse(self.ollama_host).hostname
        if host not in LOCAL_HOSTS and not self.allow_remote_llm:
            raise ValueError(
                f"OLLAMA_HOST points to a non-local host ({host!r}). Financial "
                "documents would leave this machine. Set ALLOW_REMOTE_LLM=true "
                "only if you explicitly approve that."
            )
        return self

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "audit_rag.db"

    @property
    def vector_dir(self) -> Path:
        return self.data_dir / "vector_store"

    def ensure_directories(self) -> None:
        """Create the local storage folders if they do not exist yet."""
        for folder in (self.data_dir, self.uploads_dir, self.vector_dir):
            folder.mkdir(parents=True, exist_ok=True)


def _optional_path(name: str) -> Path | None:
    """Read an environment variable as a Path, or None if unset or empty."""
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build the settings object once and reuse it everywhere."""
    return Settings(
        data_dir=Path(os.getenv("AUDIT_RAG_DATA_DIR", "data")),
        tesseract_cmd=_optional_path("TESSERACT_CMD"),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1:8b"),
        allow_remote_llm=os.getenv("ALLOW_REMOTE_LLM", "false").strip().lower() == "true",
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
