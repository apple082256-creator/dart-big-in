from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

from src.config import PROCESSED_DIR
from src.llm.analyzer import analyze_company
from src.llm.gemini_client import DEFAULT_MODEL, GeminiClient
from src.quality.llm_validation_v2 import validate_result


OUTPUT_DIR = PROCESSED_DIR / "quality"
JSON_PATH = OUTPUT_DIR / "final_db_llm_validation.json"
CSV_PATH = OUTPUT_DIR / "final_db_llm_validation_summary.csv"
QUESTION_CASES = (
    {
        "query": "삼성전자 2025년 매출액과 영업이익은 얼마인가?",
        "corp_name": "삼성전자", "year": 2025, "report_type": "FY",
    },
    {
        "query": "삼성전자 2025년 영업이익은 전년 대비 어떻게 변했는가?",
        "corp_name": "삼성전자", "year": 2025, "report_type": "FY",
    },
    {
        "query": "삼성전자의 2025년 반도체 사업 위험요인은 무엇인가?",
        "corp_name": "삼성전자", "year": 2025, "report_type": "FY",
    },
    {
        "query": "삼성전자의 2025년 실적 변화와 그 원인을 공시 근거와 함께 설명해줘.",
        "corp_name": "삼성전자", "year": 2025, "report_type": "FY",
    },
    {
        "query": "삼성전자의 설비투자와 향후 사업 전략의 연결점을 설명해줘.",
        "corp_name": "삼성전자", "year": 2025, "report_type": "FY",
    },
    {
        "query": "주성엔지니어링 2024년 2분기 CAPEX는 얼마인가?",
        "corp_name": "주성엔지니어링", "year": 2024, "report_type": "H1",
        "expected_value_source": "derived", "expected_value": 5_023_179_778,
        "expected_value_type": "quarterly", "expected_is_derived": 1,
    },
    {
        "query": "원익IPS 2024년 CAPEX는 어떻게 산출되었는가?",
        "corp_name": "원익IPS", "year": 2024, "report_type": "FY",
        "expected_value_source": "direct", "expected_value": 21_432_773_671,
        "expected_value_type": "annual", "expected_is_derived": 0,
    },
)


def _add_final_db_checks(result: dict, case: dict) -> dict:
    validation = validate_result(result)
    rows = result["financial_evidence"]
    expected_source = case.get("expected_value_source")
    expected_value = case.get("expected_value")
    expected_type = case.get("expected_value_type")
    expected_derived = case.get("expected_is_derived")

    matching = [
        row for row in rows
        if (expected_source is None or row.get("value_source") == expected_source)
        and (expected_type is None or row.get("value_type") == expected_type)
        and (expected_value is None or (
            row.get("calculated_value") if row.get("is_calculated") else row.get("normalized_value")
        ) == expected_value)
    ]
    extra_checks = {
        "period_accuracy": all(
            row.get("year") in result["years"] and row.get("report_type") == result["report_type"]
            for row in rows
        ),
        "value_source_accuracy": expected_source is None or bool(matching),
        "direct_derived_accuracy": expected_derived is None or bool(matching) and all(
            row.get("is_derived") == expected_derived for row in matching
        ),
        "capex_standard_account_accuracy": expected_source is None or bool(matching) and all(
            row.get("standard_account_nm") == "유형자산취득(CAPEX)" for row in matching
        ),
    }
    validation["checks"].update(extra_checks)
    validation["validation_status"] = "PASS" if all(validation["checks"].values()) else "FAIL"
    validation["details"]["expected_capex"] = {
        "value_source": expected_source,
        "value": expected_value,
        "value_type": expected_type,
        "is_derived": expected_derived,
        "matching_evidence_count": len(matching),
    }
    return validation


def _write_outputs(results: list[dict]) -> None:
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Google Gemini API",
        "models": sorted({result["model"] for result in results}),
        "privacy_scope": "public DART financial evidence and public filing chunks only",
        "database_role": "financial_final.db application candidate",
        "all_passed": len(results) == len(QUESTION_CASES) and all(
            result["validation"]["validation_status"] == "PASS" for result in results
        ),
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    check_names = sorted({
        name for result in results for name in result["validation"]["checks"]
    })
    fieldnames = [
        "question_id", "question", "corp_name", "report_type", "route",
        "validation_status", *check_names, "financial_evidence_count",
        "filing_evidence_count", "value_sources", "rcept_nos", "model", "answer",
    ]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            checks = result["validation"]["checks"]
            writer.writerow({
                "question_id": result["question_id"], "question": result["query"],
                "corp_name": result["corp_name"], "report_type": result["report_type"],
                "route": result["route"],
                "validation_status": result["validation"]["validation_status"],
                **{name: checks.get(name) for name in check_names},
                "financial_evidence_count": len(result["financial_evidence"]),
                "filing_evidence_count": len(result["filing_evidence"]),
                "value_sources": ",".join(sorted({
                    str(row.get("value_source")) for row in result["financial_evidence"]
                    if row.get("value_source") is not None
                })),
                "rcept_nos": ",".join(sorted({
                    str(row["rcept_no"])
                    for row in result["financial_evidence"] + result["filing_evidence"]
                })),
                "model": result["model"], "answer": result["answer"],
            })


def run_validation() -> list[dict]:
    client = GeminiClient(model=DEFAULT_MODEL)
    results: list[dict] = []
    if JSON_PATH.exists():
        saved = json.loads(JSON_PATH.read_text(encoding="utf-8")).get("results", [])
        if all(
            item.get("query") == QUESTION_CASES[offset]["query"]
            for offset, item in enumerate(saved)
            if offset < len(QUESTION_CASES)
        ):
            results = saved[:len(QUESTION_CASES)]
            for offset, result in enumerate(results):
                result["validation"] = _add_final_db_checks(result, QUESTION_CASES[offset])
            _write_outputs(results)
    for index, case in enumerate(QUESTION_CASES[len(results):], start=len(results) + 1):
        result = analyze_company(
            case["query"], corp_name=case["corp_name"], year=case["year"],
            report_type=case["report_type"], retrieval_method="feature_hashing_hybrid",
            llm_client=client,
        )
        result["generation_finish_reason"] = client.last_finish_reason
        result["question_id"] = index
        result["model"] = client.last_model
        result["validation"] = _add_final_db_checks(result, case)
        results.append(result)
        _write_outputs(results)
    return results


if __name__ == "__main__":
    for item in run_validation():
        print(item["question_id"], item["validation"]["validation_status"], item["query"])
