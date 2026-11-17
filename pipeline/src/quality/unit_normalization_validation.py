from __future__ import annotations

import json
from datetime import datetime, timezone

from src.config import PROCESSED_DIR
from src.financial_units import detect_scale_factor, normalize_financial_value


OUTPUT_PATH = PROCESSED_DIR / "quality" / "unit_normalization_test_v2.json"
CASES = (
    ("1", "억원", 100_000_000),
    ("10", "억원", 1_000_000_000),
    ("1,000", "억원", 100_000_000_000),
    ("474,764", "억원", 47_476_400_000_000),
    ("1", "조원", 1_000_000_000_000),
    ("2.5", "조원", 2_500_000_000_000),
    ("1", "백만원", 1_000_000),
    ("1", "천원", 1_000),
)


def run_validation() -> dict:
    results = []
    for raw_value, raw_unit, expected in CASES:
        actual = normalize_financial_value(raw_value, raw_unit)
        results.append({
            "raw_value": raw_value,
            "raw_unit": raw_unit,
            "expected_krw": expected,
            "actual_krw": actual,
            "passed": actual == expected,
        })
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "all_passed": all(item["passed"] for item in results),
        "passed": sum(item["passed"] for item in results),
        "total": len(results),
        "results": results,
        "scale_error_detection": [
            {
                "expected_factor": factor,
                "detected_factor": detect_scale_factor(100_000_000_000 // factor, 100_000_000_000),
                "passed": detect_scale_factor(100_000_000_000 // factor, 100_000_000_000) == factor,
            }
            for factor in (10, 100, 1_000, 100_000_000)
        ],
    }
    document["all_passed"] = document["all_passed"] and all(
        item["passed"] for item in document["scale_error_detection"]
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


if __name__ == "__main__":
    report = run_validation()
    print(f"unit normalization: {report['passed']}/{report['total']} passed")
