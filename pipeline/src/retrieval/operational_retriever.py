from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from src.db.krx_index_db import get_companies_by_index
from src.retrieval.bge_m3_indexer import search_bge_m3
from src.retrieval.operational_config import (
    BGE_M3_DENSE,
    FEATURE_HASHING_HYBRID,
    FilingRetrieverSettings,
    get_filing_retriever_settings,
)
from src.retrieval.vector_retriever import search_filing_context


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilingRetrievalResult:
    rows: list[dict]
    requested_retriever: str
    effective_retriever: str
    fallback_reason: str | None = None


def retrieve_filing_context(
    query: str,
    *,
    corp_name: str | None = None,
    corp_code: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    rcept_no: str | None = None,
    top_k: int = 5,
    retriever: str | None = None,
    settings: FilingRetrieverSettings | None = None,
    corp_codes: list[str] | tuple[str, ...] | None = None,
) -> FilingRetrievalResult:
    settings = settings or get_filing_retriever_settings()
    requested = retriever or settings.retriever
    if requested not in {BGE_M3_DENSE, FEATURE_HASHING_HYBRID}:
        raise ValueError(f"지원하지 않는 공시 검색 방식입니다: {requested!r}")

    kwargs = {
        "corp_name": corp_name,
        "corp_code": corp_code,
        "corp_codes": corp_codes,
        "year": year,
        "report_type": report_type,
        "rcept_no": rcept_no,
        "top_k": top_k,
    }
    if requested == FEATURE_HASHING_HYBRID:
        return FilingRetrievalResult(
            rows=search_filing_context(query, **kwargs),
            requested_retriever=requested,
            effective_retriever=FEATURE_HASHING_HYBRID,
        )

    try:
        rows = search_bge_m3(query, hybrid=False, apply_priority=False, **kwargs)
        return FilingRetrievalResult(
            rows=rows,
            requested_retriever=requested,
            effective_retriever=BGE_M3_DENSE,
        )
    except Exception as exc:
        if not settings.auto_fallback:
            raise
        reason = f"{type(exc).__name__}: {exc}"
        LOGGER.warning(
            "BGE-M3 Dense retrieval failed; using explicitly enabled legacy fallback: %s",
            reason,
        )
        rows = search_filing_context(query, **kwargs)
        for row in rows:
            row["fallback_reason"] = reason
        return FilingRetrievalResult(
            rows=rows,
            requested_retriever=requested,
            effective_retriever=FEATURE_HASHING_HYBRID,
            fallback_reason=reason,
        )


def retrieve_index_filing_context(
    query: str,
    index_code: str,
    *,
    as_of: str | None = None,
    financial_db_path: Path | None = None,
    **kwargs,
) -> FilingRetrievalResult:
    """Resolve an index membership snapshot before metadata-first vector search."""
    if kwargs.get("corp_code") is not None or kwargs.get("corp_codes") is not None:
        raise ValueError("지수 검색에는 corp_code/corp_codes를 함께 지정할 수 없습니다.")
    companies = get_companies_by_index(index_code, as_of=as_of, path=financial_db_path) \
        if financial_db_path is not None else get_companies_by_index(index_code, as_of=as_of)
    kwargs["corp_codes"] = [row["corp_code"] for row in companies]
    return retrieve_filing_context(query, **kwargs)
