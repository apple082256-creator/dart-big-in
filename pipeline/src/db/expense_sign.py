"""Analysis sign for bank expense metrics: positive = expense, negative = net reversal.

``financial_facts`` keeps the filed (API) sign.  Only the metric rows of
``EXPENSE_METRICS`` for bank profiles are standardized, and only where the
pinned evidence manifest (``expense_sign_manifest.json``, built by
``src.quality.expense_sign_evidence`` from the filed statements) says whether
the cumulative value is an expense or a net reversal.  Quarter rows become the
difference of the standardized cumulatives.  A row without evidence keeps its
filed value and is set to review_required.

``restore_expense_signs`` puts every row back to its filed value first, so the
other derivations (net interest/fee income, document quarters, ...) always
read filed values and never convert a sign twice; ``standardize_expense_signs``
runs last.  Both are idempotent, which makes a rebuild reproduce the result.
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent / "expense_sign_manifest.json"
EXPENSE_METRICS = ("CREDIT_LOSS_EXPENSE", "INTEREST_EXPENSE", "FEE_COMMISSION_EXPENSE")
BANK_PROFILES = ("BANK_HOLDING", "INTERNET_BANK")
POLICY = "expense_positive"
PREVIOUS_REPORT = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
KEY = "sign_standardization"


@lru_cache(maxsize=1)
def _manifest() -> dict[tuple, dict]:
    if not MANIFEST_PATH.is_file():
        return {}
    entries = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {(e["corp_code"], e["year"], e["report_type"], e["fs_div"], e["metric_code"], e["presented_value"]): e
            for e in entries if e.get("meaning") in ("expense", "reversal")}


def _rows(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    year_filter = ""
    params: tuple[object, ...] = EXPENSE_METRICS + BANK_PROFILES
    if years:
        year_filter = f" AND year IN ({','.join('?' for _ in years)})"
        params += tuple(sorted(years))
    corp_filter = ""
    if corp_codes:
        corp_filter = f" AND corp_code IN ({','.join('?' for _ in corp_codes)})"
        params += tuple(sorted(corp_codes))
    return connection.execute(
        f"""SELECT * FROM financial_metric_values
            WHERE metric_code IN ({",".join("?" for _ in EXPENSE_METRICS)})
              AND corp_code IN (SELECT corp_code FROM company_financial_profiles
                                WHERE industry_profile IN ({",".join("?" for _ in BANK_PROFILES)}))
              {year_filter}{corp_filter}""",
        params).fetchall()


def restore_expense_signs(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> int:
    """Return standardized rows to their filed value, inputs record and review status."""
    restored = 0
    for row in _rows(connection, years, corp_codes):
        sources = json.loads(row["source_values"] or "{}")
        record = sources.pop(KEY, None)
        if record is None:
            continue
        connection.execute(
            """UPDATE financial_metric_values SET value=?, source_values=?, review_status=?
               WHERE metric_value_id=?""",
            (record["original_value"], json.dumps(sources, ensure_ascii=False) if sources else None,
             record["original_review_status"], row["metric_value_id"]))
        restored += 1
    connection.row_factory = None
    return restored


def filed_value(value: int | None, source_values: str | None) -> int | None:
    """The filed (pre-standardization) value of a metric row; value guards compare this."""
    record = json.loads(source_values or "{}").get(KEY) if source_values else None
    return value if record is None else record["original_value"]


def _sign(value: int) -> str:
    return "-" if value < 0 else "+" if value > 0 else "0"


def standardize_expense_signs(
    connection: sqlite3.Connection, years: set[int] | None = None,
    corp_codes: set[str] | None = None,
) -> dict[str, int]:
    """Apply the analysis sign where the filed statements prove the meaning."""
    restore_expense_signs(connection, years, corp_codes)
    rows = _rows(connection, years, corp_codes)
    manifest = _manifest()
    counts = {"cumulative_standardized": 0, "quarter_standardized": 0, "unverified": 0, "unchanged_value": 0}
    standard: dict[tuple, tuple[int, dict]] = {}
    updates: list[tuple[sqlite3.Row, int | None, dict]] = []
    for row in rows:
        if row["value_type"] not in ("cumulative", "annual"):
            continue
        evidence = manifest.get((row["corp_code"], row["year"], row["report_type"], row["fs_div"],
                                 row["metric_code"], row["value"]))
        if evidence is None:
            updates.append((row, None, {"status": "unverified", "reason": "no filed-statement evidence"}))
            continue
        magnitude = abs(int(row["value"]))
        value = magnitude if evidence["meaning"] == "expense" else -magnitude
        record = {"status": "standardized", "meaning": evidence["meaning"], "basis": evidence["basis"],
                  "evidence": evidence["detail"], "evidence_rcept_no": evidence["rcept_no"],
                  "source_rcept_no": row["source_rcept_no"],
                  "method": "filed value with the sign of its proven meaning"}
        standard[(row["corp_code"], row["year"], row["report_type"], row["metric_code"], row["fs_div"],
                  row["scope"])] = (value, record)
        updates.append((row, value, record))
    for row in rows:
        if row["value_type"] != "quarterly":
            continue
        slot = (row["corp_code"], row["year"], row["report_type"], row["metric_code"], row["fs_div"], row["scope"])
        current = standard.get(slot)
        previous_type = PREVIOUS_REPORT.get(row["report_type"])
        previous = standard.get(slot[:2] + (previous_type,) + slot[3:]) if previous_type else None
        if current is None or (previous_type and previous is None):
            updates.append((row, None, {"status": "unverified", "reason": "cumulative inputs not standardized"}))
            continue
        value = current[0] - previous[0] if previous_type else current[0]
        if abs(value) != abs(int(row["value"])):
            # The stored quarter does not have the size the proven cumulatives give.
            updates.append((row, None, {"status": "unverified", "reason": "quarter size differs from "
                                        "standardized cumulative difference", "expected": value}))
            continue
        record = {"status": "standardized", "meaning": "expense" if value >= 0 else "reversal",
                  "basis": "standardized cumulative difference",
                  "inputs": {row["report_type"]: current[0], **({previous_type: previous[0]} if previous_type else {})},
                  "source_rcept_no": row["source_rcept_no"],
                  "method": (f"{row['report_type']} - {previous_type} standardized cumulative" if previous_type
                             else "Q1 standardized cumulative")}
        updates.append((row, value, record))
    for row, value, record in updates:
        sources = json.loads(row["source_values"] or "{}")
        original = int(row["value"])
        sources[KEY] = {"policy": POLICY, "original_value": original, "original_sign": _sign(original),
                        "standardized_value": value, "original_review_status": row["review_status"], **record}
        review_status = row["review_status"] if value is not None else "review_required"
        connection.execute(
            "UPDATE financial_metric_values SET value=?, source_values=?, review_status=? WHERE metric_value_id=?",
            (original if value is None else value, json.dumps(sources, ensure_ascii=False), review_status,
             row["metric_value_id"]))
        if value is None:
            counts["unverified"] += 1
        else:
            counts["quarter_standardized" if row["value_type"] == "quarterly" else "cumulative_standardized"] += 1
            counts["unchanged_value"] += value == original
    connection.row_factory = None
    return counts
