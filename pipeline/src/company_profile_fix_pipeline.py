"""Stage the missing financial profile of 에이치브이엠 (separate statements only).

에이치브이엠 files only separate (OFS) statements, so AUTO queries, which fall
back to CFS without a profile, returned nothing.  The profile row is the one
the general-industry layer uses (KPI v7); it only selects the statement scope:
no metric rows are created while the KPI definitions are not loaded.

The profile is added only when every fact of the company is OFS.  Re-running
changes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.financial_metrics import upsert_company_profiles

HVM_PROFILE = {
    "corp_code": "01011368", "industry_profile": "STEEL", "preferred_fs_div": "OFS", "fallback_fs_div": None,
    "comparison_scope": "separate", "capex_applicable": True, "valid_from": "2023-01-01", "valid_to": None,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_profile_fix(connection: sqlite3.Connection) -> dict:
    corp = HVM_PROFILE["corp_code"]
    scopes = {r[0] for r in connection.execute("SELECT DISTINCT fs_div FROM financial_facts WHERE corp_code=?", (corp,))}
    if scopes != {"OFS"}:
        raise RuntimeError(f"에이치브이엠 사실값 기준이 OFS 단독이 아닙니다: {scopes}")
    existing = connection.execute("SELECT * FROM company_financial_profiles WHERE corp_code=?", (corp,)).fetchone()
    if existing is not None:
        return {"added": 0, "existing": list(existing)}
    with connection:
        upsert_company_profiles(connection, [HVM_PROFILE])
    return {"added": 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true")
    args = parser.parse_args()
    if args.target.resolve() in (FINANCIAL_DB_PATH.resolve(), args.source.resolve()):
        raise ValueError("운영 DB나 source를 직접 대상으로 실행할 수 없습니다.")
    if not args.apply_only:
        if args.target.exists():
            raise FileExistsError(args.target)
        args.target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(args.source)
        with closing(sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)) as src, \
                closing(sqlite3.connect(args.target)) as dst:
            src.backup(dst)
        print(f"source sha256 {source_hash}")
    with closing(sqlite3.connect(args.target)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        print(apply_profile_fix(connection))


if __name__ == "__main__":
    main()
