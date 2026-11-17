"""Read-only access to industry KPIs (optional layer next to the full accounts).

The full statement accounts stay available through
``financial_retriever.get_financial_context``; this module only exposes the
standardized KPI rows with their provenance.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.industry_kpi_config import KPI_SET, PROFILE_BY_INDEX


def _connect(path: Path | None) -> sqlite3.Connection:
    database = (path or FINANCIAL_DB_PATH).resolve()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _industry_profile(connection: sqlite3.Connection, corp_code: str) -> str | None:
    """Stored profile, else the KRX group's profile.

    KRX-grouped companies without a stored profile get their KPI rows from
    ``industry_kpi_pipeline --transient-profiles``, which assigns the profile
    from the group in the same way.
    """
    row = connection.execute(
        "SELECT industry_profile FROM company_financial_profiles WHERE corp_code=?", (corp_code,)
    ).fetchone()
    if row is not None:
        return row["industry_profile"]
    row = connection.execute(
        "SELECT index_code FROM krx_index_constituents WHERE corp_code=? AND is_current=1", (corp_code,)
    ).fetchone()
    return None if row is None else PROFILE_BY_INDEX.get(row["index_code"])


def get_industry_kpis(
    corp_code: str,
    year: int | None = None,
    report_type: str | None = None,
    metric_codes: tuple[str, ...] | None = None,
    value_type: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    """KPI rows of one company, limited to its industry's KPI set.

    Each row carries value, unit (KRW or BPS = 0.01 %), value_type, fs_div,
    source_kind, review_status, calculation_method and source references.
    """
    with closing(_connect(path)) as connection:
        industry = _industry_profile(connection, corp_code)
        if industry is None:
            return []
        codes = KPI_SET.get(industry)
        clauses, params = ["v.corp_code=?"], [corp_code]
        if codes is not None:
            wanted = [c for c in codes if metric_codes is None or c in metric_codes]
            clauses.append("v.metric_code IN (" + ",".join("?" for _ in wanted) + ")")
            params += wanted
        elif metric_codes:
            clauses.append("v.metric_code IN (" + ",".join("?" for _ in metric_codes) + ")")
            params += list(metric_codes)
        for column, value in (("v.year", year), ("v.report_type", report_type), ("v.value_type", value_type)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        rows = connection.execute(
            f"""SELECT c.company_name, ? AS industry_profile, v.*, d.metric_name, d.metric_type,
                       d.statement_type, d.period_nature
                FROM financial_metric_values v
                JOIN companies c USING(corp_code)
                JOIN financial_metric_definitions d USING(metric_code)
                WHERE {' AND '.join(clauses)}
                ORDER BY v.year, CASE v.report_type WHEN 'Q1' THEN 1 WHEN 'H1' THEN 2 WHEN 'Q3' THEN 3 ELSE 4 END,
                         v.metric_code, v.value_type""",
            [industry, *params],
        ).fetchall()
    return [dict(row) for row in rows]


def get_industry_kpis_by_index(
    index_code: str,
    year: int | None = None,
    report_type: str | None = None,
    metric_codes: tuple[str, ...] | None = None,
    value_type: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    """KPI rows of the current constituents of one KRX group (e.g. KRX_SEMI)."""
    with closing(_connect(path)) as connection:
        members = [row[0] for row in connection.execute(
            "SELECT corp_code FROM krx_index_constituents WHERE index_code=? AND is_current=1 ORDER BY corp_code",
            (index_code,))]
    rows: list[dict] = []
    for corp_code in members:
        rows += get_industry_kpis(corp_code, year, report_type, metric_codes, value_type, path)
    return rows


BANK_PROFILES = ("BANK_HOLDING", "INTERNET_BANK")


def is_bank_company(corp_name: str, path: Path | None = None) -> bool:
    """True for companies whose metrics follow the bank layer (used to keep the
    analyzer's existing behaviour: bank metrics first, full accounts otherwise)."""
    with closing(_connect(path)) as connection:
        row = connection.execute(
            """SELECT p.industry_profile FROM company_financial_profiles p JOIN companies c USING(corp_code)
               WHERE c.company_name=?""",
            (corp_name,),
        ).fetchone()
    return row is not None and row["industry_profile"] in BANK_PROFILES


def kpi_coverage(path: Path | None = None) -> dict[str, dict]:
    """Per company and KPI: report periods with a value, out of the periods with facts."""
    with closing(_connect(path)) as connection:
        companies = connection.execute(
            """SELECT p.corp_code, c.company_name, p.industry_profile
               FROM company_financial_profiles p JOIN companies c USING(corp_code)
               WHERE p.industry_profile IN (""" + ",".join("?" for _ in KPI_SET) + ")",
            list(KPI_SET),
        ).fetchall()
        coverage = {}
        for company in companies:
            periods = {tuple(r) for r in connection.execute(
                """SELECT DISTINCT p.year, p.report_type FROM financial_facts f JOIN periods p USING(period_id)
                   WHERE f.corp_code=?""", (company["corp_code"],))}
            per_metric = {}
            for metric in KPI_SET[company["industry_profile"]]:
                have = {tuple(r) for r in connection.execute(
                    "SELECT DISTINCT year, report_type FROM financial_metric_values WHERE corp_code=? AND metric_code=?",
                    (company["corp_code"], metric))}
                per_metric[metric] = {"periods": len(have & periods), "of": len(periods),
                                      "missing": sorted(f"{y} {r}" for y, r in periods - have)}
            coverage[company["company_name"]] = {"industry_profile": company["industry_profile"],
                                                 "metrics": per_metric}
    return coverage
