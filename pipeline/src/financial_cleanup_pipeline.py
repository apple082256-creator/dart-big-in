"""Stage the 2023-2025 Financial DB cleanup (v10) on a snapshot of the operational DB.

1. Calculated "quarterly" rows on cash flow statement balance lines (opening /
   closing cash) are removed: a balance has no three-month value, and the
   quarter normalizer no longer derives them.  Removed rows are written to the
   report first.
2. Held-back quarters verified against the filed statements
   (``verified_quarter_manifest.json``) are added (financial_facts,
   is_calculated=1); API rows are never modified.
3. Metric slots left empty by those quarters are filled; existing rows stay.

Stored quarters are regenerated first as a regression gate.  Re-running on the
same target changes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.financial_metrics import _fact_key, add_missing_fact_metrics
from src.db.quarter_backfill import FACT_KEY, compute_quarters, insert_account_matched_quarters
from src.preprocess.period_normalizer import is_cf_balance_line


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_cleanup(connection: sqlite3.Connection) -> dict:
    quarters = compute_quarters(connection)
    if quarters["value_mismatch"] or quarters["not_stored_ord_pass"]:
        raise RuntimeError("저장된 분기 계산값이 재현되지 않아 중단합니다.")
    connection.row_factory = sqlite3.Row
    where = " AND ".join(f"{column} IS ?" for column in FACT_KEY)
    balance_rows = []
    for key in quarters["missing_from_regeneration"]:
        row = connection.execute(f"SELECT * FROM financial_facts WHERE {where}", key).fetchone()
        if row is None or row["is_calculated"] != 1 or row["sj_div"] != "CF" \
                or not is_cf_balance_line(row["account_id"], row["original_account_nm"]):
            raise RuntimeError(f"재생성되지 않는 저장 분기가 잔액 줄이 아닙니다: {key}")
        balance_rows.append(dict(row))
    keys = set()
    for row in balance_rows:
        year, report = connection.execute("SELECT year, report_type FROM periods WHERE period_id=?",
                                          (row["period_id"],)).fetchone()
        keys.add(_fact_key({**row, "year": year, "report_type": report}))
    referenced = sum(1 for (key,) in connection.execute("SELECT source_fact_key FROM financial_metric_values")
                     if key in keys)
    if referenced:
        raise RuntimeError(f"삭제 대상 잔액 줄을 원천으로 쓰는 지표가 {referenced}행 있습니다.")
    new_quarters = [r for r in quarters["account_id_matches"] if r not in quarters["already_stored_account_matches"]]
    with connection:
        removed = 0
        for row in balance_rows:
            removed += connection.execute(
                "DELETE FROM financial_facts WHERE fact_id=? AND is_calculated=1 AND sj_div='CF'",
                (row["fact_id"],)).rowcount
        inserted = insert_account_matched_quarters(connection, new_quarters)
        metrics = add_missing_fact_metrics(connection)
    connection.row_factory = None
    return {
        "stored_quarters_before": quarters["stored_quarters"],
        "balance_line_rows_removed": removed,
        "verified_quarters_inserted": inserted,
        "metric_rows_added": metrics,
        "quarter_review_left": len(quarters["review"]),
        "removed_rows": balance_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true")
    parser.add_argument("--report", type=Path)
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
        result = apply_cleanup(connection)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print({k: v for k, v in result.items() if k != "removed_rows"})


if __name__ == "__main__":
    main()
