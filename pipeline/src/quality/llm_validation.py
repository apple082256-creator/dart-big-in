from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from src.config import DEFAULT_COMPANIES, PROCESSED_DIR
from src.llm.analyzer import analyze_company
from src.llm.gemini_client import DEFAULT_MODEL, GeminiClient
from src.quality.integration_validation import VALIDATION_QUESTIONS


OUTPUT_DIR = PROCESSED_DIR / "quality"
JSON_PATH = OUTPUT_DIR / "gemini_llm_validation.json"
CSV_PATH = OUTPUT_DIR / "gemini_llm_validation_summary.csv"
MONEY_PATTERN = re.compile(
    r"(?<![\d.])(?=\d)((?:\d[\d,]*(?:\.\d+)?\s*조\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*억\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*만\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*)?)원"
)
PERCENT_PATTERN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")


def _used_value(row: dict) -> int | float | None:
    return row.get("calculated_value") if row.get("is_calculated") else row.get("normalized_value")


def _money_to_won(expression: str) -> Decimal:
    components = re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|만)", expression)
    if components:
        multiplier = {"만": 10_000, "억": 100_000_000, "조": 1_000_000_000_000}
        return sum(
            (Decimal(number.replace(",", "")) * multiplier[unit] for number, unit in components),
            Decimal(0),
        )
    number = re.search(r"\d[\d,]*(?:\.\d+)?", expression)
    if not number:
        raise InvalidOperation("money expression has no number")
    return Decimal(number.group().replace(",", ""))


def _money_values(text: str) -> list[Decimal]:
    return [_money_to_won(match) for match in MONEY_PATTERN.findall(text)]


def _tabular_money_values(text: str) -> list[Decimal]:
    values: list[Decimal] = []
    unit_multipliers = {"백만원": Decimal(1_000_000), "억원": Decimal(100_000_000)}
    for unit, multiplier in unit_multipliers.items():
        if re.search(rf"단위\s*:\s*{unit}", text):
            values.extend(
                Decimal(number.replace(",", "")) * multiplier
                for number in re.findall(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])", text)
            )
    return values


def _close_to_any(value: Decimal, allowed: list[Decimal]) -> bool:
    for expected in allowed:
        tolerance = max(abs(expected) * Decimal("0.001"), Decimal("1"))
        if abs(value - expected) <= tolerance:
            return True
    return False


def _validate(result: dict) -> dict:
    answer = result["answer"]
    financial = result["financial_evidence"]
    filings = result["filing_evidence"]
    evidence_years = {int(row["year"]) for row in financial + filings}
    answer_years = {int(value) for value in re.findall(r"(20\d{2})년", answer)}
    unexpected_years = sorted(answer_years - evidence_years)

    other_companies = sorted(
        company for company in DEFAULT_COMPANIES
        if company != result["corp_name"] and company in answer
    )
    allowed_receipts = {str(row["rcept_no"]) for row in financial + filings}
    answer_receipts = set(re.findall(r"(?<!\d)(20\d{12})(?!\d)", answer))
    unexpected_receipts = sorted(answer_receipts - allowed_receipts)
    missing_receipt_citation = bool(allowed_receipts) and not bool(answer_receipts)

    filing_text = "\n".join(str(row.get("chunk_text") or "") for row in filings)
    financial_amounts = [Decimal(str(value)) for row in financial if (value := _used_value(row)) is not None]
    allowed_amounts = list(financial_amounts)
    for left in financial_amounts:
        for right in financial_amounts:
            if left != right:
                allowed_amounts.append(abs(left - right))
    allowed_amounts.extend(_money_values(filing_text))
    allowed_amounts.extend(_tabular_money_values(filing_text))
    answer_amounts = _money_values(answer)
    unsupported_amounts = [str(value) for value in answer_amounts if not _close_to_any(value, allowed_amounts)]

    allowed_percentages: list[Decimal] = []
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
    allowed_percentages.extend(Decimal(value) for value in PERCENT_PATTERN.findall(filing_text))
    answer_percentages = [Decimal(value) for value in PERCENT_PATTERN.findall(answer)]
    unsupported_percentages = [
        str(value) for value in answer_percentages
        if not any(abs(value - expected) <= Decimal("0.15") for expected in allowed_percentages)
    ]

    evidence_mismatch = []
    if any(row.get("corp_name") != result["corp_name"] for row in financial + filings):
        evidence_mismatch.append("retrieved evidence contains a different company")
    if any(row.get("report_type") != result["report_type"] for row in financial + filings):
        evidence_mismatch.append("retrieved evidence contains a different report type")
    if financial and filings:
        financial_receipts = {str(row["rcept_no"]) for row in financial if row["year"] == max(result["years"])}
        if any(str(row["rcept_no"]) not in financial_receipts for row in filings):
            evidence_mismatch.append("financial and filing evidence rcept_no do not match")

    checks = {
        "numeric_hallucination": not unsupported_amounts and not unsupported_percentages,
        "evidence_consistency": not evidence_mismatch and not unexpected_receipts,
        "year_mixing": not unexpected_years,
        "other_company_contamination": not other_companies,
        "rcept_no_cited": not missing_receipt_citation,
        "response_complete": result.get("generation_finish_reason") in {None, "STOP"},
    }
    critical_checks = (
        "numeric_hallucination", "evidence_consistency", "year_mixing",
        "other_company_contamination",
    )
    status = "fail" if any(not checks[name] for name in critical_checks) else (
        "pass" if all(checks.values()) else "warning"
    )
    return {
        "status": status,
        "checks": checks,
        "details": {
            "unsupported_amounts_won": unsupported_amounts,
            "unsupported_percentages": unsupported_percentages,
            "unexpected_years": unexpected_years,
            "unexpected_rcept_no": unexpected_receipts,
            "other_companies": other_companies,
            "evidence_mismatch": evidence_mismatch,
        },
    }


def run_validation() -> list[dict]:
    client = GeminiClient(model=DEFAULT_MODEL)
    results: list[dict] = []
    for index, question in enumerate(VALIDATION_QUESTIONS, start=1):
        result = analyze_company(
            question,
            corp_name="삼성전자",
            year=2025,
            report_type="FY",
            retrieval_method="feature_hashing_hybrid",
            llm_client=client,
        )
        result["generation_finish_reason"] = client.last_finish_reason
        result["validation"] = _validate(result)
        result["question_id"] = index
        result["model"] = client.last_model
        results.append(result)
        _write_outputs(results)
    return results


def _write_outputs(results: list[dict]) -> None:
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Google Gemini API",
        "models": sorted({result["model"] for result in results}),
        "privacy_scope": "public DART financial evidence and public filing chunks only",
        "results": results,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            "question_id", "question", "route", "status", "numeric_hallucination",
            "evidence_consistency", "year_mixing", "other_company_contamination",
            "rcept_no_cited", "response_complete", "financial_evidence_count", "filing_evidence_count",
            "rcept_nos", "answer",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            checks = result["validation"]["checks"]
            receipts = sorted({
                str(row["rcept_no"])
                for row in result["financial_evidence"] + result["filing_evidence"]
            })
            writer.writerow({
                "question_id": result["question_id"], "question": result["query"],
                "route": result["route"], "status": result["validation"]["status"],
                **checks,
                "financial_evidence_count": len(result["financial_evidence"]),
                "filing_evidence_count": len(result["filing_evidence"]),
                "rcept_nos": ",".join(receipts), "answer": result["answer"],
            })


if __name__ == "__main__":
    for item in run_validation():
        print(item["question_id"], item["validation"]["status"], item["query"])
