"""Decide from the filed statements whether a bank expense line is a net expense or a net reversal.

The API (XBRL) sign of bank expense lines varies by issuer, year and report,
so the sign alone says nothing.  The filed income statement does:

1. subtotal: the line sits between a gross and a net subtotal of the same
   column, e.g. 총영업이익 - 신용손실충당금전입액 = 순영업이익.  gross - |v| = net
   means an expense, gross + |v| = net a net reversal.
2. reference_line: otherwise, the line is printed like the general and
   administrative expense of the same statement column (both in parentheses or
   both without) -> expense.  An opposite presentation is not taken as proof of
   a reversal (issuers print deductions both ways), so it stays unverified.

No ``abs()`` decides anything: without one of these the result is None.
``python -m src.quality.expense_sign_evidence --write`` regenerates the pinned
manifest ``src/db/expense_sign_manifest.json`` from the current database.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.quality.residual_verification import UNIT_SCALE, _document_path, _name_keys, _statement_rows

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "db" / "expense_sign_manifest.json"
EXPENSE_METRICS = ("CREDIT_LOSS_EXPENSE", "INTEREST_EXPENSE", "FEE_COMMISSION_EXPENSE")
# (gross subtotal keywords, net subtotal keywords) by metric; matched on the
# normalized row label (spaces and numbering removed).
SUBTOTAL_ROLES = {
    "CREDIT_LOSS_EXPENSE": (("총영업이익", "반영전영업이익", "전입전영업이익"), ("순영업이익", "영업이익")),
    "INTEREST_EXPENSE": (("이자수익",), ("순이자이익", "순이자손익")),
    "FEE_COMMISSION_EXPENSE": (("수수료수익",), ("순수수료이익", "순수수료손익")),
}
REFERENCE_LINE = ("일반관리비", "판매비와관리비")
WINDOW = 8


def _label(names: frozenset) -> str:
    return max(names, key=len) if names else ""


def _has(label: str, keywords: tuple[str, ...], exclude: tuple[str, ...] = ()) -> bool:
    return any(k in label for k in keywords) and not any(x in label for x in exclude)


def judge(corp_code: str, year: int, report_type: str, fs_div: str, metric_code: str,
          account_name: str, value: int) -> dict:
    """{'meaning': 'expense'|'reversal'|None, 'basis', 'rcept_no', 'presented_sign', 'detail'}."""
    path = _document_path(corp_code, year, report_type)
    result = {"meaning": None, "basis": None, "rcept_no": Path(path).parent.name if path else None,
              "presented_sign": None, "detail": None}
    if not value or path is None:
        result["basis"] = "no_document" if path is None else "zero_value"
        return result
    rows = [r for r in _statement_rows(corp_code, year, report_type, fs_div) if r[0] == "IS"]
    keys = _name_keys(account_name)
    gross_keys, net_keys = SUBTOTAL_ROLES[metric_code]
    hits = []
    for index, (_, names, amounts, unit) in enumerate(rows):
        if not names & keys:
            continue
        # A table whose unit line was not captured: try every unit, as presented_sign does.
        scales = (UNIT_SCALE[unit],) if unit in UNIT_SCALE else (1, 1_000, 1_000_000)
        for column, amount in enumerate(amounts):
            for scale in scales:
                if abs(amount) * scale == abs(value):
                    hits.append((index, column, amount, scale))
    if not hits:
        result["basis"] = "line_not_found"
        return result
    signs = {"-" if amount < 0 else "+" for _, _, amount, _ in hits}
    result["presented_sign"] = signs.pop() if len(signs) == 1 else "mixed"
    magnitude = abs(value)
    for index, column, amount, scale in hits:
        nearby = [(i, rows[i]) for i in range(max(0, index - WINDOW), min(len(rows), index + WINDOW + 1))
                  if i != index]

        def cell(row):
            return row[2][column] * scale if column < len(row[2]) else None

        gross = [(i, cell(r), _label(r[1])) for i, r in nearby
                 if _has(_label(r[1]), gross_keys, exclude=("순",)) and cell(r) is not None]
        net = [(i, cell(r), _label(r[1])) for i, r in nearby
               if _has(_label(r[1]), net_keys) and not _has(_label(r[1]), gross_keys, exclude=("순",))
               and cell(r) is not None]
        for _, g, g_label in gross:
            for _, n, n_label in net:
                if g - magnitude == n:
                    result.update(meaning="expense", basis="subtotal",
                                  detail=f"{g_label} {g:,} - {magnitude:,} = {n_label} {n:,}")
                    return result
                if g + magnitude == n:
                    result.update(meaning="reversal", basis="subtotal",
                                  detail=f"{g_label} {g:,} + {magnitude:,} = {n_label} {n:,}")
                    return result
    for index, column, amount, scale in hits:
        for _, names, amounts, unit in rows:
            label = _label(names)
            if _has(label, REFERENCE_LINE) and column < len(amounts) and amounts[column]:
                if (amounts[column] < 0) == (amount < 0):
                    result.update(meaning="expense", basis="reference_line",
                                  detail=f"printed like {label} ({'negative' if amount < 0 else 'positive'})")
                    return result
                result["detail"] = f"printed opposite to {label}"
    result["basis"] = "unverified"
    return result


def build_manifest(connection: sqlite3.Connection) -> list[dict]:
    """Evidence for every cumulative/annual expense metric row of the bank profiles."""
    entries = []
    for corp, year, report, fs_div, metric, value, name, sources in connection.execute(
            f"""SELECT corp_code, year, report_type, fs_div, metric_code, value, source_account_name, source_values
                FROM financial_metric_values
                WHERE metric_code IN ({",".join("?" for _ in EXPENSE_METRICS)})
                  AND value_type IN ('cumulative','annual')
                  AND corp_code IN (SELECT corp_code FROM company_financial_profiles
                                    WHERE industry_profile IN ('BANK_HOLDING','INTERNET_BANK'))
                ORDER BY corp_code, metric_code, year, report_type""", EXPENSE_METRICS):
        standardization = json.loads(sources or "{}").get("sign_standardization") or {}
        presented = standardization.get("original_value", value)
        evidence = judge(corp, year, report, fs_div, metric, name or "", presented)
        entries.append({"corp_code": corp, "year": year, "report_type": report, "fs_div": fs_div,
                        "metric_code": metric, "presented_value": presented, "account_name": name,
                        **evidence})
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--write", action="store_true", help="pinned manifest를 다시 쓴다.")
    args = parser.parse_args()
    with closing(sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)) as connection:
        entries = build_manifest(connection)
    summary: dict = {}
    for entry in entries:
        key = f"{entry['metric_code']}|{entry['meaning']}|{entry['basis']}"
        summary[key] = summary.get(key, 0) + 1
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.write:
        MANIFEST_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {MANIFEST_PATH} ({len(entries)} entries)")


if __name__ == "__main__":
    main()
