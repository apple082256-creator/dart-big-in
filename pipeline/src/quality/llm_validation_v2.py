from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

from src.config import DEFAULT_COMPANIES, PROCESSED_DIR
from src.financial_units import detect_scale_factor, extract_monetary_facts
from src.llm.analyzer import analyze_company
from src.llm.gemini_client import DEFAULT_MODEL, GeminiClient
from src.quality.integration_validation import VALIDATION_QUESTIONS


OUTPUT_DIR = PROCESSED_DIR / "quality"
JSON_PATH = OUTPUT_DIR / "gemini_llm_validation_v2.json"
CSV_PATH = OUTPUT_DIR / "gemini_llm_validation_summary_v2.csv"
PERCENT_PATTERN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")


def _used_value(row: dict) -> int | float | None:
    return row.get("calculated_value") if row.get("is_calculated") else row.get("normalized_value")


def _close(value: Decimal, expected: Decimal, tolerance_rate: Decimal = Decimal("0.001")) -> bool:
    return abs(value - expected) <= max(abs(expected) * tolerance_rate, Decimal(1))


def _scale_mismatch(value: Decimal, allowed: list[Decimal]) -> dict | None:
    for expected in allowed:
        if not expected or not value:
            continue
        factor = detect_scale_factor(value, expected)
        if factor:
            return {
                "answer_value_krw": str(value),
                "evidence_value_krw": str(expected),
                "detected_scale_factor": str(factor),
            }
    return None


def _allowed_amounts(result: dict) -> list[Decimal]:
    financial = [
        Decimal(str(value))
        for row in result["financial_evidence"]
        if (value := _used_value(row)) is not None
    ]
    values = list(financial)
    for row in result["financial_evidence"]:
        try:
            source_payload = json.loads(row.get("source_values") or "null")
        except (TypeError, json.JSONDecodeError):
            source_payload = None

        def add_numeric_leaves(value: object) -> None:
            if isinstance(value, dict):
                for nested in value.values():
                    add_numeric_leaves(nested)
            elif isinstance(value, list):
                for nested in value:
                    add_numeric_leaves(nested)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(Decimal(str(value)))

        add_numeric_leaves(source_payload)
        for key in ("display_value_krw", "display_value_eok", "display_value_trillion"):
            values.extend(
                Decimal(str(fact["normalized_value"]))
                for fact in extract_monetary_facts(str(row.get(key) or ""))
            )
    for left in financial:
        for right in financial:
            if left != right:
                values.append(abs(left - right))
    for row in result["filing_evidence"]:
        for fact in row.get("monetary_facts", []):
            values.append(Decimal(str(fact["normalized_value"])))
            for key in ("display_value_krw", "display_value_eok", "display_value_trillion"):
                values.extend(
                    Decimal(str(display_fact["normalized_value"]))
                    for display_fact in extract_monetary_facts(str(fact.get(key) or ""))
                )
    return list(dict.fromkeys(values))


def validate_result(result: dict) -> dict:
    answer = result["answer"]
    financial = result["financial_evidence"]
    filings = result["filing_evidence"]
    filing_text = "\n".join(str(row.get("chunk_text") or "") for row in filings)
    allowed_amounts = _allowed_amounts(result)
    answer_amounts = [
        Decimal(str(fact["normalized_value"]))
        for fact in extract_monetary_facts(answer, max_facts=200)
    ]
    unsupported_amounts = [
        value for value in answer_amounts
        if not any(_close(value, expected) for expected in allowed_amounts)
    ]
    scale_mismatches = [
        mismatch for value in unsupported_amounts
        if (mismatch := _scale_mismatch(value, allowed_amounts)) is not None
    ]
    required_labeled_amounts = [
        Decimal(str(fact["normalized_value"]))
        for row in filings for fact in row.get("monetary_facts", [])
        if fact.get("source_type") == "facility_investment_table"
    ] if "설비투자" in result["query"] else []
    missing_required_amounts = [
        value for value in required_labeled_amounts
        if not any(_close(answer_value, value) for answer_value in answer_amounts)
    ]

    allowed_percentages = [Decimal(value) for value in PERCENT_PATTERN.findall(filing_text)]
    by_account: dict[tuple[str, str], list[dict]] = {}
    for row in financial:
        key = (row.get("standard_account_nm") or "", row.get("value_type") or "")
        by_account.setdefault(key, []).append(row)
    for rows in by_account.values():
        for current in rows:
            for previous in rows:
                current_value, previous_value = _used_value(current), _used_value(previous)
                if current["year"] == previous["year"] + 1 and previous_value not in (None, 0) and current_value is not None:
                    change = (Decimal(str(current_value)) - Decimal(str(previous_value))) / abs(Decimal(str(previous_value))) * 100
                    allowed_percentages.extend((change, abs(change)))
    unsupported_percentages = [
        value for value in map(Decimal, PERCENT_PATTERN.findall(answer))
        if not any(abs(value - expected) <= Decimal("0.15") for expected in allowed_percentages)
    ]

    allowed_years = {int(row["year"]) for row in financial + filings}
    allowed_years.update(int(value) for value in re.findall(r"(20\d{2})년", filing_text))
    answer_years = {int(value) for value in re.findall(r"(20\d{2})년", answer)}
    unexpected_years = sorted(answer_years - allowed_years)
    other_companies = sorted(
        company for company in DEFAULT_COMPANIES
        if company != result["corp_name"] and company in answer
    )
    allowed_receipts = {str(row["rcept_no"]) for row in financial + filings}
    answer_receipts = set(re.findall(r"(?<!\d)(20\d{12})(?!\d)", answer))
    unexpected_receipts = sorted(answer_receipts - allowed_receipts)

    evidence_mismatch = []
    if any(row.get("corp_name") != result["corp_name"] for row in financial + filings):
        evidence_mismatch.append("retrieved evidence contains a different company")
    if any(row.get("report_type") != result["report_type"] for row in financial + filings):
        evidence_mismatch.append("retrieved evidence contains a different report type")
    if financial and filings:
        financial_receipts = {str(row["rcept_no"]) for row in financial if row["year"] == max(result["years"])}
        if any(str(row["rcept_no"]) not in financial_receipts for row in filings):
            evidence_mismatch.append("financial and filing evidence rcept_no do not match")

    causal_terms = ("원인", "때문", "기여", "결과", "따라", "연결")
    causal_claim_present = result["vector_db_used"] and any(term in answer for term in causal_terms)
    causal_grounding = not causal_claim_present or bool(filings and answer_receipts & allowed_receipts)
    checks = {
        "numeric_accuracy": not unsupported_amounts and not unsupported_percentages and not missing_required_amounts,
        "unit_accuracy": not scale_mismatches,
        "year_consistency": not unexpected_years,
        "company_consistency": not other_companies,
        "rcept_no_consistency": bool(answer_receipts) and not unexpected_receipts,
        "financial_evidence_consistency": not result["financial_db_used"] or bool(financial),
        "filing_evidence_consistency": not result["vector_db_used"] or bool(filings),
        "causal_grounding": causal_grounding,
        "response_complete": result.get("generation_finish_reason") == "STOP",
        "evidence_metadata_consistency": not evidence_mismatch,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "validation_status": status,
        "numeric_mismatch": bool(unsupported_amounts or unsupported_percentages),
        "checks": checks,
        "details": {
            "answer_amounts_krw": [str(value) for value in answer_amounts],
            "unsupported_amounts_krw": [str(value) for value in unsupported_amounts],
            "unsupported_percentages": [str(value) for value in unsupported_percentages],
            "scale_mismatches": scale_mismatches,
            "missing_required_amounts_krw": [str(value) for value in missing_required_amounts],
            "unexpected_years": unexpected_years,
            "other_companies": other_companies,
            "unexpected_rcept_no": unexpected_receipts,
            "evidence_mismatch": evidence_mismatch,
        },
    }


def write_outputs(results: list[dict]) -> None:
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Google Gemini API",
        "models": sorted({result["model"] for result in results}),
        "privacy_scope": "public DART financial evidence and public filing chunks only",
        "unit_normalization": "deterministic Python conversion to KRW before LLM call",
        "all_passed": len(results) == len(VALIDATION_QUESTIONS) and all(
            result["validation"]["validation_status"] == "PASS" for result in results
        ),
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "question_id", "question", "route", "validation_status", "numeric_mismatch",
            "numeric_accuracy", "unit_accuracy", "year_consistency", "company_consistency",
            "rcept_no_consistency", "financial_evidence_consistency", "filing_evidence_consistency",
            "causal_grounding", "response_complete", "financial_evidence_count",
            "evidence_metadata_consistency", "filing_evidence_count", "rcept_nos", "model", "answer",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            validation = result["validation"]
            receipts = sorted({
                str(row["rcept_no"])
                for row in result["financial_evidence"] + result["filing_evidence"]
            })
            writer.writerow({
                "question_id": result["question_id"], "question": result["query"],
                "route": result["route"], "validation_status": validation["validation_status"],
                "numeric_mismatch": validation["numeric_mismatch"], **validation["checks"],
                "financial_evidence_count": len(result["financial_evidence"]),
                "filing_evidence_count": len(result["filing_evidence"]),
                "rcept_nos": ",".join(receipts), "model": result["model"],
                "answer": result["answer"],
            })


def run_validation() -> list[dict]:
    client = GeminiClient(model=DEFAULT_MODEL)
    results: list[dict] = []
    if JSON_PATH.exists():
        saved = json.loads(JSON_PATH.read_text(encoding="utf-8")).get("results", [])
        if all(item.get("query") == VALIDATION_QUESTIONS[offset] for offset, item in enumerate(saved)):
            results = saved
    for index, question in enumerate(VALIDATION_QUESTIONS[len(results):], start=len(results) + 1):
        result = analyze_company(
            question, corp_name="삼성전자", year=2025, report_type="FY",
            retrieval_method="feature_hashing_hybrid", llm_client=client,
        )
        result["generation_finish_reason"] = client.last_finish_reason
        result["question_id"] = index
        result["model"] = client.last_model
        result["validation"] = validate_result(result)
        results.append(result)
        write_outputs(results)
    return results


if __name__ == "__main__":
    for item in run_validation():
        print(item["question_id"], item["validation"]["validation_status"], item["query"])
