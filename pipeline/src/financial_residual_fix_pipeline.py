"""Stage the residual financial fixes on a snapshot of the operational DB.

1. Calculated quarters skipped because a line's display order (ord) changed
   between reports are added, matched by official account id (financial_facts,
   is_calculated=1); stored quarters are regenerated first as a regression gate.
2. Bank metric slots left empty by those quarters are filled (DERIVED).
3. Loans shown combined with deposits and other financial assets are split out
   from the filing notes into LOANS_AT_AMORTISED_COST_NOTE.

Re-running on the same target changes nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.bank_xml_backfill_pipeline import apply_combined_loan_notes
from src.config import FINANCIAL_DB_PATH
from src.db.financial_metrics import add_missing_fact_metrics
from src.db.quarter_backfill import compute_quarters, insert_account_matched_quarters


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def apply_residual_fixes(connection: sqlite3.Connection) -> dict:
    quarters = compute_quarters(connection)
    if quarters["missing_from_regeneration"] or quarters["not_stored_ord_pass"] or quarters["value_mismatch"]:
        raise RuntimeError("저장된 분기 계산값이 재현되지 않아 중단합니다.")
    with connection:
        inserted = insert_account_matched_quarters(connection, quarters["account_id_matches"])
        metrics = add_missing_fact_metrics(connection)
    loans = apply_combined_loan_notes(connection)
    return {
        "stored_quarters_regenerated": quarters["stored_quarters"],
        "account_id_matches": len(quarters["account_id_matches"]),
        "facts_inserted": inserted,
        "quarter_review": len(quarters["review"]),
        "metric_rows_added": metrics,
        "note_loan_rows": loans["rows"],
        "note_loan_summaries": loans["summaries"],
        "quarter_review_items": quarters["review"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true")
    parser.add_argument("--report", type=Path)
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
        result = apply_residual_fixes(connection)
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print({k: v for k, v in result.items() if not k.endswith(("summaries", "items"))})


if __name__ == "__main__":
    main()
