from __future__ import annotations

import pandas as pd

from src.config import PROCESSED_DIR
from src.retrieval.transformer_retriever import search_filing_context_transformer
from src.retrieval.vector_retriever import search_filing_context


QUERY_EXPECTATIONS = {
    "매출 감소 원인은 무엇인가?": ("이사의 경영진단", "매출 및 수주상황", "사업의 개요"),
    "반도체 업황 악화가 실적에 어떤 영향을 줬는가?": ("사업의 내용", "이사의 경영진단"),
    "설비투자를 늘린 이유는 무엇인가?": ("원재료 및 생산설비",),
    "수익성이 악화된 이유는 무엇인가?": ("이사의 경영진단", "매출 및 수주상황", "사업의 개요"),
    "향후 성장 동력은 무엇인가?": ("사업의 내용",),
}


def _note(result: dict, expected: tuple[str, ...]) -> str:
    heading = f"{result['section_name']} {result.get('subsection_name') or ''}"
    if any(term in heading for term in expected):
        return "likely_relevant_section"
    return "manual_review"


def build_vector_comparison() -> pd.DataFrame:
    rows: list[dict] = []
    methods = (
        ("feature_hashing_hybrid", search_filing_context),
        ("multilingual_e5_small", search_filing_context_transformer),
    )
    for query, expected in QUERY_EXPECTATIONS.items():
        for method_name, search in methods:
            results = search(query, corp_name="삼성전자", year=2025, report_type="FY", top_k=5)
            for rank, result in enumerate(results, start=1):
                rows.append({
                    "query": query, "retrieval_method": method_name, "rank": rank,
                    "corp_name": result["corp_name"], "year": result["year"],
                    "report_type": result["report_type"], "rcept_no": result["rcept_no"],
                    "section_name": result["section_name"],
                    "subsection_name": result.get("subsection_name"),
                    "score": result["score"], "relevance_note": _note(result, expected),
                    "chunk_id": result["chunk_id"], "source_file": result["source_file"],
                })
    frame = pd.DataFrame(rows)
    output = PROCESSED_DIR / "quality" / "vector_search_comparison.csv"
    frame.to_csv(output, index=False, encoding="utf-8-sig")
    return frame


if __name__ == "__main__":
    frame = build_vector_comparison()
    print(frame.groupby("retrieval_method")["relevance_note"].value_counts().to_dict())
