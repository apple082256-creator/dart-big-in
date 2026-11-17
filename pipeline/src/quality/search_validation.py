from __future__ import annotations

import pandas as pd

from src.config import PROCESSED_DIR
from src.retrieval.vector_retriever import search_filing_context


VALIDATION_QUERIES = (
    "삼성전자의 반도체 사업 현황",
    "삼성전자의 연구개발 투자 내용",
    "삼성전자 2025년 주요 사업 위험요인",
    "삼성전자 설비투자 관련 내용",
)


def build_search_validation_report() -> pd.DataFrame:
    rows: list[dict] = []
    for query in VALIDATION_QUERIES:
        results = search_filing_context(
            query, corp_name="삼성전자", year=2025, report_type="FY", top_k=5
        )
        for rank, result in enumerate(results, start=1):
            rows.append({"query": query, "rank": rank, **result})
    frame = pd.DataFrame(rows)
    output = PROCESSED_DIR / "quality" / "search_validation_report.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, encoding="utf-8-sig")
    return frame


if __name__ == "__main__":
    report = build_search_validation_report()
    print(f"검색 검증 결과 {len(report)}건 저장 완료")
