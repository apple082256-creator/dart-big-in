from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from src.config import DB_DIR, MAPPING_DIR, PROCESSED_DIR


REPORT_COLUMNS = (
    "severity", "issue_type", "corp_name", "corp_code", "year", "report_type",
    "rcept_no", "sj_div", "account_id", "account_nm", "value_type",
    "observed_value", "expected_value", "detail",
)


def _issue(row: pd.Series, issue_type: str, detail: str, severity: str = "warning", expected=None) -> dict:
    return {
        "severity": severity, "issue_type": issue_type,
        "corp_name": row.get("corp_name"), "corp_code": row.get("corp_code"),
        "year": row.get("year"), "report_type": row.get("report_type"),
        "rcept_no": row.get("rcept_no"), "sj_div": row.get("sj_div"),
        "account_id": row.get("account_id"), "account_nm": row.get("standard_account_nm"),
        "value_type": row.get("value_type"),
        "observed_value": row.get("calculated_value") if row.get("is_calculated") else row.get("normalized_value"),
        "expected_value": expected, "detail": detail,
    }


def load_financial_facts(path: Path = DB_DIR / "financial.db") -> pd.DataFrame:
    with sqlite3.connect(path) as connection:
        return pd.read_sql_query(
            """SELECT f.*, c.company_name AS corp_name, p.year, p.report_type,
                      p.report_name
               FROM financial_facts f
               JOIN companies c USING(corp_code)
               JOIN periods p USING(period_id)""",
            connection,
        )


def validate_financial_database(path: Path = DB_DIR / "financial.db") -> tuple[pd.DataFrame, pd.DataFrame]:
    facts = load_financial_facts(path)
    issues: list[dict] = []

    for _, row in facts.iterrows():
        if not row["rcept_no"]:
            issues.append(_issue(row, "missing_rcept_no", "Financial/Vector 연결 키 누락", "error"))
        if not row["currency"]:
            issues.append(_issue(row, "missing_currency", "통화/단위 누락", "error"))
        if pd.isna(row["normalized_value"]) and pd.isna(row["calculated_value"]):
            issues.append(_issue(row, "missing_value", "원본 정규화값과 계산값 모두 없음", "error"))
        if row["sj_div"] == "BS" and row["value_type"] != "point_in_time":
            issues.append(_issue(row, "bs_period_mismatch", "BS는 point_in_time이어야 함", "error"))
        if row["is_calculated"]:
            if not row["calculation_method"] or not row["source_values"]:
                issues.append(_issue(row, "calculation_trace_missing", "계산식 또는 입력값 누락", "error"))
                continue
            try:
                sources = json.loads(row["source_values"])
                values = list(sources.values())
                expected = values[0] if len(values) == 1 else values[0] - values[1]
                if int(row["calculated_value"]) != int(expected):
                    issues.append(_issue(row, "calculation_mismatch", "누적 차감 재계산 불일치", "error", expected))
                if expected < 0 and abs(expected) > 2 * max(abs(value) for value in values):
                    issues.append(_issue(row, "abnormal_negative_calculation", "입력 누적값 대비 큰 음수 계산값", "warning"))
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                issues.append(_issue(row, "invalid_source_values", f"계산 입력 파싱 실패: {exc}", "error"))

    direct = facts[facts["is_calculated"].eq(0)].copy()
    pair_keys = ["corp_code", "year", "report_type", "fs_div", "sj_div", "account_key", "ord"]
    quarterly = direct[direct["value_type"].eq("quarterly")][pair_keys + ["normalized_value"]].rename(columns={"normalized_value": "quarterly_value"})
    cumulative = direct[direct["value_type"].eq("cumulative")][pair_keys + ["normalized_value"]].rename(columns={"normalized_value": "cumulative_value"})
    for _, row in quarterly.merge(cumulative, on=pair_keys).iterrows():
        if row["quarterly_value"] >= 0 and row["cumulative_value"] >= 0 and row["quarterly_value"] > row["cumulative_value"]:
            base = facts[(facts["corp_code"].eq(row["corp_code"])) & (facts["year"].eq(row["year"])) & (facts["report_type"].eq(row["report_type"])) & (facts["account_key"].eq(row["account_key"]))].iloc[0].copy()
            base["normalized_value"] = row["quarterly_value"]
            issues.append(_issue(base, "quarterly_exceeds_cumulative", f"quarterly={row['quarterly_value']}, cumulative={row['cumulative_value']}"))

    annual = direct[direct["value_type"].eq("annual")].copy()
    q3 = direct[(direct["report_type"].eq("Q3")) & (direct["value_type"].eq("cumulative"))].copy()
    annual_keys = ["corp_code", "year", "fs_div", "sj_div", "account_key", "ord"]
    compare = annual.merge(q3, on=annual_keys, suffixes=("_fy", "_q3"))
    for _, row in compare.iterrows():
        if row["normalized_value_fy"] >= 0 and row["normalized_value_q3"] >= 0 and row["normalized_value_fy"] < row["normalized_value_q3"]:
            base = pd.Series({
                "corp_name": row["corp_name_fy"], "corp_code": row["corp_code"], "year": row["year"],
                "report_type": "FY", "rcept_no": row["rcept_no_fy"], "sj_div": row["sj_div"],
                "account_id": row["account_id_fy"], "standard_account_nm": row["standard_account_nm_fy"],
                "value_type": "annual", "normalized_value": row["normalized_value_fy"], "is_calculated": 0,
            })
            issues.append(_issue(base, "annual_below_q3_cumulative", f"annual={row['normalized_value_fy']}, Q3={row['normalized_value_q3']}"))

    duplicate_keys = ["corp_code", "year", "report_type", "rcept_no", "fs_div", "sj_div", "account_key", "ord", "value_type", "is_calculated"]
    duplicated = facts.groupby(duplicate_keys, dropna=False).size().reset_index(name="count")
    for _, duplicate in duplicated[duplicated["count"].gt(1)].iterrows():
        base = facts.merge(pd.DataFrame([duplicate[duplicate_keys]]), on=duplicate_keys).iloc[0]
        issues.append(_issue(base, "duplicate_financial_fact", f"동일 의미 레코드 {duplicate['count']}건", "error"))

    core_for_duplicates = pd.read_csv(MAPPING_DIR / "account_mapping_core.csv").fillna("")
    core_ids_for_duplicates = set(
        core_for_duplicates.loc[
            ~core_for_duplicates["account_id"].isin(["", "-표준계정코드 미사용-"]), "account_id"
        ]
    )
    core_fact_rows = facts[facts["account_id"].isin(core_ids_for_duplicates)].copy()
    core_fact_rows["effective_value"] = core_fact_rows["calculated_value"].fillna(core_fact_rows["normalized_value"])
    multiple_keys = ["corp_code", "year", "report_type", "rcept_no", "fs_div", "sj_div", "account_id", "value_type", "is_calculated"]
    multiple = core_fact_rows.groupby(multiple_keys, dropna=False)["effective_value"].nunique().reset_index(name="distinct_values")
    for _, candidate in multiple[multiple["distinct_values"].gt(1)].iterrows():
        base = core_fact_rows.merge(pd.DataFrame([candidate[multiple_keys]]), on=multiple_keys).iloc[0]
        issues.append(_issue(base, "multiple_values_same_receipt", f"동일 핵심계정에 서로 다른 값 {candidate['distinct_values']}개", "warning"))

    report = pd.DataFrame(issues, columns=REPORT_COLUMNS)
    quality_dir = PROCESSED_DIR / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(quality_dir / "financial_quality_report.csv", index=False, encoding="utf-8-sig")

    core = core_for_duplicates
    core_ids = set(core.loc[~core["account_id"].isin(["", "-표준계정코드 미사용-"]), "account_id"])
    core_facts = facts[facts["account_id"].isin(core_ids)].copy()
    summary = (
        core_facts.groupby(["standard_account_nm", "sj_div", "fs_div"], dropna=False)
        .agg(record_count=("fact_id", "size"), company_count=("corp_code", "nunique"),
             receipt_count=("rcept_no", "nunique"), null_value_count=("normalized_value", lambda s: int(s.isna().sum())),
             calculated_count=("is_calculated", "sum"), currency=("currency", lambda s: " | ".join(sorted(set(s.dropna())))))
        .reset_index()
    )
    summary.to_csv(quality_dir / "financial_core_validation.csv", index=False, encoding="utf-8-sig")
    return report, summary


if __name__ == "__main__":
    issues, summary = validate_financial_database()
    print(f"핵심계정 검증 {len(summary)}개 그룹, 이상 후보 {len(issues)}건")
