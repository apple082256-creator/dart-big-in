from __future__ import annotations

import csv
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, PRE_PROMOTION_FINANCIAL_DB_PATH, PROCESSED_DIR
from src.db.capex_db import DIRECT_CAPEX_IDS, STANDARD_CAPEX_NAME


OUTPUT_PATH = PROCESSED_DIR / "quality" / "financial_final_regression.csv"
FACT_COLUMNS = (
    "corp_name", "year", "report_type", "corp_code", "fs_div", "sj_div",
    "account_key", "account_id", "original_account_nm", "standard_account_nm",
    "period_type", "value_type", "period_start", "period_end", "currency",
    "source_value_field", "raw_value", "normalized_value", "calculated_value",
    "calculation_method", "source_values", "is_calculated", "ord", "rcept_no",
)
REQUIRED_FINAL_VIEW_COLUMNS = {
    "corp_name", "corp_code", "year", "report_type", "rcept_no", "account_id",
    "original_account_nm", "standard_account_nm", "sj_div", "fs_div", "value_type",
    "period_type", "raw_value", "normalized_value", "calculated_value",
    "is_calculated", "calculation_method", "currency", "value_source", "is_derived",
    "source_account_ids", "source_account_names", "source_values",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _base_rows(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute(
        """
        SELECT c.company_name AS corp_name, p.year, p.report_type, f.corp_code,
               f.fs_div, f.sj_div, f.account_key, f.account_id,
               f.original_account_nm, f.standard_account_nm, f.period_type,
               f.value_type, f.period_start, f.period_end, f.currency,
               f.source_value_field, f.raw_value, f.normalized_value,
               f.calculated_value, f.calculation_method, f.source_values,
               f.is_calculated, f.ord, f.rcept_no
        FROM financial_facts f
        JOIN companies c USING(corp_code)
        JOIN periods p USING(period_id)
        ORDER BY c.company_name, p.year, p.report_type, f.fs_div, f.sj_div,
                 f.account_key, f.ord, f.value_type, f.is_calculated
        """
    )]


def _used_value(row: dict) -> int | None:
    return row["calculated_value"] if row["is_calculated"] else row["normalized_value"]


def run_final_regression(
    old_path: Path = PRE_PROMOTION_FINANCIAL_DB_PATH,
    final_path: Path = FINANCIAL_DB_PATH,
    output_path: Path = OUTPUT_PATH,
) -> dict:
    old_hash_before = _sha256(old_path)
    with closing(sqlite3.connect(old_path)) as old_connection, closing(sqlite3.connect(final_path)) as final_connection:
        old_rows = _base_rows(old_connection)
        final_rows = _base_rows(final_connection)
        base_identical = old_rows == final_rows

        view_columns = {
            row[1] for row in final_connection.execute("PRAGMA table_info(financial_facts_final)")
        }
        missing_view_columns = sorted(REQUIRED_FINAL_VIEW_COLUMNS - view_columns)
        integrity = final_connection.execute("PRAGMA integrity_check").fetchone()[0]

        old_direct: dict[tuple, int] = {}
        placeholders = ",".join("?" for _ in DIRECT_CAPEX_IDS)
        for row in old_connection.execute(
            f"""
            SELECT c.company_name, p.year, p.report_type, f.fs_div, f.value_type,
                   f.is_calculated, f.normalized_value, f.calculated_value
            FROM financial_facts f
            JOIN companies c USING(corp_code)
            JOIN periods p USING(period_id)
            WHERE f.sj_div='CF' AND f.account_id IN ({placeholders})
            """,
            tuple(DIRECT_CAPEX_IDS),
        ):
            value = row[7] if row[5] else row[6]
            if value is not None:
                old_direct[(row[0], row[1], row[2], row[3], row[4], row[5])] = abs(value)

        final_connection.row_factory = sqlite3.Row
        capex_rows = [dict(row) for row in final_connection.execute(
            """
            SELECT c.company_name AS corp_name, p.year, p.report_type, cf.*
            FROM capex_facts cf
            JOIN companies c USING(corp_code)
            JOIN periods p USING(period_id)
            ORDER BY c.company_name, p.year,
              CASE p.report_type WHEN 'Q1' THEN 1 WHEN 'H1' THEN 2 WHEN 'Q3' THEN 3 ELSE 4 END,
              cf.value_type
            """
        )]

    output_rows: list[dict] = []
    for old_row, new_row in zip(old_rows, final_rows):
        exact = all(old_row[column] == new_row[column] for column in FACT_COLUMNS)
        old_value, new_value = _used_value(old_row), _used_value(new_row)
        output_rows.append({
            "record_scope": "base_financial_fact",
            "corp_name": old_row["corp_name"], "year": old_row["year"],
            "report_type": old_row["report_type"], "standard_account_nm": old_row["standard_account_nm"],
            "sj_div": old_row["sj_div"], "fs_div": old_row["fs_div"],
            "value_type": old_row["value_type"], "value_source": "reported",
            "old_value": old_value, "new_value": new_value,
            "difference": None if old_value is None or new_value is None else new_value - old_value,
            "expected_change": False, "change_reason": "base financial fact must remain unchanged",
            "validation_status": "PASS" if exact else "FAIL",
        })

    capex_failures = 0
    for row in capex_rows:
        key = (
            row["corp_name"], row["year"], row["report_type"], row["fs_div"],
            row["value_type"], row["is_calculated"],
        )
        old_value = old_direct.get(key)
        new_value = row["cash_outflow_amount"]
        direct_matches = row["value_source"] == "direct" and (
            old_value == new_value or (old_value is None and row["is_calculated"] == 1)
        )
        valid = (
            row["standard_account_nm"] == STANDARD_CAPEX_NAME
            and (direct_matches
                 or (row["value_source"] == "derived" and old_value is None and row["source_account_count"] > 0))
        )
        capex_failures += int(not valid)
        output_rows.append({
            "record_scope": "capex_standard_fact",
            "corp_name": row["corp_name"], "year": row["year"],
            "report_type": row["report_type"], "standard_account_nm": row["standard_account_nm"],
            "sj_div": row["sj_div"], "fs_div": row["fs_div"],
            "value_type": row["value_type"], "value_source": row["value_source"],
            "old_value": old_value, "new_value": new_value,
            "difference": None if old_value is None else new_value - old_value,
            "expected_change": True,
            "change_reason": (
                ("direct CAPEX quarterly value newly calculated from cumulative reports"
                 if old_value is None else "direct CAPEX provenance record added; amount unchanged")
                if row["value_source"] == "direct"
                else "missing CAPEX derived from approved tangible-asset acquisition components"
            ),
            "validation_status": "PASS" if valid else "FAIL",
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    old_hash_after = _sha256(old_path)
    base_failures = sum(row["validation_status"] == "FAIL" for row in output_rows if row["record_scope"] == "base_financial_fact")
    return {
        "old_fact_count": len(old_rows),
        "final_base_fact_count": len(final_rows),
        "base_facts_identical": base_identical,
        "base_failure_count": base_failures,
        "capex_added_count": len(capex_rows),
        "capex_failure_count": capex_failures,
        "non_capex_change_count": base_failures,
        "missing_final_view_columns": missing_view_columns,
        "integrity": integrity,
        "old_database_unchanged": old_hash_before == old_hash_after,
        "output_path": str(output_path),
    }


if __name__ == "__main__":
    print(run_final_regression())
