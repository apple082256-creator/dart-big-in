from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.krx_index_db import get_companies_by_index


CAPEX_ALIASES = {"CAPEX", "설비투자", "유형자산취득(CAPEX)", "유형자산의 취득"}
REPORT_TYPE_ALIASES = {"HY": "H1", "HALF": "H1", "1H": "H1"}


def _preferred_fs_div(corp_name: str, path: Path, requested: str) -> str:
    if requested != "AUTO":
        return requested
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_financial_profiles'"
        ).fetchone()
        if exists:
            row = connection.execute(
                """SELECT p.preferred_fs_div
                   FROM company_financial_profiles p JOIN companies c USING(corp_code)
                   WHERE c.company_name=?""",
                (corp_name,),
            ).fetchone()
            if row:
                return str(row[0])
    return "CFS"


def _database_path(path: Path | None) -> Path:
    return path if path is not None else FINANCIAL_DB_PATH


def _is_capex_query(account_name: str | None) -> bool:
    return bool(account_name and any(alias.casefold() in account_name.casefold() for alias in CAPEX_ALIASES))


def _get_capex_context(
    connection: sqlite3.Connection,
    corp_name: str,
    year: int,
    report_type: str,
    fs_div: str,
) -> list[dict]:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capex_facts'"
    ).fetchone()
    if not table_exists:
        return []
    rows = connection.execute(
        """
        SELECT c.company_name AS corp_name, cf.corp_code, p.year, p.report_type,
               cf.rcept_no, cf.sj_div, cf.account_id, cf.original_account_nm,
               cf.standard_account_nm, cf.period_type, cf.value_type,
               cf.period_start, cf.period_end, cf.currency, cf.raw_value,
               cf.cash_outflow_amount AS normalized_value, cf.calculated_value,
               cf.calculation_method, cf.source_values, cf.is_calculated, cf.fs_div,
               cf.cash_outflow_amount, cf.sign_normalized, cf.is_derived,
               cf.value_source, cf.capex_method, cf.source_account_ids,
               cf.source_account_names, cf.source_account_count,
               cf.direct_account_found, cf.review_required, cf.note,
               cf.normalized_value AS signed_normalized_value
        FROM capex_facts cf
        JOIN companies c USING(corp_code)
        JOIN periods p USING(period_id)
        WHERE c.company_name=? AND p.year=? AND p.report_type=? AND cf.fs_div=?
        ORDER BY CASE cf.value_type
                   WHEN 'quarterly' THEN 1 WHEN 'cumulative' THEN 2 WHEN 'annual' THEN 3 ELSE 4
                 END
        """,
        (corp_name, year, report_type, fs_div),
    ).fetchall()
    return [dict(row) for row in rows]


def get_financial_context(
    corp_name: str,
    year: int,
    report_type: str,
    account_name: str | None = None,
    fs_div: str = "AUTO",
    path: Path | None = None,
) -> list[dict]:
    report_type = REPORT_TYPE_ALIASES.get(report_type.upper(), report_type.upper())
    database_path = _database_path(path)
    fs_div = _preferred_fs_div(corp_name, database_path, fs_div)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        if _is_capex_query(account_name):
            return _get_capex_context(connection, corp_name, year, report_type, fs_div)

    clauses = ["c.company_name = ?", "p.year = ?", "p.report_type = ?", "f.fs_div = ?"]
    params: list[object] = [corp_name, year, report_type, fs_div]
    if account_name:
        clauses.append("(f.standard_account_nm LIKE ? OR f.original_account_nm LIKE ?)")
        params.extend([f"%{account_name}%", f"%{account_name}%"])
    sql = f"""
    SELECT c.company_name AS corp_name, f.corp_code, p.year, p.report_type,
           f.rcept_no, f.sj_div, f.account_id, f.original_account_nm,
           f.standard_account_nm, f.period_type, f.value_type,
           f.period_start, f.period_end, f.currency, f.raw_value,
           f.normalized_value, f.calculated_value, f.calculation_method,
           f.source_values, f.is_calculated, f.fs_div
    FROM financial_facts f
    JOIN companies c USING(corp_code)
    JOIN periods p USING(period_id)
    WHERE {' AND '.join(clauses)}
    ORDER BY f.sj_div, f.ord, f.value_type
    """
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def get_financial_context_by_corp_codes(
    corp_codes: list[str] | tuple[str, ...],
    year: int,
    report_type: str,
    account_name: str | None = None,
    fs_div: str = "AUTO",
    path: Path | None = None,
) -> list[dict]:
    """Return the same evidence shape for an index-scoped company set."""
    codes = tuple(dict.fromkeys(str(code).strip() for code in corp_codes if str(code).strip()))
    if not codes:
        return []
    report_type = REPORT_TYPE_ALIASES.get(report_type.upper(), report_type.upper())
    database_path = _database_path(path)
    if fs_div == "AUTO":
        with closing(sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            names = {
                row["corp_code"]: row["company_name"]
                for row in connection.execute(
                    "SELECT corp_code,company_name FROM companies WHERE corp_code IN ("
                    + ",".join("?" for _ in codes) + ")",
                    codes,
                )
            }
        rows: list[dict] = []
        for code in codes:
            name = names.get(code)
            if name:
                rows.extend(get_financial_context(
                    name, year, report_type, account_name=account_name,
                    fs_div="AUTO", path=database_path,
                ))
        return rows
    placeholders = ",".join("?" for _ in codes)
    clauses = [
        f"f.corp_code IN ({placeholders})",
        "p.year = ?", "p.report_type = ?", "f.fs_div = ?",
    ]
    params: list[object] = [*codes, year, report_type, fs_div]
    if account_name:
        clauses.append("(f.standard_account_nm LIKE ? OR f.original_account_nm LIKE ?)")
        params.extend([f"%{account_name}%", f"%{account_name}%"])
    table = "capex_facts" if _is_capex_query(account_name) else "financial_facts"
    if table == "capex_facts":
        value_columns = """cf.cash_outflow_amount AS normalized_value, cf.calculated_value,
                            cf.calculation_method, cf.source_values, cf.is_calculated, cf.fs_div,
                            cf.cash_outflow_amount, cf.sign_normalized, cf.is_derived,
                            cf.value_source, cf.capex_method, cf.source_account_ids,
                            cf.source_account_names, cf.source_account_count,
                            cf.direct_account_found, cf.review_required, cf.note,
                            cf.normalized_value AS signed_normalized_value"""
        sql = f"""SELECT c.company_name AS corp_name, cf.corp_code, p.year, p.report_type,
                          cf.rcept_no, cf.sj_div, cf.account_id, cf.original_account_nm,
                          cf.standard_account_nm, cf.period_type, cf.value_type,
                          cf.period_start, cf.period_end, cf.currency, cf.raw_value, {value_columns}
                   FROM capex_facts cf JOIN companies c USING(corp_code) JOIN periods p USING(period_id)
                   WHERE cf.corp_code IN ({placeholders}) AND p.year=? AND p.report_type=? AND cf.fs_div=?
                   ORDER BY c.company_name, cf.value_type"""
        params = [*codes, year, report_type, fs_div]
    else:
        sql = f"""SELECT c.company_name AS corp_name, f.corp_code, p.year, p.report_type,
                          f.rcept_no, f.sj_div, f.account_id, f.original_account_nm,
                          f.standard_account_nm, f.period_type, f.value_type,
                          f.period_start, f.period_end, f.currency, f.raw_value,
                          f.normalized_value, f.calculated_value, f.calculation_method,
                          f.source_values, f.is_calculated, f.fs_div
                   FROM financial_facts f JOIN companies c USING(corp_code) JOIN periods p USING(period_id)
                   WHERE {' AND '.join(clauses)}
                   ORDER BY c.company_name, f.sj_div, f.ord, f.value_type"""
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def get_financial_context_by_index(
    index_code: str,
    year: int,
    report_type: str,
    account_name: str | None = None,
    fs_div: str = "AUTO",
    as_of: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    database_path = _database_path(path)
    companies = get_companies_by_index(index_code, as_of=as_of, path=database_path)
    return get_financial_context_by_corp_codes(
        [row["corp_code"] for row in companies],
        year,
        report_type,
        account_name=account_name,
        fs_div=fs_div,
        path=database_path,
    )


def get_financial_metrics(
    corp_name: str,
    year: int,
    report_type: str,
    metric_code: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Return finance-specific direct/derived metrics with provenance."""
    database_path = _database_path(path)
    report_type = REPORT_TYPE_ALIASES.get(report_type.upper(), report_type.upper())
    clauses = ["c.company_name=?", "v.year=?", "v.report_type=?"]
    params: list[object] = [corp_name, year, report_type]
    if metric_code:
        clauses.append("v.metric_code=?")
        params.append(metric_code)
    with closing(sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='financial_metric_values'"
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in connection.execute(
            f"""SELECT c.company_name AS corp_name,v.*,d.metric_name,d.metric_type,
                       d.statement_type,d.period_nature
                FROM financial_metric_values v
                JOIN companies c USING(corp_code)
                JOIN financial_metric_definitions d USING(metric_code)
                WHERE {' AND '.join(clauses)}
                ORDER BY d.is_core DESC,v.metric_code,v.value_type,v.source_kind""",
            params,
        )]
