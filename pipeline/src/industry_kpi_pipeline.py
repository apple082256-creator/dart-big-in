"""Stage industry KPIs for the general (non-financial) companies.

On a snapshot of the operational DB: register the 20 general companies in
``company_financial_profiles`` (industry from their KRX group, statement scope
from their facts), add the general KPI definitions and official-tag mappings,
fill only empty metric slots, and derive ratios/growth/changes.  Facts,
CAPEX and existing bank metric values are not modified.  Reruns change nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.financial_metrics import (
    add_missing_fact_metrics,
    initialize_financial_metric_schema,
    upsert_company_profiles,
)
from src.db.industry_kpi_config import PROFILE_BY_INDEX


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def general_company_profiles(connection: sqlite3.Connection) -> list[dict]:
    """Profiles for KRX-grouped companies that have no financial profile yet."""
    existing = {row[0] for row in connection.execute("SELECT corp_code FROM company_financial_profiles")}
    profiles = []
    for corp_code, index_code in connection.execute(
            """SELECT corp_code, index_code FROM krx_index_constituents
               WHERE is_current=1 ORDER BY index_code, corp_code"""):
        if corp_code in existing or index_code not in PROFILE_BY_INDEX:
            continue
        scopes = {row[0] for row in connection.execute(
            "SELECT DISTINCT fs_div FROM financial_facts WHERE corp_code=?", (corp_code,))}
        if len(scopes) != 1:
            raise RuntimeError(f"{corp_code}: 재무제표 범위가 하나가 아닙니다: {scopes}")
        fs_div = scopes.pop()
        profiles.append({
            "corp_code": corp_code, "industry_profile": PROFILE_BY_INDEX[index_code],
            "preferred_fs_div": fs_div, "fallback_fs_div": None,
            "comparison_scope": "consolidated" if fs_div == "CFS" else "separate",
            "capex_applicable": True, "valid_from": "2023-01-01", "valid_to": None,
        })
    return profiles


def apply_industry_kpis(
    connection: sqlite3.Connection,
    years: set[int] | None = None,
    persist_profiles: bool = True,
) -> dict:
    before = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    with connection:
        initialize_financial_metric_schema(connection, include_general=True)
        profiles = general_company_profiles(connection)
        upsert_company_profiles(connection, profiles)
        added = add_missing_fact_metrics(connection, years=years)
        if not persist_profiles and profiles:
            connection.executemany(
                "DELETE FROM company_financial_profiles WHERE corp_code=?",
                [(profile["corp_code"],) for profile in profiles],
            )
    after = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    return {"profiles_added": len(profiles) if persist_profiles else 0,
            "transient_profiles_used": len(profiles) if not persist_profiles else 0,
            "base_rows_added": added,
            "metric_values_before": before, "metric_values_after": after}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true")
    parser.add_argument("--years", nargs="+", type=int)
    parser.add_argument("--transient-profiles", action="store_true")
    args = parser.parse_args()
    if args.target.resolve() == FINANCIAL_DB_PATH.resolve():
        raise ValueError("운영 DB를 직접 대상으로 실행할 수 없습니다. staging 경로를 지정하세요.")
    if not args.apply_only:
        if args.target.exists():
            raise FileExistsError(f"이미 존재하는 staging DB입니다: {args.target}")
        source_hash = _sha256(args.source)
        args.target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)) as src, \
                closing(sqlite3.connect(args.target)) as dst:
            src.backup(dst)
        if _sha256(args.source) != source_hash:
            raise RuntimeError("스냅샷 중 source DB가 변경됐습니다.")
        print(f"source sha256 {source_hash}")
    with closing(sqlite3.connect(args.target)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        print(json.dumps(apply_industry_kpis(
            connection,
            years=set(args.years) if args.years else None,
            persist_profiles=not args.transient_profiles,
        ), ensure_ascii=False))


if __name__ == "__main__":
    main()
