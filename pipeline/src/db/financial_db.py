from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.db.krx_index_db import initialize_krx_schema


def _sqlite_scalar(value: object) -> object:
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _sqlite_rows(frame: pd.DataFrame, columns: list[str]) -> list[tuple]:
    return [
        tuple(_sqlite_scalar(value) for value in row)
        for row in frame[columns].itertuples(index=False, name=None)
    ]


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS companies (
    corp_code TEXT PRIMARY KEY, company_name TEXT NOT NULL, stock_code TEXT
);
CREATE TABLE IF NOT EXISTS periods (
    period_id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL, report_type TEXT NOT NULL, report_code TEXT NOT NULL,
    report_name TEXT NOT NULL, UNIQUE(year, report_type)
);
CREATE TABLE IF NOT EXISTS account_mapping (
    account_key TEXT NOT NULL, sj_div TEXT NOT NULL, account_id TEXT,
    standard_account_name TEXT NOT NULL, source_account_names TEXT,
    company_count INTEGER, observation_count INTEGER, mapping_status TEXT NOT NULL,
    PRIMARY KEY(account_key, sj_div)
);
CREATE TABLE IF NOT EXISTS financial_facts (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code TEXT NOT NULL, period_id INTEGER NOT NULL, fs_div TEXT NOT NULL,
    sj_div TEXT NOT NULL, account_key TEXT NOT NULL, account_id TEXT,
    original_account_nm TEXT NOT NULL, standard_account_nm TEXT,
    period_type TEXT NOT NULL, value_type TEXT NOT NULL,
    period_start TEXT, period_end TEXT NOT NULL, currency TEXT,
    source_value_field TEXT NOT NULL, raw_value TEXT,
    normalized_value INTEGER, calculated_value INTEGER,
    calculation_method TEXT, source_values TEXT,
    is_calculated INTEGER NOT NULL CHECK(is_calculated IN (0, 1)),
    ord INTEGER, rcept_no TEXT,
    UNIQUE(corp_code, period_id, fs_div, sj_div, account_key, ord, value_type, is_calculated),
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code),
    FOREIGN KEY(period_id) REFERENCES periods(period_id)
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL,
    company_count INTEGER NOT NULL, fact_count INTEGER NOT NULL
);
"""


def initialize_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='financial_facts'"
    ).fetchone()
    if existing:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(financial_facts)")}
        if "period_type" not in columns:
            connection.execute("ALTER TABLE financial_facts RENAME TO financial_facts_legacy_v1")
            connection.execute("DROP INDEX IF EXISTS idx_facts_account")
            connection.execute("DROP INDEX IF EXISTS idx_facts_company_period")
    period_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(periods)")
    }
    if period_columns and "report_type" not in period_columns:
        connection.execute("ALTER TABLE periods RENAME TO periods_legacy_v1")
    connection.executescript(SCHEMA)
    initialize_krx_schema(connection)
    from src.db.financial_metrics import initialize_financial_metric_schema
    initialize_financial_metric_schema(connection)
    fact_columns = {row[1] for row in connection.execute("PRAGMA table_info(financial_facts)")}
    if "source_values" not in fact_columns:
        connection.execute("ALTER TABLE financial_facts ADD COLUMN source_values TEXT")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_facts_account ON financial_facts(account_key, sj_div);
        CREATE INDEX IF NOT EXISTS idx_facts_company_period ON financial_facts(corp_code, period_id);
        CREATE INDEX IF NOT EXISTS idx_facts_semantics ON financial_facts(period_type, value_type, is_calculated);
        """
    )
    return connection


def save_financial_data(path: Path, frame: pd.DataFrame, mapping: pd.DataFrame) -> None:
    with closing(initialize_database(path)) as connection, connection:
        companies = frame[["corp_code", "corp_name", "stock_code"]].drop_duplicates("corp_code")
        connection.executemany(
            "INSERT INTO companies VALUES (?, ?, ?) ON CONFLICT(corp_code) DO UPDATE SET company_name=excluded.company_name, stock_code=excluded.stock_code",
            _sqlite_rows(companies, ["corp_code", "corp_name", "stock_code"]),
        )
        periods = frame[["year", "report_type", "report_code", "report_name"]].drop_duplicates()
        connection.executemany(
            "INSERT INTO periods(year, report_type, report_code, report_name) VALUES (?, ?, ?, ?) ON CONFLICT(year, report_type) DO UPDATE SET report_code=excluded.report_code, report_name=excluded.report_name",
            _sqlite_rows(periods, ["year", "report_type", "report_code", "report_name"]),
        )
        mapping_rows = mapping.astype(object).where(pd.notna(mapping), None)
        connection.executemany(
            """INSERT INTO account_mapping VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_key, sj_div) DO UPDATE SET
            account_id=excluded.account_id, standard_account_name=excluded.standard_account_name,
            source_account_names=excluded.source_account_names, company_count=excluded.company_count,
            observation_count=excluded.observation_count, mapping_status=excluded.mapping_status""",
            _sqlite_rows(
                mapping_rows,
                ["account_key", "sj_div", "account_id", "standard_account_name", "source_account_names", "company_count", "observation_count", "mapping_status"],
            ),
        )
        period_ids = pd.read_sql_query("SELECT period_id, year, report_type FROM periods", connection)
        period_ids["year"] = pd.to_numeric(period_ids["year"], errors="raise").astype("Int64")
        if frame["year"].dtype != period_ids["year"].dtype:
            frame = frame.copy()
            frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype("Int64")
        facts = frame.merge(period_ids, on=["year", "report_type"], how="left", validate="many_to_one")
        fact_columns = [
            "corp_code", "period_id", "fs_div", "sj_div", "account_key", "account_id",
            "original_account_nm", "standard_account_nm", "period_type", "value_type",
            "period_start", "period_end", "currency", "source_value_field", "raw_value",
            "normalized_value", "calculated_value", "calculation_method", "source_values",
            "is_calculated", "ord", "rcept_no",
        ]
        facts["is_calculated"] = facts["is_calculated"].astype(int)
        facts = facts[fact_columns].astype(object).where(pd.notna(facts[fact_columns]), None)
        connection.executemany(
            """INSERT INTO financial_facts(
            corp_code, period_id, fs_div, sj_div, account_key, account_id,
            original_account_nm, standard_account_nm, period_type, value_type,
            period_start, period_end, currency, source_value_field, raw_value,
            normalized_value, calculated_value, calculation_method, source_values,
            is_calculated, ord, rcept_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(corp_code, period_id, fs_div, sj_div, account_key, ord, value_type, is_calculated)
            DO UPDATE SET original_account_nm=excluded.original_account_nm,
            standard_account_nm=excluded.standard_account_nm, raw_value=excluded.raw_value,
            normalized_value=excluded.normalized_value, calculated_value=excluded.calculated_value,
            calculation_method=excluded.calculation_method, source_values=excluded.source_values,
            rcept_no=excluded.rcept_no""",
            _sqlite_rows(facts, fact_columns),
        )
        connection.execute(
            "INSERT INTO pipeline_runs(run_at, company_count, fact_count) VALUES (?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), frame["corp_code"].nunique(), len(frame)),
        )
