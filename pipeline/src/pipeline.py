from __future__ import annotations

import argparse

from src.collect.financial_collector import DartClient, collect_financials
from src.config import DEFAULT_COMPANIES, DEFAULT_YEARS, FINANCIAL_DB_PATH, MAPPING_DIR, PROCESSED_DIR, ensure_directories, load_settings
from src.db.final_db import build_final_database
from src.mapping.account_mapper import apply_mapping, build_account_comparison, build_mapping_anomalies, build_mapping_draft
from src.preprocess.financial_cleaner import clean_financials
from src.preprocess.period_normalizer import normalize_period_values


def run_pipeline(companies: tuple[str, ...], years: tuple[int, ...], fs_div: str, refresh: bool) -> dict[str, int]:
    ensure_directories()
    settings = load_settings(companies, years, fs_div)
    client = DartClient(settings.api_key)
    company_master = client.resolve_companies(settings.companies)
    raw_frame = collect_financials(client, company_master, settings.years, settings.fs_div, refresh)
    if raw_frame.empty:
        raise RuntimeError("수집된 재무 데이터가 없습니다. 대상 연도와 보고서 공개 여부를 확인하세요.")

    cleaned = clean_financials(raw_frame)
    comparison = build_account_comparison(cleaned)
    mapping = build_mapping_draft(cleaned)
    mapping_anomalies = build_mapping_anomalies(cleaned)
    standardized = apply_mapping(cleaned, mapping)
    periodized = normalize_period_values(standardized)

    comparison.to_csv(PROCESSED_DIR / "account_comparison.csv", index=False, encoding="utf-8-sig")
    mapping.to_csv(MAPPING_DIR / "account_mapping_draft.csv", index=False, encoding="utf-8-sig")
    mapping_anomalies.to_csv(MAPPING_DIR / "account_mapping_review.csv", index=False, encoding="utf-8-sig")
    standardized.to_csv(PROCESSED_DIR / "financial" / "financial_accounts_wide.csv", index=False, encoding="utf-8-sig")
    periodized.to_csv(PROCESSED_DIR / "financial" / "financial_facts.csv", index=False, encoding="utf-8-sig")
    final_result = build_final_database(periodized, mapping, target_path=FINANCIAL_DB_PATH)
    return {
        "companies": standardized["corp_code"].nunique(),
        "facts": len(periodized),
        "mapping_rows": len(mapping),
        "capex_base_records": final_result["capex_base_records"],
        "capex_quarterly_records": final_result["capex_quarterly_records"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DART 재무계정 표준화 파이프라인")
    parser.add_argument("--companies", nargs="+", default=list(DEFAULT_COMPANIES))
    parser.add_argument("--years", nargs="+", type=int, default=list(DEFAULT_YEARS))
    parser.add_argument("--fs-div", choices=("CFS", "OFS"), default="CFS")
    parser.add_argument("--refresh", action="store_true", help="캐시된 원본 대신 API를 다시 호출")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_pipeline(tuple(args.companies), tuple(args.years), args.fs_div, args.refresh)
    print(
        f"완료: {result['companies']}개 기업, {result['facts']:,}개 재무 항목, "
        f"{result['mapping_rows']:,}개 매핑 후보"
    )


if __name__ == "__main__":
    main()
