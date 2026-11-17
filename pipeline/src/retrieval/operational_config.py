from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.config import OPERATIONAL_COMPANIES, PROJECT_ROOT


BGE_M3_DENSE = "bge_m3_dense"
FEATURE_HASHING_HYBRID = "feature_hashing_hybrid"
SUPPORTED_FILING_RETRIEVERS = (BGE_M3_DENSE, FEATURE_HASHING_HYBRID)
DEFAULT_FILING_RETRIEVER = BGE_M3_DENSE

VECTOR_DIR = PROJECT_ROOT / "db" / "vector"
LEGACY_VECTOR_DB_PATH = VECTOR_DIR / "filings.db"
BGE_DB_PATH = VECTOR_DIR / "filings_bge_m3.db"
BGE_FAISS_PATH = VECTOR_DIR / "filings_bge_m3.faiss"

EXPECTED_BGE_MODEL = "BAAI/bge-m3"
EXPECTED_BGE_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EXPECTED_BGE_DIMENSIONS = 1024
EXPECTED_BGE_COMPANIES = OPERATIONAL_COMPANIES


@dataclass(frozen=True)
class FilingRetrieverSettings:
    retriever: str
    auto_fallback: bool


def _parse_bool(name: str, raw: str | None, *, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false: {raw!r}")


def get_filing_retriever_settings() -> FilingRetrieverSettings:
    retriever = (os.getenv("FILING_RETRIEVER") or DEFAULT_FILING_RETRIEVER).strip()
    if retriever not in SUPPORTED_FILING_RETRIEVERS:
        supported = ", ".join(SUPPORTED_FILING_RETRIEVERS)
        raise ValueError(
            f"지원하지 않는 FILING_RETRIEVER입니다: {retriever!r}. 지원값: {supported}"
        )
    auto_fallback = _parse_bool(
        "FILING_RETRIEVER_AUTO_FALLBACK",
        os.getenv("FILING_RETRIEVER_AUTO_FALLBACK"),
        default=False,
    )
    return FilingRetrieverSettings(retriever=retriever, auto_fallback=auto_fallback)


def relative_path(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
