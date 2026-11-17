"""Stage the analysis sign for bank expense metrics on a snapshot of the operational DB.

Runs the regular derived-metric refresh, whose last step standardizes the
expense metrics (``src.db.expense_sign``).  ``financial_facts`` is not
touched; metric rows keep their ids.  Re-running on the same target changes
nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.financial_metrics import refresh_derived_metrics


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_expense_signs(connection: sqlite3.Connection) -> dict[str, int]:
    bank_codes = {row[0] for row in connection.execute(
        """SELECT corp_code FROM company_financial_profiles
           WHERE industry_profile IN ('BANK_HOLDING','INTERNET_BANK')""")}
    with connection:
        steps = refresh_derived_metrics(connection, corp_codes=bank_codes)
    return steps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true", help="기존 staging DB에 다시 적용한다(멱등).")
    args = parser.parse_args()
    if args.target.resolve() in (FINANCIAL_DB_PATH.resolve(), args.source.resolve()):
        raise ValueError("운영 DB나 source를 직접 대상으로 실행할 수 없습니다. staging 경로를 지정하세요.")
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
        print(apply_expense_signs(connection))


if __name__ == "__main__":
    main()
