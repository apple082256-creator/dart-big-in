"""Conservative, model-independent relevance and promotion policy."""
from __future__ import annotations


def is_relevant(result: dict, gold: dict) -> bool:
    if any(result.get(key) != gold.get(expected) for key, expected in (
        ("corp_name", "corp_name"), ("year", "year"),
        ("report_type", "report_type"), ("rcept_no", "expected_rcept_no"),
    )):
        return False
    allowed = set(gold.get("expected_chunk_ids", [])) | set(gold.get("acceptable_chunk_ids", []))
    return result.get("chunk_id") in allowed


def promotion_decision(current: dict, candidate: dict, *, validation: dict,
                       goldset: list[dict], latency_budget_ms: float = 1000,
                       minimum_gain: float = 0.02) -> str:
    if candidate["hit_at_5"] < current["hit_at_5"] or any(
        candidate[key] for key in ("other_corp_count", "other_year_count", "other_report_type_count")
    ):
        return "KEEP_CURRENT_DEFAULT"
    reviewed = len(goldset) >= 30 and all(row.get("review_status") == "reviewed" for row in goldset)
    coverage = (
        len({r["corp_name"] for r in goldset}) >= 5
        and {2023, 2024, 2025} <= {r["year"] for r in goldset}
        and {"Q1", "H1", "Q3", "FY"} <= {r["report_type"] for r in goldset}
    )
    gain = max(candidate["hit_at_1"] - current["hit_at_1"], candidate["mrr"] - current["mrr"])
    if (reviewed and coverage and validation.get("status") == "PASS"
            and candidate["rcept_no_match_rate"] == 1.0
            and candidate["p95_latency_ms"] <= latency_budget_ms and gain >= minimum_gain):
        return "APPROVE_FOR_PROMOTION"
    return "NEEDS_TUNING"
