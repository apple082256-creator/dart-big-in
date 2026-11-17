from __future__ import annotations

import json

from src.config import PROCESSED_DIR
from src.llm.analyzer import analyze_company


VALIDATION_QUESTIONS = (
    "삼성전자 2025년 매출액과 영업이익은 얼마인가?",
    "삼성전자 2025년 영업이익은 전년 대비 어떻게 변했는가?",
    "삼성전자의 2025년 반도체 사업 위험요인은 무엇인가?",
    "삼성전자의 2025년 실적 변화와 그 원인을 공시 근거와 함께 설명해줘.",
    "삼성전자의 설비투자와 향후 사업 전략의 연결점을 설명해줘.",
)


def build_integration_validation() -> list[dict]:
    results = [
        analyze_company(
            question, corp_name="삼성전자", year=2025, report_type="FY",
            retrieval_method="feature_hashing_hybrid",
        )
        for question in VALIDATION_QUESTIONS
    ]
    output = PROCESSED_DIR / "quality" / "integration_validation.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    results = build_integration_validation()
    for result in results:
        print(
            result["route"], result["financial_db_used"], result["vector_db_used"],
            len(result["financial_sources"]), len(result["filing_sources"]), result["query"],
        )
