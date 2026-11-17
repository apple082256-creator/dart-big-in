from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

import pandas as pd

from src.collect.financial_collector import DartClient
from src.config import BANK_COMPANY_PROFILES, DEFAULT_YEARS, RAW_DIR, REPORTS, load_settings
from src.db.financial_db import save_financial_data
from src.db.financial_metrics import (
    materialize_financial_metric_values,
    replace_collection_status,
    upsert_company_profiles,
)
from src.db.krx_index_db import replace_current_constituents
from src.mapping.account_mapper import apply_mapping, build_mapping_draft
from src.preprocess.financial_cleaner import clean_financials
from src.preprocess.period_normalizer import normalize_period_values


def _payload(corp_code: str, year: int, report_type: str, fs_div: str) -> dict:
    path = RAW_DIR / "financial" / corp_code / str(year) / f"{report_type}_{fs_div}.json"
    if not path.is_file():
        return {"status": "NOT_TRIED", "list": []}
    return json.loads(path.read_text(encoding="utf-8"))


def build_bank_financial_staging(source: Path, target: Path) -> dict[str, object]:
    """Copy the current DB and append only the five verified finance companies."""
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == target.resolve():
        raise ValueError("source와 target은 달라야 합니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)

    settings = load_settings(tuple(BANK_COMPANY_PROFILES), DEFAULT_YEARS)
    client = DartClient(settings.api_key)
    stock_codes = tuple(profile["stock_code"] for profile in BANK_COMPANY_PROFILES.values())
    companies = client.resolve_companies_by_stock_code(stock_codes)
    actual = {
        str(row.stock_code).zfill(6): str(row.corp_code).zfill(8)
        for row in companies.itertuples()
    }
    expected = {
        str(profile["stock_code"]): str(profile["corp_code"])
        for profile in BANK_COMPANY_PROFILES.values()
    }
    if actual != expected:
        raise RuntimeError(f"DART stock_code→corp_code 불일치: actual={actual}, expected={expected}")

    company_by_code = {
        str(row.corp_code).zfill(8): row._asdict() for row in companies.itertuples()
    }
    raw_rows: list[dict] = []
    statuses: list[dict] = []
    for name, profile in BANK_COMPANY_PROFILES.items():
        corp_code = profile["corp_code"]
        company = company_by_code[corp_code]
        for year in DEFAULT_YEARS:
            for report_type, (report_code, report_name) in REPORTS.items():
                cfs = _payload(corp_code, year, report_type, "CFS")
                cfs_rows = cfs.get("list") or [] if cfs.get("status") == "000" else []
                ofs = _payload(corp_code, year, report_type, "OFS") if not cfs_rows else {
                    "status": "NOT_TRIED", "list": []
                }
                ofs_rows = ofs.get("list") or [] if ofs.get("status") == "000" else []
                selected_fs = "CFS" if cfs_rows else "OFS" if ofs_rows else None
                selected_rows = cfs_rows or ofs_rows
                for account in selected_rows:
                    raw_rows.append({
                        **account,
                        "company_name": name,
                        "stock_code": str(company["stock_code"]).zfill(6),
                        "year": year,
                        "quarter": report_type,
                        "report_code": report_code,
                        "report_name": report_name,
                        "fs_div_requested": selected_fs,
                    })
                statuses.append({
                    "corp_code": corp_code,
                    "year": year,
                    "report_type": report_type,
                    "requested_fs_div": "CFS",
                    "selected_fs_div": selected_fs,
                    "cfs_status": str(cfs.get("status") or "UNKNOWN"),
                    "ofs_status": str(ofs.get("status") or "UNKNOWN"),
                    "missing_reason": None if selected_rows else "DART_FNLTT_API_NO_DATA",
                    "rcept_no": str(selected_rows[0].get("rcept_no")) if selected_rows else None,
                })

    raw = pd.DataFrame(raw_rows)
    cleaned = clean_financials(raw)
    mapping = build_mapping_draft(cleaned)
    standardized = apply_mapping(cleaned, mapping)
    periodized = normalize_period_values(standardized)

    with sqlite3.connect(source) as connection:
        existing_mapping_keys = set(connection.execute(
            "SELECT account_key,sj_div FROM account_mapping"
        ).fetchall())
        old_counts = {
            "companies": connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "financial_facts": connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0],
            "capex_facts": connection.execute("SELECT COUNT(*) FROM capex_facts").fetchone()[0],
        }
    new_mapping = mapping[
        ~mapping.apply(lambda row: (row["account_key"], row["sj_div"]) in existing_mapping_keys, axis=1)
    ].copy()
    save_financial_data(target, periodized, new_mapping)

    profiles = []
    for name, profile in BANK_COMPANY_PROFILES.items():
        profiles.append({
            "corp_code": profile["corp_code"],
            "industry_profile": profile["industry_profile"],
            "preferred_fs_div": profile["preferred_fs_div"],
            "fallback_fs_div": "OFS" if profile["preferred_fs_div"] == "CFS" else None,
            "comparison_scope": (
                "holding_consolidated" if profile["industry_profile"] == "BANK_HOLDING"
                else "bank_separate"
            ),
            "capex_applicable": False,
            "valid_from": "2023-01-01",
            "valid_to": None,
        })
    with sqlite3.connect(target) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        upsert_company_profiles(connection, profiles)
        replace_collection_status(connection, statuses)
        metric_counts = materialize_financial_metric_values(connection)

    constituents = [
        {"corp_code": profile["corp_code"], "weight": None}
        for profile in BANK_COMPANY_PROFILES.values()
    ]
    krx = replace_current_constituents(
        "KRX_BANK", constituents, source_as_of="2026-10-07",
        effective_from="2026-10-07", path=target,
    )
    with sqlite3.connect(target) as connection:
        result = {
            "raw_rows": len(raw),
            "clean_rows": len(cleaned),
            "new_financial_facts": connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
                - old_counts["financial_facts"],
            "new_mapping_rows": len(new_mapping),
            "metric_values": connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0],
            "metric_counts": metric_counts,
            "krx": krx,
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": connection.execute("PRAGMA foreign_key_check").fetchall(),
            "companies": connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "financial_facts": connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0],
            "capex_facts": connection.execute("SELECT COUNT(*) FROM capex_facts").fetchone()[0],
        }
    if result["integrity"] != "ok" or result["foreign_key_errors"]:
        raise RuntimeError(f"staging 무결성 실패: {result}")
    if result["capex_facts"] != old_counts["capex_facts"]:
        raise RuntimeError("기존 CAPEX 행 수가 변경됐습니다.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(build_bank_financial_staging(args.source, args.target))


if __name__ == "__main__":
    main()
