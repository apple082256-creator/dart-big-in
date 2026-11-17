"""Recompute calculated quarters from stored API cumulatives and add missing ones.

The stored raw (is_calculated=0) facts are fed back through
``period_normalizer._add_calculated_quarterly_values``.  Rows the original
ord-exact pass produces must equal the stored calculated quarters (regression);
rows found only by account-id matching are the quarters that were skipped
because a line's display order changed between reports, and only those are
inserted.  API values are never modified.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from src.preprocess.period_normalizer import _add_calculated_quarterly_values

FACT_KEY = ("corp_code", "period_id", "fs_div", "sj_div", "account_key", "ord", "value_type", "is_calculated")
FACT_COLUMNS = (
    "corp_code", "period_id", "fs_div", "sj_div", "account_key", "account_id",
    "original_account_nm", "standard_account_nm", "period_type", "value_type",
    "period_start", "period_end", "currency", "source_value_field", "raw_value",
    "normalized_value", "calculated_value", "calculation_method", "source_values",
    "is_calculated", "ord", "rcept_no",
)


def _raw_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    frame = pd.read_sql_query(
        """SELECT f.*, p.year, p.report_type FROM financial_facts f JOIN periods p USING(period_id)
           WHERE f.is_calculated=0""",
        connection,
    )
    frame["ord"] = frame["ord"].astype("Int64")
    return frame


def _key(row: dict) -> tuple:
    ord_value = row["ord"]
    ord_value = None if ord_value is None or pd.isna(ord_value) else int(ord_value)
    return (row["corp_code"], int(row["period_id"]), row["fs_div"], row["sj_div"], row["account_key"],
            ord_value, row["value_type"], int(bool(row["is_calculated"])))


def compute_quarters(connection: sqlite3.Connection) -> dict[str, object]:
    """Regression of stored quarters plus the account-id matched additions."""
    frame = _raw_frame(connection)
    result = _add_calculated_quarterly_values(frame)
    generated = result.iloc[len(frame):].to_dict("records")
    stored = {
        _key(row): row for row in pd.read_sql_query(
            "SELECT * FROM financial_facts WHERE is_calculated=1", connection).to_dict("records")
    }
    ord_pass, account_pass = [], []
    for row in generated:
        match = json.loads(row["source_values"]).get("match")
        (account_pass if match in ("account_id", "verified_evidence") else ord_pass).append(row)
    regenerated = {_key(row): row for row in ord_pass + account_pass}
    value_mismatch = [
        {"key": list(key), "stored": stored[key]["calculated_value"], "recomputed": row["calculated_value"]}
        for key, row in regenerated.items() if key in stored and stored[key]["calculated_value"] != row["calculated_value"]
    ]
    # Same value, different formula text or inputs record.
    text_mismatch = [
        {"key": list(key), "fact_id": stored[key]["fact_id"],
         "stored": [stored[key]["calculation_method"], stored[key]["source_values"]],
         "recomputed": [row["calculation_method"], row["source_values"]]}
        for key, row in regenerated.items()
        if key in stored and stored[key]["calculated_value"] == row["calculated_value"]
        and (stored[key]["calculation_method"], stored[key]["source_values"])
        != (row["calculation_method"], row["source_values"])
    ]
    return {
        "stored_quarters": len(stored),
        "text_mismatch": text_mismatch,
        "ord_pass_quarters": len(ord_pass),
        "missing_from_regeneration": [list(k) for k in stored.keys() - regenerated.keys()],
        "not_stored_ord_pass": [list(k) for k in {_key(row) for row in ord_pass} - stored.keys()],
        "value_mismatch": value_mismatch,
        "account_id_matches": account_pass,
        "already_stored_account_matches": [r for r in account_pass if _key(r) in stored],
        "review": result.attrs.get("quarter_match_review", []),
    }


def insert_account_matched_quarters(connection: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert account-id matched quarters; existing natural keys are left untouched."""
    values = []
    for row in rows:
        record = {column: row.get(column) for column in FACT_COLUMNS}
        record["is_calculated"] = 1
        record["ord"] = None if pd.isna(record["ord"]) else int(record["ord"])
        record["period_id"] = int(record["period_id"])
        values.append(tuple(None if (isinstance(v, float) and pd.isna(v)) else v for v in record.values()))
    before = connection.total_changes
    connection.executemany(
        f"""INSERT INTO financial_facts({",".join(FACT_COLUMNS)})
            VALUES ({",".join("?" for _ in FACT_COLUMNS)})
            ON CONFLICT({",".join(FACT_KEY)}) DO NOTHING""",
        values,
    )
    return connection.total_changes - before
