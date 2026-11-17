from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import PROJECT_ROOT
from src.llm.analyzer import analyze_company
from src.retrieval.bge_m3_indexer import BGE_DB_PATH, BGE_FAISS_PATH, validate_bge_operational_assets
from src.retrieval.operational_config import (
    BGE_M3_DENSE,
    EXPECTED_BGE_COMPANIES,
    FEATURE_HASHING_HYBRID,
    FilingRetrieverSettings,
    get_filing_retriever_settings,
)
from src.retrieval.operational_retriever import retrieve_filing_context


REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "quality" / "bge_m3_dense_promotion_report.json"


def _asset_counts() -> dict[str, int]:
    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        row = connection.execute(
            """SELECT COUNT(*), COUNT(DISTINCT rcept_no), COUNT(DISTINCT corp_name)
               FROM filing_chunks WHERE is_searchable=1"""
        ).fetchone()
    return {"searchable_vectors": int(row[0]), "filings": int(row[1]), "companies": int(row[2])}


def run_promotion_smoke(*, write_report: bool = True) -> dict[str, object]:
    integrity = validate_bge_operational_assets()
    settings = get_filing_retriever_settings()
    counts = _asset_counts()
    result = analyze_company(
        "삼성전자 2025년 반도체 사업의 주요 위험을 공시 근거로 설명해줘",
        corp_name="삼성전자",
        year=2025,
        report_type="FY",
    )
    filing_rows = result["filing_evidence"]
    required = (
        "corp_name", "corp_code", "year", "report_type", "rcept_no",
        "section_name", "subsection_name", "source_file", "chunk_id", "chunk_text", "score",
    )
    bge_smoke_passed = (
        bool(filing_rows)
        and result["effective_retrieval_method"] == BGE_M3_DENSE
        and result["retrieval_fallback_reason"] is None
        and all(row["corp_name"] == "삼성전자" for row in filing_rows)
        and all(row["year"] == 2025 for row in filing_rows)
        and all(row["report_type"] == "FY" for row in filing_rows)
        and all(all(key in row for key in required) for row in filing_rows)
    )
    rollback = retrieve_filing_context(
        "반도체 사업 위험요인",
        corp_name="삼성전자",
        year=2025,
        report_type="FY",
        top_k=1,
        settings=FilingRetrieverSettings(FEATURE_HASHING_HYBRID, False),
    )
    rollback_passed = (
        rollback.effective_retriever == FEATURE_HASHING_HYBRID and bool(rollback.rows)
    )
    tests = {
        "central_default_bge_m3_dense": settings.retriever == BGE_M3_DENSE,
        "auto_fallback_default_off": not settings.auto_fallback,
        "sqlite_faiss_integrity": integrity["status"] == "PASS",
        "expected_asset_counts": counts == {
            "searchable_vectors": 21_797, "filings": 60, "companies": 5,
        },
        "expected_company_scope": tuple(integrity["companies"]) == EXPECTED_BGE_COMPANIES,
        "retriever_analyzer_smoke": bge_smoke_passed,
        "legacy_rollback": rollback_passed,
    }
    report = {
        "status": "PASS" if all(tests.values()) else "FAIL",
        "generated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "promotion_decision": "USER_DIRECTED_PROMOTION",
        "previous_default": FEATURE_HASHING_HYBRID,
        "current_default": BGE_M3_DENSE,
        "auto_fallback_default": False,
        "assets": {
            "sqlite_path": str(Path(BGE_DB_PATH).relative_to(PROJECT_ROOT)),
            "faiss_path": str(Path(BGE_FAISS_PATH).relative_to(PROJECT_ROOT)),
            "model": integrity["model"],
            "model_revision": integrity["model_revision"],
            "dimensions": integrity["dimensions"],
            **counts,
            "company_names": integrity["companies"],
        },
        "tests": tests,
        "smoke_result": {
            "query_count": 1,
            "result_count": len(filing_rows),
            "requested_retriever": result["requested_retrieval_method"],
            "effective_retriever": result["effective_retrieval_method"],
            "fallback_reason": result["retrieval_fallback_reason"],
        },
        "rollback": {
            "environment": "FILING_RETRIEVER=feature_hashing_hybrid",
            "verified": rollback_passed,
        },
        "known_limitations": [
            "과거 검토 정답셋에서 BGE-M3 Dense의 Hit@1과 MRR은 Feature Hashing보다 낮았다.",
            "현대자동차, NAVER, LG화학은 현재 BGE 운영 인덱스에 포함되지 않는다.",
            "BGE 모델 최초 로딩은 legacy 검색보다 느리고 메모리 사용량이 크다.",
        ],
        "reembedding_performed": False,
        "index_rebuild_performed": False,
    }
    if write_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_promotion_smoke(), ensure_ascii=False, indent=2))
