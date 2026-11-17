from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

from src.config import FINANCIAL_FINAL_DB_PATH, MAPPING_DIR, PROCESSED_DIR
from src.db.capex_db import materialize_capex_tables
from src.db.financial_db import save_financial_data
from src.db.krx_index_db import copy_krx_index_layer


FINAL_CAPEX_VALIDATION_PATH = PROCESSED_DIR / "quality" / "capex_final_validation.csv"


def build_final_database(
    frame: pd.DataFrame,
    mapping: pd.DataFrame,
    target_path: Path = FINANCIAL_FINAL_DB_PATH,
    capex_validation_path: Path = FINAL_CAPEX_VALIDATION_PATH,
) -> dict:
    """Build a fresh final DB from pipeline facts, then materialize verified CAPEX."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(".building.db")
    if temporary_path.exists():
        temporary_path.unlink()

    save_financial_data(temporary_path, frame, mapping)
    if target_path.exists():
        copy_krx_index_layer(target_path, temporary_path)
    capex_result = materialize_capex_tables(
        temporary_path,
        validation_path=capex_validation_path,
        source_label="main pipeline normalized financial facts",
    )
    with closing(sqlite3.connect(temporary_path)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        base_fact_count = connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0]
        capex_fact_count = connection.execute("SELECT COUNT(*) FROM capex_facts").fetchone()[0]
        view_count = connection.execute("SELECT COUNT(*) FROM financial_facts_final").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"최종 Financial DB 무결성 검사 실패: {integrity}")
    temporary_path.replace(target_path)
    return {
        "target_path": str(target_path),
        "integrity": integrity,
        "base_fact_count": base_fact_count,
        "capex_fact_count": capex_fact_count,
        "unified_view_count": view_count,
        "capex_base_records": capex_result["base_records"],
        "capex_quarterly_records": capex_result["quarterly_records"],
        "capex_validation_path": str(capex_validation_path),
    }


def load_processed_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    facts_path = PROCESSED_DIR / "financial" / "financial_facts.csv"
    mapping_path = MAPPING_DIR / "account_mapping_draft.csv"
    string_columns = {
        "corp_code": "string",
        "stock_code": "string",
        "report_code": "string",
        "account_key": "string",
        "account_id": "string",
        "rcept_no": "string",
        "raw_value": "string",
    }
    frame = pd.read_csv(facts_path, dtype=string_columns)
    frame = frame.drop(columns=["Index"], errors="ignore")
    for column in ("year", "ord", "normalized_value", "calculated_value"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    if frame["is_calculated"].dtype != bool:
        frame["is_calculated"] = frame["is_calculated"].astype(str).str.lower().eq("true")

    mapping = pd.read_csv(
        mapping_path,
        dtype={"account_key": "string", "account_id": "string"},
    )
    return frame, mapping


def build_final_from_processed() -> dict:
    frame, mapping = load_processed_inputs()
    return build_final_database(frame, mapping)


if __name__ == "__main__":
    print(build_final_from_processed())
