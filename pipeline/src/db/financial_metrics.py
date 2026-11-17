from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.expense_sign import restore_expense_signs, standardize_expense_signs
from src.db.sign_convention_corrections import METRIC_CORRECTIONS
from src.db.industry_kpi_config import (
    DERIVED_KPIS,
    GENERAL_DEFINITIONS,
    GENERAL_MAPPINGS,
    PREVIOUS_POINT,
    SHARED_METRICS,
)
from src.db.verified_quarter_overrides import METRIC_QUARTERS, RESTATED_METRIC_QUARTERS


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS company_financial_profiles (
    corp_code TEXT PRIMARY KEY,
    industry_profile TEXT NOT NULL,
    preferred_fs_div TEXT NOT NULL CHECK(preferred_fs_div IN ('CFS','OFS')),
    fallback_fs_div TEXT CHECK(fallback_fs_div IN ('CFS','OFS')),
    comparison_scope TEXT NOT NULL,
    capex_applicable INTEGER NOT NULL DEFAULT 0 CHECK(capex_applicable IN (0,1)),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code)
);
CREATE TABLE IF NOT EXISTS financial_collection_status (
    corp_code TEXT NOT NULL,
    year INTEGER NOT NULL,
    report_type TEXT NOT NULL,
    requested_fs_div TEXT NOT NULL,
    selected_fs_div TEXT,
    cfs_status TEXT NOT NULL,
    ofs_status TEXT NOT NULL,
    missing_reason TEXT,
    rcept_no TEXT,
    PRIMARY KEY(corp_code, year, report_type),
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code)
);
CREATE TABLE IF NOT EXISTS financial_metric_definitions (
    metric_code TEXT PRIMARY KEY,
    metric_name TEXT NOT NULL,
    industry_profile TEXT NOT NULL,
    statement_type TEXT,
    metric_type TEXT NOT NULL CHECK(metric_type IN ('DIRECT','DERIVED','RATIO')),
    period_nature TEXT NOT NULL CHECK(period_nature IN ('point_in_time','flow','ratio')),
    unit_type TEXT NOT NULL,
    description TEXT,
    is_core INTEGER NOT NULL CHECK(is_core IN (0,1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1))
);
CREATE TABLE IF NOT EXISTS financial_metric_mappings (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_code TEXT NOT NULL,
    industry_profile TEXT NOT NULL,
    account_id TEXT NOT NULL,
    normalized_account_name TEXT NOT NULL DEFAULT '',
    sj_div TEXT NOT NULL,
    fs_div TEXT NOT NULL DEFAULT '*',
    applies_to_corp_code TEXT NOT NULL DEFAULT '*',
    mapping_status TEXT NOT NULL,
    mapping_confidence TEXT NOT NULL,
    tag_class TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    valid_from TEXT,
    valid_to TEXT,
    review_note TEXT,
    UNIQUE(metric_code, industry_profile, account_id, normalized_account_name,
           sj_div, fs_div, applies_to_corp_code),
    FOREIGN KEY(metric_code) REFERENCES financial_metric_definitions(metric_code)
);
CREATE TABLE IF NOT EXISTS financial_metric_values (
    metric_value_id INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code TEXT NOT NULL,
    year INTEGER NOT NULL,
    report_type TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    value INTEGER,
    unit TEXT,
    value_type TEXT NOT NULL,
    fs_div TEXT NOT NULL,
    scope TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('DIRECT','DERIVED','DOCUMENT_EXTRACTED')),
    source_type TEXT NOT NULL,
    source_rcept_no TEXT,
    source_document_id TEXT,
    source_chunk_id TEXT,
    source_account_id TEXT,
    source_account_name TEXT,
    source_fact_key TEXT,
    calculation_method TEXT,
    source_values TEXT,
    review_status TEXT NOT NULL,
    UNIQUE(corp_code, year, report_type, metric_code, value_type, fs_div, scope, source_kind),
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code),
    FOREIGN KEY(metric_code) REFERENCES financial_metric_definitions(metric_code)
);
CREATE INDEX IF NOT EXISTS ix_fin_metric_values_lookup
ON financial_metric_values(corp_code, year, report_type, metric_code);
"""


METRIC_DEFINITIONS = (
    ("TOTAL_ASSETS", "자산총계", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "금융회사 총자산", 1),
    ("TOTAL_LIABILITIES", "부채총계", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "금융회사 총부채", 1),
    ("TOTAL_EQUITY", "자본총계", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "금융회사 총자본", 1),
    ("NET_INCOME", "당기순이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", "연결 또는 별도 당기순이익", 1),
    ("PRETAX_INCOME", "법인세비용차감전순이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("OWNERS_NET_INCOME", "지배기업소유주지분순이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", "금융지주 연결 범위 지배주주 순이익", 1),
    ("OPERATING_INCOME", "영업이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("CASH_AND_CASH_EQUIVALENTS", "현금및현금성자산", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "전체 유동성을 의미하지 않음", 0),
    ("INTEREST_INCOME", "이자수익", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("INTEREST_EXPENSE", "이자비용", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("NET_INTEREST_INCOME", "순이자이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", "직접값 우선, 없으면 이자수익-이자비용", 1),
    ("FEE_COMMISSION_INCOME", "수수료수익", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("FEE_COMMISSION_EXPENSE", "수수료비용", "BANKING", "CIS", "DIRECT", "flow", "KRW", None, 1),
    ("NET_FEE_COMMISSION_INCOME", "순수수료이익", "BANKING", "CIS", "DIRECT", "flow", "KRW", "직접값 우선, 없으면 수수료수익-수수료비용", 1),
    ("LOANS_AT_AMORTISED_COST", "대출채권", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "결합 계정은 검토 필요", 1),
    ("LOANS_AT_AMORTISED_COST_NOTE", "대출채권(주석 분리)", "BANKING", "BS", "DIRECT", "point_in_time", "KRW",
     "재무상태표가 대출채권을 예치금·기타금융자산과 결합 표시한 경우 주석에서 분리한 상각후원가측정 대출채권 순장부금액"
     "(손실충당금·이연대출부대손익·현재가치할인차금 반영). 다른 은행 LOANS_AT_AMORTISED_COST와 같은 정의", 1),
    ("CUSTOMER_DEPOSITS", "예수부채", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", None, 1),
    ("CREDIT_LOSS_EXPENSE", "신용손실비용", "BANKING", "CIS", "DIRECT", "flow", "KRW", "손실충당금 잔액과 구분", 1),
    ("LOSS_ALLOWANCE", "손실충당금", "BANKING", "BS", "DIRECT", "point_in_time", "KRW", "주석 또는 원문 추출 대상", 1),
    ("NIM", "순이자마진", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("BIS_CAPITAL_RATIO", "BIS자기자본비율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("CET1_RATIO", "보통주자본비율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("TOTAL_CAPITAL_RATIO", "총자본비율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("NPL_RATIO", "고정이하여신비율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("DELINQUENCY_RATIO", "연체율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("CREDIT_COST_RATIO", "대손비용률", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("LOAN_TO_DEPOSIT_RATIO", "예대율", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("ROA", "총자산이익률", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
    ("ROE", "자기자본이익률", "BANKING", None, "RATIO", "ratio", "PERCENT", None, 1),
)


# Derived slots whose negative expense input was verified (next-year
# comparatives) to be a presentation sign rather than a reversal.
VERIFIED_DERIVED_SIGN_NORMALIZATIONS = frozenset(
    (item.corp_code, item.year, "FY", item.metric_code, item.value_type)
    for item in METRIC_CORRECTIONS
    if item.source_kind == "DERIVED"
)


BASE_MAPPINGS = (
    ("TOTAL_ASSETS", "ifrs-full_Assets", "BS", 10),
    ("TOTAL_LIABILITIES", "ifrs-full_Liabilities", "BS", 10),
    ("TOTAL_EQUITY", "ifrs-full_Equity", "BS", 10),
    ("NET_INCOME", "ifrs-full_ProfitLoss", "CIS", 10),
    ("PRETAX_INCOME", "ifrs-full_ProfitLossBeforeTax", "CIS", 10),
    ("OWNERS_NET_INCOME", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "CIS", 10),
    ("OPERATING_INCOME", "ifrs-full_ProfitLossFromOperatingActivities", "CIS", 10),
    ("OPERATING_INCOME", "dart_OperatingIncomeLoss", "CIS", 20),
    ("CASH_AND_CASH_EQUIVALENTS", "ifrs-full_CashAndCashEquivalents", "BS", 10),
    ("INTEREST_INCOME", "ifrs-full_RevenueFromInterest", "CIS", 10),
    ("INTEREST_EXPENSE", "ifrs-full_InterestExpense", "CIS", 10),
    ("NET_INTEREST_INCOME", "ifrs-full_InterestRevenueExpense", "CIS", 10),
    ("FEE_COMMISSION_INCOME", "ifrs-full_FeeAndCommissionIncome", "CIS", 10),
    ("FEE_COMMISSION_EXPENSE", "ifrs-full_FeeAndCommissionExpense", "CIS", 10),
    ("NET_FEE_COMMISSION_INCOME", "ifrs-full_FeeAndCommissionIncomeExpense", "CIS", 10),
    ("LOANS_AT_AMORTISED_COST", "dart_LoansAtAmortisedCost", "BS", 10),
    ("LOANS_AT_AMORTISED_COST", "ifrs-full_FinancialAssetsAtAmortisedCost", "BS", 30),
    ("CUSTOMER_DEPOSITS", "ifrs-full_DepositsFromCustomers", "BS", 10),
    ("CREDIT_LOSS_EXPENSE", "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets", "CIS", 10),
    ("CREDIT_LOSS_EXPENSE", "dart_AdditionReversalOfCreditLossFinancialAssets", "CIS", 20),
    ("CREDIT_LOSS_EXPENSE", "ifrs-full_ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9", "CIS", 30),
)


def initialize_financial_metric_schema(connection: sqlite3.Connection, include_general: bool | None = None) -> None:
    """Create the metric tables and seed definitions/mappings.

    The general-industry KPI layer (definitions, mappings, the COMMON label of
    shared metrics) is seeded only when asked for (``industry_kpi_pipeline``)
    or when the database already carries it, so bank-only pipelines and
    rebuilds never introduce it.
    """
    connection.executescript(SCHEMA)
    if include_general is None:
        include_general = connection.execute(
            "SELECT 1 FROM financial_metric_definitions WHERE metric_code=?", (GENERAL_DEFINITIONS[0][0],)
        ).fetchone() is not None
    if not include_general:
        connection.executemany(
            """INSERT INTO financial_metric_definitions(
                   metric_code,metric_name,industry_profile,statement_type,metric_type,
                   period_nature,unit_type,description,is_core,is_active
               ) VALUES (?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(metric_code) DO UPDATE SET
                 metric_name=excluded.metric_name, industry_profile=excluded.industry_profile,
                 statement_type=excluded.statement_type, metric_type=excluded.metric_type,
                 period_nature=excluded.period_nature, unit_type=excluded.unit_type,
                 description=excluded.description, is_core=excluded.is_core, is_active=1""",
            list(METRIC_DEFINITIONS),
        )
        _seed_bank_mappings(connection)
        return
    connection.executemany(
        """INSERT INTO financial_metric_definitions(
               metric_code,metric_name,industry_profile,statement_type,metric_type,
               period_nature,unit_type,description,is_core,is_active
           ) VALUES (?,?,?,?,?,?,?,?,?,1)
           ON CONFLICT(metric_code) DO UPDATE SET
             metric_name=excluded.metric_name, industry_profile=excluded.industry_profile,
             statement_type=excluded.statement_type, metric_type=excluded.metric_type,
             period_nature=excluded.period_nature, unit_type=excluded.unit_type,
             description=excluded.description, is_core=excluded.is_core, is_active=1""",
        [(row[0], row[1], "COMMON", *row[3:]) if row[0] in SHARED_METRICS else row
         for row in METRIC_DEFINITIONS] + list(GENERAL_DEFINITIONS),
    )
    rows = []
    for metric_code, account_id, sj_divs, priority, profiles in GENERAL_MAPPINGS:
        for profile in profiles:
            for sj_div in sj_divs:
                rows.append((metric_code, profile, account_id, "", sj_div, "*", "*", "approved", "high",
                             "official_standard_tag", priority, None, None, None))
    _seed_bank_mappings(connection, rows)


def _seed_bank_mappings(connection: sqlite3.Connection, rows: list | None = None) -> None:
    rows = list(rows or [])
    for profile in ("BANK_HOLDING", "INTERNET_BANK"):
        for metric_code, account_id, sj_div, priority in BASE_MAPPINGS:
            review = account_id == "ifrs-full_FinancialAssetsAtAmortisedCost"
            rows.append((
                metric_code, profile, account_id, "", sj_div, "*", "*",
                "review_required" if review else "approved",
                "medium" if review else "high", "official_standard_tag", priority,
                None, None,
                "대출채권 및 기타금융자산 결합 계정" if review else None,
            ))
    connection.executemany(
        """INSERT OR IGNORE INTO financial_metric_mappings(
               metric_code,industry_profile,account_id,normalized_account_name,sj_div,
               fs_div,applies_to_corp_code,mapping_status,mapping_confidence,tag_class,
               priority,valid_from,valid_to,review_note
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )


def upsert_company_profiles(
    connection: sqlite3.Connection,
    profiles: Iterable[Mapping[str, object]],
) -> None:
    connection.executemany(
        """INSERT INTO company_financial_profiles(
               corp_code,industry_profile,preferred_fs_div,fallback_fs_div,
               comparison_scope,capex_applicable,valid_from,valid_to
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(corp_code) DO UPDATE SET
             industry_profile=excluded.industry_profile,
             preferred_fs_div=excluded.preferred_fs_div,
             fallback_fs_div=excluded.fallback_fs_div,
             comparison_scope=excluded.comparison_scope,
             capex_applicable=excluded.capex_applicable,
             valid_from=excluded.valid_from, valid_to=excluded.valid_to""",
        [(
            row["corp_code"], row["industry_profile"], row["preferred_fs_div"],
            row.get("fallback_fs_div"), row["comparison_scope"],
            int(bool(row.get("capex_applicable", False))), row["valid_from"], row.get("valid_to"),
        ) for row in profiles],
    )


def replace_collection_status(
    connection: sqlite3.Connection,
    rows: Iterable[Mapping[str, object]],
) -> None:
    connection.executemany(
        """INSERT INTO financial_collection_status(
               corp_code,year,report_type,requested_fs_div,selected_fs_div,
               cfs_status,ofs_status,missing_reason,rcept_no
           ) VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(corp_code,year,report_type) DO UPDATE SET
             requested_fs_div=excluded.requested_fs_div,
             selected_fs_div=excluded.selected_fs_div,
             cfs_status=excluded.cfs_status, ofs_status=excluded.ofs_status,
             missing_reason=excluded.missing_reason, rcept_no=excluded.rcept_no""",
        [(
            row["corp_code"], int(row["year"]), row["report_type"], row["requested_fs_div"],
            row.get("selected_fs_div"), row["cfs_status"], row["ofs_status"],
            row.get("missing_reason"), row.get("rcept_no"),
        ) for row in rows],
    )


def _fact_key(row: sqlite3.Row) -> str:
    raw = "|".join(str(row[key] if row[key] is not None else "") for key in (
        "corp_code", "year", "report_type", "fs_div", "sj_div", "account_key",
        "ord", "value_type", "is_calculated",
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _has_unverified_sign_conflict(row: sqlite3.Row) -> bool:
    """True for a calculated quarter whose two cumulative inputs disagree in sign."""
    if not row["is_calculated"] or not row["source_values"]:
        return False
    try:
        sources = json.loads(row["source_values"])
    except (TypeError, ValueError):
        return False
    if sources.get("sign_note") == "sign_aligned":
        return False
    evidence = sources.get("verified_evidence")
    if (
        sources.get("match") == "verified_evidence"
        and isinstance(evidence, dict)
        and evidence.get("basis") == "document"
        and evidence.get("category") in {"document_real_reversal", "verified_real_reversal"}
        and len(evidence.get("rcept_nos") or []) >= 2
    ):
        # Both cumulative inputs were checked against their filed statements.
        # An opposite sign is therefore a real cash-flow/profit reversal, not
        # an unresolved presentation-sign conflict.
        return False
    inputs = [value for value in sources.values() if isinstance(value, int) and value]
    return len(inputs) == 2 and (inputs[0] > 0) != (inputs[1] > 0)

_UPSERT_COLUMNS = (
    "corp_code", "year", "report_type", "metric_code", "value", "unit", "value_type", "fs_div",
    "scope", "source_kind", "source_type", "source_rcept_no", "source_document_id",
    "source_chunk_id", "source_account_id", "source_account_name", "source_fact_key",
    "calculation_method", "source_values", "review_status",
)
_NATURAL_KEY = ("corp_code", "year", "report_type", "metric_code", "value_type", "fs_div",
                "scope", "source_kind")


def upsert_metric_values(connection: sqlite3.Connection, rows: Iterable[Mapping[str, object]]) -> int:
    """Insert or refresh rows by the natural key; reruns leave one row per key."""
    updates = ", ".join(f"{column}=excluded.{column}" for column in _UPSERT_COLUMNS
                        if column not in _NATURAL_KEY)
    values = [tuple(row.get(column) for column in _UPSERT_COLUMNS) for row in rows]
    connection.executemany(
        f"""INSERT INTO financial_metric_values({",".join(_UPSERT_COLUMNS)})
            VALUES ({",".join("?" for _ in _UPSERT_COLUMNS)})
            ON CONFLICT({",".join(_NATURAL_KEY)}) DO UPDATE SET {updates}""",
        values,
    )
    return len(values)


def derive_document_quarters(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Standalone H1/Q3 quarters from two document-extracted cumulative flows.

    Regular reports present only the cumulative column, so the quarter is the
    difference of two extracted cumulatives.  Slots that already have a
    quarterly row of any kind are left alone.
    """
    connection.row_factory = sqlite3.Row
    year_filter = ""
    params: tuple[object, ...] = ()
    if years:
        year_filter = f" AND c.year IN ({','.join('?' for _ in years)})"
        params = tuple(sorted(years))
    if corp_codes:
        year_filter += f" AND c.corp_code IN ({','.join('?' for _ in corp_codes)})"
        params += tuple(sorted(corp_codes))
    rows = connection.execute(
        f"""SELECT c.corp_code,c.year,c.report_type,c.metric_code,c.fs_div,c.scope,
                  c.value current_value,p.value previous_value,p.report_type previous_type,
                  c.source_rcept_no,c.source_document_id current_document,
                  p.source_document_id previous_document,
                  c.review_status current_review,p.review_status previous_review
           FROM financial_metric_values c
           JOIN financial_metric_definitions d ON d.metric_code=c.metric_code AND d.period_nature='flow'
           JOIN financial_metric_values p
             ON p.corp_code=c.corp_code AND p.year=c.year AND p.metric_code=c.metric_code
            AND p.fs_div=c.fs_div AND p.scope=c.scope AND p.value_type='cumulative'
            AND p.source_kind='DOCUMENT_EXTRACTED'
            AND p.report_type=CASE c.report_type WHEN 'H1' THEN 'Q1' WHEN 'Q3' THEN 'H1' END
           WHERE c.source_kind='DOCUMENT_EXTRACTED' AND c.value_type='cumulative'
             AND c.report_type IN ('H1','Q3')
             AND NOT EXISTS (
               SELECT 1 FROM financial_metric_values q
               WHERE q.corp_code=c.corp_code AND q.year=c.year AND q.report_type=c.report_type
                 AND q.metric_code=c.metric_code AND q.fs_div=c.fs_div AND q.scope=c.scope
                 AND q.value_type='quarterly' AND q.source_kind<>'DERIVED'
             ){year_filter}""",
        params,
    ).fetchall()
    derived = []
    for row in rows:
        current, previous = int(row["current_value"]), int(row["previous_value"])
        conflict = bool(current and previous and (current > 0) != (previous > 0))
        approved = row["current_review"] == row["previous_review"] == "approved" and not conflict
        derived.append({
            "corp_code": row["corp_code"], "year": row["year"], "report_type": row["report_type"],
            "metric_code": row["metric_code"], "value": current - previous, "unit": "KRW",
            "value_type": "quarterly", "fs_div": row["fs_div"], "scope": row["scope"],
            "source_kind": "DERIVED", "source_type": "calculation",
            "source_rcept_no": row["source_rcept_no"],
            "calculation_method": f"{row['report_type']} cumulative - {row['previous_type']} cumulative",
            "source_values": json.dumps({
                row["report_type"]: current, row["previous_type"]: previous,
                "component_source_kind": "DOCUMENT_EXTRACTED",
                "source_document_ids": [row["current_document"], row["previous_document"]],
                **({"sign_note": "sign_conflict_review"} if conflict else {}),
            }, ensure_ascii=False),
            "review_status": "approved" if approved else "review_required",
        })
    return upsert_metric_values(connection, derived)


def derive_net_metrics(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Net interest/fee income where no presented net line exists for the slot."""
    connection.row_factory = sqlite3.Row
    formulas = (
        ("NET_INTEREST_INCOME", "INTEREST_INCOME", "INTEREST_EXPENSE"),
        ("NET_FEE_COMMISSION_INCOME", "FEE_COMMISSION_INCOME", "FEE_COMMISSION_EXPENSE"),
    )
    derived_rows = []
    for target, income_code, expense_code in formulas:
        year_filter = ""
        params: tuple[object, ...] = (income_code, expense_code, target,
                                     f"{income_code} - ABS({expense_code})")
        if years:
            year_filter = f" AND i.year IN ({','.join('?' for _ in years)})"
            params += tuple(sorted(years))
        if corp_codes:
            year_filter += f" AND i.corp_code IN ({','.join('?' for _ in corp_codes)})"
            params += tuple(sorted(corp_codes))
        components = connection.execute(
            f"""SELECT i.corp_code,i.year,i.report_type,i.value_type,i.fs_div,i.scope,
                      i.value income_value,e.value expense_value,i.source_kind,
                      i.source_rcept_no,i.source_fact_key income_key,e.source_fact_key expense_key,
                      i.source_document_id income_document,e.source_document_id expense_document,
                      i.review_status income_review,e.review_status expense_review,
                      e.source_values expense_sources
               FROM financial_metric_values i
               JOIN financial_metric_values e
                 ON e.corp_code=i.corp_code AND e.year=i.year AND e.report_type=i.report_type
                AND e.value_type=i.value_type AND e.fs_div=i.fs_div AND e.scope=i.scope
                AND e.source_kind=i.source_kind
               WHERE i.metric_code=? AND e.metric_code=?
                 AND NOT EXISTS (
                   SELECT 1 FROM financial_metric_values d
                   WHERE d.corp_code=i.corp_code AND d.year=i.year AND d.report_type=i.report_type
                     AND d.value_type=i.value_type AND d.fs_div=i.fs_div AND d.scope=i.scope
                     AND d.metric_code=?
                     -- Same-period components take precedence over an FY - Q3
                     -- difference of nets; both give the same value.
                     AND NOT (d.source_kind='DERIVED'
                              AND d.calculation_method IN (?, 'FY annual - Q3 cumulative'))
                 ){year_filter}""",
            params,
        ).fetchall()
        for row in components:
            if row["income_value"] is None or row["expense_value"] is None:
                continue
            # Issuers present interest/fee expenses either as amounts or as
            # negative deductions, sometimes switching between reports.  The
            # expense is an outflow either way, so subtract its magnitude; a
            # negative input is approved only where that was verified externally.
            expense_amount = abs(int(row["expense_value"]))
            value = int(row["income_value"]) - expense_amount
            sign_normalized = int(row["expense_value"]) < 0
            review_status = "approved"
            # A negative expense is approved when the slot was verified or the
            # expense itself came from a verified sign alignment.
            expense_aligned = json.loads(row["expense_sources"] or "{}").get("sign_note") == "sign_aligned"
            if sign_normalized and not expense_aligned and (
                row["corp_code"], int(row["year"]), row["report_type"], target, row["value_type"]
            ) not in VERIFIED_DERIVED_SIGN_NORMALIZATIONS:
                review_status = "review_required"
            if row["income_review"] != "approved" or row["expense_review"] != "approved":
                review_status = "review_required"
            sources = {
                income_code: int(row["income_value"]),
                expense_code: int(row["expense_value"]),
                "expense_amount": expense_amount,
                "expense_sign_normalized": sign_normalized,
                "source_fact_keys": [row["income_key"], row["expense_key"]],
            }
            if row["source_kind"] != "DIRECT":
                sources["component_source_kind"] = row["source_kind"]
                sources["source_document_ids"] = [row["income_document"], row["expense_document"]]
            derived_rows.append({
                "corp_code": row["corp_code"], "year": row["year"], "report_type": row["report_type"],
                "metric_code": target, "value": value, "unit": "KRW", "value_type": row["value_type"],
                "fs_div": row["fs_div"], "scope": row["scope"], "source_kind": "DERIVED",
                "source_type": "calculation", "source_rcept_no": row["source_rcept_no"],
                "calculation_method": f"{income_code} - ABS({expense_code})",
                "source_values": json.dumps(sources, ensure_ascii=False),
                "review_status": review_status,
            })
    return upsert_metric_values(connection, derived_rows)


def derive_fy_quarter_nets(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Q4 net interest/fee income = FY annual - Q3 cumulative of derived nets.

    Only when both inputs are approved DERIVED values built with the same
    formula for the same company, statement scope and unit, and the FY slot
    has no quarterly row of another kind.
    """
    connection.row_factory = sqlite3.Row
    year_filter = ""
    params: tuple[object, ...] = ()
    if years:
        year_filter = f" AND a.year IN ({','.join('?' for _ in years)})"
        params = tuple(sorted(years))
    if corp_codes:
        year_filter += f" AND a.corp_code IN ({','.join('?' for _ in corp_codes)})"
        params += tuple(sorted(corp_codes))
    rows = connection.execute(
        f"""SELECT a.corp_code,a.year,a.metric_code,a.fs_div,a.scope,a.unit,
                  a.value annual_value,c.value q3_value,a.calculation_method formula,
                  a.source_rcept_no annual_rcept,c.source_rcept_no q3_rcept
           FROM financial_metric_values a
           JOIN financial_metric_values c
             ON c.corp_code=a.corp_code AND c.year=a.year AND c.metric_code=a.metric_code
            AND c.fs_div=a.fs_div AND c.scope=a.scope AND c.unit=a.unit
            AND c.report_type='Q3' AND c.value_type='cumulative' AND c.source_kind='DERIVED'
            -- 'A - B' (legacy text) and 'A - ABS(B)' are the same definition for
            -- approved inputs: a negative expense is approved only when verified.
            AND REPLACE(REPLACE(c.calculation_method,'ABS(',''),')','')
              = REPLACE(REPLACE(a.calculation_method,'ABS(',''),')','')
            AND c.review_status='approved'
           WHERE a.report_type='FY' AND a.value_type='annual' AND a.source_kind='DERIVED'
             AND a.review_status='approved'
             AND a.metric_code IN ('NET_INTEREST_INCOME','NET_FEE_COMMISSION_INCOME')
             AND NOT EXISTS (
               SELECT 1 FROM financial_metric_values q
               WHERE q.corp_code=a.corp_code AND q.year=a.year AND q.report_type='FY'
                 AND q.metric_code=a.metric_code AND q.fs_div=a.fs_div AND q.scope=a.scope
                 AND q.value_type='quarterly'
                 AND NOT (q.source_kind='DERIVED' AND q.calculation_method='FY annual - Q3 cumulative')
             ){year_filter}""",
        params,
    ).fetchall()
    derived = [{
        "corp_code": row["corp_code"], "year": row["year"], "report_type": "FY",
        "metric_code": row["metric_code"], "value": int(row["annual_value"]) - int(row["q3_value"]),
        "unit": row["unit"], "value_type": "quarterly", "fs_div": row["fs_div"], "scope": row["scope"],
        "source_kind": "DERIVED", "source_type": "calculation", "source_rcept_no": row["annual_rcept"],
        "calculation_method": "FY annual - Q3 cumulative",
        "source_values": json.dumps({
            "FY": int(row["annual_value"]), "Q3": int(row["q3_value"]),
            "input_formula": row["formula"],
            "source_rcept_nos": [row["annual_rcept"], row["q3_rcept"]],
        }, ensure_ascii=False),
        "review_status": "approved",
    } for row in rows]
    return upsert_metric_values(connection, derived)


def derive_verified_metric_quarters(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Q4 for metrics whose FY and Q3 lines carry different account ids.

    Only the pinned, externally verified cases in ``verified_quarter_overrides``
    are derived, and only while both stored inputs still equal the pinned ones.
    """
    connection.row_factory = sqlite3.Row
    derived = []
    for item in METRIC_QUARTERS:
        if years and item.year not in years:
            continue
        if corp_codes and item.corp_code not in corp_codes:
            continue
        rows = {
            row["report_type"]: row for row in connection.execute(
                """SELECT * FROM financial_metric_values
                   WHERE corp_code=? AND year=? AND metric_code=? AND source_kind='DIRECT'
                     AND ((report_type='FY' AND value_type='annual')
                          OR (report_type='Q3' AND value_type='cumulative'))""",
                (item.corp_code, item.year, item.metric_code))
        }
        fy, q3 = rows.get("FY"), rows.get("Q3")
        if fy is None or q3 is None or (fy["source_account_id"], fy["value"]) != (item.fy_account_id, item.fy_value) \
                or (q3["source_account_id"], q3["value"]) != (item.q3_account_id, item.q3_value):
            continue
        occupied = connection.execute(
            """SELECT 1 FROM financial_metric_values
               WHERE corp_code=? AND year=? AND report_type='FY' AND metric_code=? AND value_type='quarterly'
                 AND fs_div=? AND scope=? AND NOT (source_kind='DERIVED' AND calculation_method LIKE ?)""",
            (item.corp_code, item.year, item.metric_code, fy["fs_div"], fy["scope"],
             "FY annual - Q3 cumulative (verified tag change%"),
        ).fetchone()
        if occupied:
            continue
        value = item.fy_value + item.q3_value if item.align_sign else item.fy_value - item.q3_value
        note = "verified tag change; sign_aligned" if item.align_sign else "verified tag change"
        derived.append({
            "corp_code": item.corp_code, "year": item.year, "report_type": "FY",
            "metric_code": item.metric_code, "value": value, "unit": fy["unit"], "value_type": "quarterly",
            "fs_div": fy["fs_div"], "scope": fy["scope"], "source_kind": "DERIVED",
            "source_type": "calculation", "source_rcept_no": fy["source_rcept_no"],
            "calculation_method": f"FY annual - Q3 cumulative ({note})",
            "source_values": json.dumps({
                "FY": item.fy_value, "Q3": item.q3_value,
                "FY_account_id": item.fy_account_id, "Q3_account_id": item.q3_account_id,
                "source_fact_keys": [fy["source_fact_key"], q3["source_fact_key"]],
                "source_rcept_nos": [fy["source_rcept_no"], q3["source_rcept_no"]],
                "verified_override": {"reason": item.reason, "evidence": item.evidence},
                **({"sign_note": "sign_aligned"} if item.align_sign else {}),
            }, ensure_ascii=False),
            "review_status": "approved",
        })
    return upsert_metric_values(connection, derived)


RESTATED_QUARTER_METHOD = "FY annual - Q3 cumulative (Q3 restated to the FY basis)"


def derive_restated_metric_quarters(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Q4 from FY and the Q3 cumulative restated to the FY basis (pinned cases).

    Applies only while the stored FY annual and Q3 cumulative still equal the
    pinned values and the slot holds no quarterly row of another kind.
    """
    connection.row_factory = sqlite3.Row
    derived = []
    for item in RESTATED_METRIC_QUARTERS:
        if years and item.year not in years:
            continue
        if corp_codes and item.corp_code not in corp_codes:
            continue
        rows = {
            row["report_type"]: row for row in connection.execute(
                """SELECT * FROM financial_metric_values
                   WHERE corp_code=? AND year=? AND metric_code=? AND source_kind='DIRECT'
                     AND ((report_type='FY' AND value_type='annual')
                          OR (report_type='Q3' AND value_type='cumulative'))""",
                (item.corp_code, item.year, item.metric_code))
        }
        fy, q3 = rows.get("FY"), rows.get("Q3")
        if fy is None or q3 is None or (fy["value"], q3["value"]) != (item.fy_value, item.stored_q3_value):
            continue
        occupied = connection.execute(
            """SELECT 1 FROM financial_metric_values
               WHERE corp_code=? AND year=? AND report_type='FY' AND metric_code=? AND value_type='quarterly'
                 AND fs_div=? AND scope=? AND NOT (source_kind='DERIVED' AND calculation_method=?)""",
            (item.corp_code, item.year, item.metric_code, fy["fs_div"], fy["scope"], RESTATED_QUARTER_METHOD),
        ).fetchone()
        if occupied:
            continue
        derived.append({
            "corp_code": item.corp_code, "year": item.year, "report_type": "FY",
            "metric_code": item.metric_code, "value": item.fy_value - item.restated_q3_value,
            "unit": fy["unit"], "value_type": "quarterly", "fs_div": fy["fs_div"], "scope": fy["scope"],
            "source_kind": "DERIVED", "source_type": "calculation", "source_rcept_no": item.fy_rcept_no,
            "source_account_id": fy["source_account_id"], "source_account_name": fy["source_account_name"],
            "source_fact_key": hashlib.sha256(
                f"restated-q4|{item.corp_code}|{item.year}|{item.metric_code}".encode("utf-8")).hexdigest(),
            "calculation_method": RESTATED_QUARTER_METHOD,
            "source_values": json.dumps({
                "FY": item.fy_value, "Q3_restated": item.restated_q3_value,
                "stored_Q3_earlier_basis": str(item.stored_q3_value),
                "source_rcept_nos": [item.fy_rcept_no, item.restated_rcept_no],
                "source_fact_keys": [fy["source_fact_key"], q3["source_fact_key"]],
                "verified_override": {"evidence": item.evidence},
            }, ensure_ascii=False),
            # The stored Q1-Q3 quarters stay on the earlier basis, so the four
            # quarters do not add up to FY.
            "review_status": "review_required",
        })
    return upsert_metric_values(connection, derived)


def _capex_metric_rows(connection: sqlite3.Connection) -> list[dict]:
    """CAPEX of companies whose profile applies it, as positive cash outflow."""
    connection.row_factory = sqlite3.Row
    if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='capex_facts'").fetchone() is None:
        return []
    # CAPEX as a metric belongs to the general-industry KPI layer; a profile
    # alone (e.g. to pick the statement scope) must not create KPI rows.
    if connection.execute("SELECT 1 FROM financial_metric_definitions WHERE metric_code='CAPEX'").fetchone() is None:
        return []
    rows = connection.execute(
        """SELECT c.*, p.year, p.report_type, pr.comparison_scope
           FROM capex_facts c JOIN periods p USING(period_id)
           JOIN company_financial_profiles pr USING(corp_code)
           WHERE pr.capex_applicable=1 AND c.fs_div=pr.preferred_fs_div"""
    ).fetchall()
    metric_rows = []
    for row in rows:
        # A reviewed negative cumulative difference has no defensible positive
        # standalone CAPEX amount.  Preserve it in capex_facts, but do not
        # publish a numeric KPI by applying abs().
        if row["cash_outflow_amount"] is None:
            continue
        direct = not row["is_calculated"] and row["capex_method"] == "direct_account"
        key = "|".join(str(row[k]) for k in ("corp_code", "year", "report_type", "fs_div", "value_type"))
        metric_rows.append({
            "corp_code": row["corp_code"], "year": row["year"], "report_type": row["report_type"],
            "metric_code": "CAPEX", "value": row["cash_outflow_amount"], "unit": row["currency"] or "KRW",
            "value_type": row["value_type"], "fs_div": row["fs_div"], "scope": row["comparison_scope"],
            "source_kind": "DIRECT" if direct else "DERIVED", "source_type": "capex_facts",
            "source_rcept_no": row["rcept_no"], "source_document_id": None, "source_chunk_id": None,
            "source_account_id": row["account_id"], "source_account_name": row["original_account_nm"],
            "source_fact_key": hashlib.sha256(f"capex|{key}".encode("utf-8")).hexdigest(),
            "calculation_method": None if direct else row["calculation_method"],
            "source_values": row["source_values"],
            "review_status": "review_required" if row["review_required"] else "approved",
        })
    return metric_rows


def _ratio_bps(numerator: int, denominator: int) -> int:
    return int((Decimal(numerator) * 10000 / Decimal(denominator)).quantize(Decimal(1), ROUND_HALF_UP))


def derive_general_kpis(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Ratios, year-on-year growth and balance changes for general-industry profiles.

    Inputs must share company, value type, statement scope and unit; growth
    compares the same report and value type one year apart; ratios need a
    positive denominator.  Values are basis points (1 = 0.01 %) or KRW.
    """
    connection.row_factory = sqlite3.Row
    profile_of = dict(connection.execute(
        "SELECT corp_code, industry_profile FROM company_financial_profiles"))
    base: dict[tuple, sqlite3.Row] = {}
    for row in connection.execute(
            """SELECT * FROM financial_metric_values
               WHERE source_kind IN ('DIRECT','DERIVED') AND unit='KRW'
               ORDER BY CASE source_kind WHEN 'DIRECT' THEN 0 ELSE 1 END"""):
        key = (row["corp_code"], row["year"], row["report_type"], row["metric_code"],
               row["value_type"], row["fs_div"], row["scope"])
        base.setdefault(key, row)

    def reference(row: sqlite3.Row) -> dict:
        return {"metric": row["metric_code"], "year": row["year"], "report_type": row["report_type"],
                "value_type": row["value_type"], "value": row["value"], "source_kind": row["source_kind"],
                "source_fact_key": row["source_fact_key"]}

    derived = []
    for spec in DERIVED_KPIS:
        for key, current in base.items():
            corp, year, report, metric, value_type, fs_div, scope = key
            if years and year not in years:
                continue
            if corp_codes and corp not in corp_codes:
                continue
            if metric != spec.numerator or profile_of.get(corp) not in spec.profiles:
                continue
            if spec.kind == "ratio":
                other = base.get((corp, year, report, spec.denominator, value_type, fs_div, scope))
                if other is None or other["value"] is None or other["value"] <= 0 or current["value"] is None:
                    continue
                value, unit = _ratio_bps(current["value"], other["value"]), "BPS"
                method = f"{spec.numerator} / {spec.denominator} x 10000 (bps)"
                inputs = {"numerator": reference(current), "denominator": reference(other)}
                out_value_type = value_type
            elif spec.kind == "growth":
                other = base.get((corp, year - 1, report, metric, value_type, fs_div, scope))
                if other is None or other["value"] is None or other["value"] <= 0 or current["value"] is None:
                    continue
                value, unit = _ratio_bps(current["value"] - other["value"], other["value"]), "BPS"
                method = f"({spec.numerator} / prior-year {spec.numerator} - 1) x 10000 (bps)"
                inputs = {"current": reference(current), "prior_year": reference(other)}
                out_value_type = value_type
            else:  # change between consecutive balance dates
                if value_type != "point_in_time":
                    continue
                offset, previous_report = PREVIOUS_POINT[report]
                other = base.get((corp, year + offset, previous_report, metric, value_type, fs_div, scope))
                if other is None or other["value"] is None or current["value"] is None:
                    continue
                value, unit = current["value"] - other["value"], "KRW"
                method = f"{spec.numerator} - previous {spec.numerator}"
                inputs = {"current": reference(current), "previous": reference(other)}
                out_value_type = "quarterly"
            approved = current["review_status"] == other["review_status"] == "approved"
            derived.append({
                "corp_code": corp, "year": year, "report_type": report, "metric_code": spec.metric_code,
                "value": value, "unit": unit, "value_type": out_value_type, "fs_div": fs_div, "scope": scope,
                "source_kind": "DERIVED", "source_type": "kpi_calculation",
                "source_rcept_no": current["source_rcept_no"], "calculation_method": method,
                "source_values": json.dumps(inputs, ensure_ascii=False),
                "review_status": "approved" if approved else "review_required",
            })
    return upsert_metric_values(connection, derived)


def refresh_derived_metrics(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> dict[str, int]:
    """All derived steps in dependency order; safe to rerun.

    Expense rows are returned to their filed sign first, so every derivation
    reads filed values, and get the analysis sign again at the end.
    """
    restore_expense_signs(connection, years, corp_codes)
    steps = {
        "verified_metric_quarters": derive_verified_metric_quarters(connection, years, corp_codes),
        "restated_metric_quarters": derive_restated_metric_quarters(connection, years, corp_codes),
        "document_quarters": derive_document_quarters(connection, years, corp_codes),
        "nets": derive_net_metrics(connection, years, corp_codes),
        "fy_quarter_nets": derive_fy_quarter_nets(connection, years, corp_codes),
        "general_kpis": derive_general_kpis(connection, years, corp_codes),
    }
    steps["expense_signs"] = sum(standardize_expense_signs(connection, years, corp_codes).values())
    return steps


def _fact_metric_rows(connection: sqlite3.Connection, profile_codes: list[str]) -> list[dict]:
    """One metric row per slot from mapped facts (highest-priority mapping first).

    Quarters that were matched across reports by account id (their display
    order changed) are calculations, so they are labelled DERIVED and keep the
    formula and FY/Q3 inputs; all other fact projections are DIRECT.
    """
    placeholders = ",".join("?" for _ in profile_codes)
    rows = connection.execute(
        f"""
        SELECT f.*, p.year, p.report_type, pr.industry_profile, pr.comparison_scope,
               m.metric_code, m.mapping_status, m.priority
        FROM financial_facts f
        JOIN periods p USING(period_id)
        JOIN company_financial_profiles pr USING(corp_code)
        JOIN financial_metric_mappings m
          ON m.industry_profile=pr.industry_profile
         AND m.account_id=f.account_id AND m.sj_div=f.sj_div
         AND (m.fs_div='*' OR m.fs_div=f.fs_div)
         AND (m.applies_to_corp_code='*' OR m.applies_to_corp_code=f.corp_code)
        WHERE f.corp_code IN ({placeholders})
        ORDER BY f.corp_code,p.year,p.report_type,m.metric_code,f.value_type,
                 m.priority,COALESCE(f.ord,999999),f.fact_id
        """,
        profile_codes,
    ).fetchall()
    selected: dict[tuple, sqlite3.Row] = {}
    for row in rows:
        key = (
            row["corp_code"], row["year"], row["report_type"], row["metric_code"],
            row["value_type"], row["fs_div"], row["comparison_scope"],
        )
        selected.setdefault(key, row)
    metric_rows = []
    for row in selected.values():
        value = row["calculated_value"] if row["is_calculated"] else row["normalized_value"]
        account_matched = bool(row["is_calculated"]) and \
            json.loads(row["source_values"] or "{}").get("match") == "account_id"
        # Calculated quarters are DERIVED for general-industry KPIs; bank rows keep
        # their established labelling so existing values are not reclassified.
        derived_fact = account_matched or (
            bool(row["is_calculated"]) and row["industry_profile"] not in ("BANK_HOLDING", "INTERNET_BANK"))
        metric_rows.append({
            "corp_code": row["corp_code"], "year": row["year"], "report_type": row["report_type"],
            "metric_code": row["metric_code"], "value": value, "unit": row["currency"] or "KRW",
            "value_type": row["value_type"], "fs_div": row["fs_div"], "scope": row["comparison_scope"],
            "source_kind": "DERIVED" if derived_fact else "DIRECT",
            "source_type": "calculation" if derived_fact else "financial_statement",
            "source_rcept_no": row["rcept_no"], "source_document_id": None, "source_chunk_id": None,
            "source_account_id": row["account_id"], "source_account_name": row["original_account_nm"],
            "source_fact_key": _fact_key(row),
            "calculation_method": row["calculation_method"] if derived_fact else None,
            "source_values": row["source_values"],
            "review_status": "review_required"
            if row["mapping_status"] == "review_required" or _has_unverified_sign_conflict(row)
            else "approved",
        })
    return metric_rows


def materialize_financial_metric_values(connection: sqlite3.Connection) -> dict[str, int]:
    connection.row_factory = sqlite3.Row
    initialize_financial_metric_schema(connection)
    profile_codes = [row[0] for row in connection.execute("SELECT corp_code FROM company_financial_profiles")]
    if not profile_codes:
        return {"direct": 0, "derived": 0}
    placeholders = ",".join("?" for _ in profile_codes)
    # Document-extracted values are curated inputs, not projections of
    # financial_facts, so a rebuild must not drop them.
    connection.execute(
        f"""DELETE FROM financial_metric_values
            WHERE corp_code IN ({placeholders}) AND source_kind IN ('DIRECT','DERIVED')""",
        profile_codes,
    )
    metric_rows = _fact_metric_rows(connection, profile_codes) + _capex_metric_rows(connection)
    upsert_metric_values(connection, metric_rows)
    derived = sum(refresh_derived_metrics(connection).values())
    return {"direct": sum(r["source_kind"] == "DIRECT" for r in metric_rows),
            "derived": derived + sum(r["source_kind"] == "DERIVED" for r in metric_rows)}


def add_missing_fact_metrics(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Insert fact-based metric rows only for slots that have no row of any kind.

    Used after new calculated quarters are added, so existing rows (and their
    ids) stay untouched; a full rebuild produces the same set.
    """
    connection.row_factory = sqlite3.Row
    initialize_financial_metric_schema(connection)
    profile_codes = [row[0] for row in connection.execute("SELECT corp_code FROM company_financial_profiles")]
    if not profile_codes:
        return 0
    existing = {
        tuple(row) for row in connection.execute(
            "SELECT corp_code,year,report_type,metric_code,value_type,fs_div,scope FROM financial_metric_values")
    }
    new_rows = [row for row in _fact_metric_rows(connection, profile_codes) + _capex_metric_rows(connection)
                if (not years or int(row["year"]) in years)
                if (not corp_codes or row["corp_code"] in corp_codes)
                if (row["corp_code"], row["year"], row["report_type"], row["metric_code"],
                    row["value_type"], row["fs_div"], row["scope"]) not in existing]
    upsert_metric_values(connection, new_rows)
    refresh_derived_metrics(connection, years, corp_codes)
    return len(new_rows)


def initialize_financial_metrics(path: Path = FINANCIAL_DB_PATH) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        initialize_financial_metric_schema(connection)
