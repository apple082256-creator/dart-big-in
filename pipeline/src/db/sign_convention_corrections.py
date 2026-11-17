"""Correct verified expense sign-convention errors in derived/quarterly values.

Every target row was verified against the issuer's next-year report, whose
comparative columns restate both inputs with one sign convention.  Only the
listed rows change; each update is guarded by its current value so a rerun is
a no-op and an unexpected value aborts the whole transaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.db.expense_sign import filed_value

CORRECTION_ID = "sign-convention-20261008"


@dataclass(frozen=True)
class FactCorrection:
    fact_id: int
    corp_code: str
    year: int
    account_key: str
    current_cumulative: int
    previous_cumulative: int
    old_value: int
    new_value: int
    evidence_rcept_nos: tuple[str, str]


@dataclass(frozen=True)
class MetricCorrection:
    metric_value_id: int
    corp_code: str
    year: int
    metric_code: str
    value_type: str
    source_kind: str
    old_value: int
    new_value: int
    evidence: str
    fact_id: int | None = None


# FY quarterly = FY annual - Q3 cumulative, where the two reports used opposite
# signs.  Corrected = FY annual + Q3 cumulative (Q3 restated in FY convention).
FACT_CORRECTIONS = (
    FactCorrection(54987, "00547583", 2023, "dart_NonOperatingExpense",
                   412511000000, -280338000000, 692849000000, 132173000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(54990, "00547583", 2023, "dart_OperatingExpenseInsurance",
                   521008000000, -402651000000, 923659000000, 118357000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(54993, "00547583", 2023, "dart_OtherOperatingExpense",
                   2476408000000, -1639893000000, 4116301000000, 836515000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(54995, "00547583", 2023,
                   "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets",
                   1550742000000, -1227966000000, 2778708000000, 322776000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(55001, "00547583", 2023, "ifrs-full_FeeAndCommissionExpense",
                   1057187000000, -766549000000, 1823736000000, 290638000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(55004, "00547583", 2023, "ifrs-full_IncomeTaxExpenseContinuingOperations",
                   1212856000000, -1026357000000, 2239213000000, 186499000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(55013, "00547583", 2023, "name:보험및재보험계약이자비용",
                   330481000000, -226398000000, 556879000000, 104083000000,
                   ("20250317000894", "20241114002571")),
    FactCorrection(54731, "00382199", 2024, "ifrs-full_FeeAndCommissionExpense",
                   1580492000000, -1157904000000, 2738396000000, 422588000000,
                   ("20261007000218", "20251114002208")),
    FactCorrection(56070, "01133217", 2023,
                   "dart_LossFromFinancialInstrumentsAtFairValueThroughProfitOrLoss",
                   -3027000000, 3001000000, -6028000000, -26000000,
                   ("20250411001905", "20241114001656")),
    FactCorrection(9789, "00161383", 2023,
                   "ifrs-full_IncomeTaxRelatingToComponentsOfOtherComprehensiveIncomeThatWillNotBeReclassifiedToProfitOrLoss",
                   -16544100, 12318324, -28862424, -4225776,
                   ("20250313001171", "20241114002196")),
)

# Metric rows built from the facts above (source_kind DIRECT, value_type
# quarterly) and the two DERIVED rows that subtracted a negative expense.
METRIC_CORRECTIONS = (
    MetricCorrection(1215, "01133217", 2023, "NET_INTEREST_INCOME", "annual", "DERIVED",
                     2980370000000, 1115970000000,
                     "INTEREST_INCOME 2048170000000 - ABS(INTEREST_EXPENSE -932200000000); "
                     "2024 FY comparative 20250411001905"),
    MetricCorrection(1233, "01133217", 2023, "NET_FEE_COMMISSION_INCOME", "annual", "DERIVED",
                     537624000000, -2030000000,
                     "FEE_COMMISSION_INCOME 267797000000 - ABS(FEE_COMMISSION_EXPENSE -269827000000); "
                     "2024 FY comparative 20250411001905"),
    MetricCorrection(47, "00382199", 2024, "FEE_COMMISSION_EXPENSE", "quarterly", "DIRECT",
                     2738396000000, 422588000000, "financial_facts 54731", fact_id=54731),
    MetricCorrection(253, "00547583", 2023, "CREDIT_LOSS_EXPENSE", "quarterly", "DIRECT",
                     2778708000000, 322776000000, "financial_facts 54995", fact_id=54995),
    MetricCorrection(256, "00547583", 2023, "FEE_COMMISSION_EXPENSE", "quarterly", "DIRECT",
                     1823736000000, 290638000000, "financial_facts 55001", fact_id=55001),
)


@dataclass(frozen=True)
class ReviewFlag:
    metric_value_id: int
    corp_code: str
    year: int
    metric_code: str
    value_type: str
    value: int
    reason: str
    # Value a later verified correction set (the flag then no longer applies).
    superseded_value: int | None = None
    superseded_by: str | None = None


# Values left unchanged but no longer presented as approved: a calculated
# quarter whose cumulative inputs disagree in sign without external proof.
REVIEW_FLAGS = (
    ReviewFlag(964, "01350869", 2023, "CREDIT_LOSS_EXPENSE", "quarterly", -2973496000000,
               "FY -1894916000000 vs Q3 cumulative 1078580000000 opposite signs; "
               "2024 comparatives repeat the mismatch, so not verifiable",
               superseded_value=-816336000000,
               superseded_by="residual-verification-v8: both cumulatives shown in parentheses in the filings"),
)


def correction_record(item: FactCorrection) -> dict:
    """Provenance kept in source_values; quarter regeneration emits the same record."""
    return {"id": CORRECTION_ID, "previous_value": item.old_value,
            "evidence_rcept_nos": list(item.evidence_rcept_nos)}


def _fact_payload(item: FactCorrection) -> tuple[str, str]:
    method = (
        f"FY cumulative - Q3 cumulative (sign_aligned) "
        f"[FY:{item.current_cumulative};Q3:{item.previous_cumulative}]"
    )
    sources = json.dumps({
        "FY": item.current_cumulative,
        "Q3": item.previous_cumulative,
        "sign_note": "sign_aligned",
        "correction": correction_record(item),
    }, ensure_ascii=False)
    return method, sources


def _metric_payload(connection: sqlite3.Connection, item: MetricCorrection) -> tuple[str | None, str]:
    """Return exactly what materialize_financial_metric_values() would now write."""
    if item.source_kind == "DIRECT":
        fact = next(f for f in FACT_CORRECTIONS if f.fact_id == item.fact_id)
        return None, _fact_payload(fact)[1]
    current = json.loads(connection.execute(
        "SELECT source_values FROM financial_metric_values WHERE metric_value_id=?",
        (item.metric_value_id,),
    ).fetchone()[0])
    income_code, expense_code = {
        "NET_INTEREST_INCOME": ("INTEREST_INCOME", "INTEREST_EXPENSE"),
        "NET_FEE_COMMISSION_INCOME": ("FEE_COMMISSION_INCOME", "FEE_COMMISSION_EXPENSE"),
    }[item.metric_code]
    expense = int(current[expense_code])
    sources = {
        income_code: int(current[income_code]),
        expense_code: expense,
        "expense_amount": abs(expense),
        "expense_sign_normalized": expense < 0,
        "source_fact_keys": current["source_fact_keys"],
    }
    return f"{income_code} - ABS({expense_code})", json.dumps(sources, ensure_ascii=False)


def apply_corrections(connection: sqlite3.Connection) -> dict[str, int]:
    """Apply all corrections in one transaction; return applied/skipped counts."""
    counts = {"facts_updated": 0, "facts_already": 0, "metrics_updated": 0, "metrics_already": 0,
              "review_flagged": 0, "review_already": 0, "review_superseded": 0}
    with connection:
        for item in FACT_CORRECTIONS:
            row = connection.execute(
                """SELECT f.corp_code, p.year, p.report_type, f.account_key, f.value_type,
                          f.is_calculated, f.calculated_value
                   FROM financial_facts f JOIN periods p USING(period_id) WHERE f.fact_id=?""",
                (item.fact_id,),
            ).fetchone()
            expected = (item.corp_code, item.year, "FY", item.account_key, "quarterly", 1)
            if row is None or tuple(row[:6]) != expected:
                raise RuntimeError(f"financial_facts {item.fact_id} 식별 정보 불일치: {row}")
            if row[6] == item.new_value:
                counts["facts_already"] += 1
                continue
            if row[6] != item.old_value:
                raise RuntimeError(f"financial_facts {item.fact_id} 예상 밖 값: {row[6]}")
            method, sources = _fact_payload(item)
            connection.execute(
                """UPDATE financial_facts SET calculated_value=?, calculation_method=?, source_values=?
                   WHERE fact_id=? AND calculated_value=?""",
                (item.new_value, method, sources, item.fact_id, item.old_value),
            )
            counts["facts_updated"] += 1
        for item in METRIC_CORRECTIONS:
            row = connection.execute(
                """SELECT corp_code, year, report_type, metric_code, value_type, source_kind, value, source_values
                   FROM financial_metric_values WHERE metric_value_id=?""",
                (item.metric_value_id,),
            ).fetchone()
            if row is not None:  # guards compare the filed value of sign-standardized expense rows
                row = tuple(row[:6]) + (filed_value(row[6], row[7]),)
            expected = (item.corp_code, item.year, "FY", item.metric_code, item.value_type, item.source_kind)
            if row is None or tuple(row[:6]) != expected:
                raise RuntimeError(f"financial_metric_values {item.metric_value_id} 식별 정보 불일치: {row}")
            if row[6] == item.new_value:
                counts["metrics_already"] += 1
                continue
            if row[6] != item.old_value:
                raise RuntimeError(f"financial_metric_values {item.metric_value_id} 예상 밖 값: {row[6]}")
            method, sources = _metric_payload(connection, item)
            connection.execute(
                """UPDATE financial_metric_values SET value=?, calculation_method=?, source_values=?
                   WHERE metric_value_id=? AND value=?""",
                (item.new_value, method, sources, item.metric_value_id, item.old_value),
            )
            counts["metrics_updated"] += 1
        for flag in REVIEW_FLAGS:
            row = connection.execute(
                """SELECT corp_code, year, report_type, metric_code, value_type, value, review_status, source_values
                   FROM financial_metric_values WHERE metric_value_id=?""",
                (flag.metric_value_id,),
            ).fetchone()
            if row is not None:
                row = tuple(row[:5]) + (filed_value(row[5], row[7]), row[6])
            expected = (flag.corp_code, flag.year, "FY", flag.metric_code, flag.value_type, flag.value)
            if row is not None and flag.superseded_value is not None \
                    and tuple(row[:6]) == expected[:5] + (flag.superseded_value,):
                counts["review_superseded"] += 1
                continue
            if row is None or tuple(row[:6]) != expected:
                raise RuntimeError(f"financial_metric_values {flag.metric_value_id} 식별 정보 불일치: {row}")
            if row[6] == "review_required":
                counts["review_already"] += 1
                continue
            connection.execute(
                """UPDATE financial_metric_values SET review_status='review_required'
                   WHERE metric_value_id=? AND value=? AND review_status='approved'""",
                (flag.metric_value_id, flag.value),
            )
            counts["review_flagged"] += 1
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_staging(source: Path, target: Path) -> dict[str, object]:
    """Snapshot the source DB read-only into a new target and correct the target."""
    if target.resolve() == FINANCIAL_DB_PATH.resolve() or target.resolve() == source.resolve():
        raise ValueError("운영 DB나 source를 직접 수정할 수 없습니다. staging 경로를 지정하세요.")
    if target.exists():
        raise FileExistsError(f"이미 존재하는 staging DB입니다(재적용은 --apply-only): {target}")
    source_hash = _sha256(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    if _sha256(source) != source_hash:
        raise RuntimeError("스냅샷 중 source DB가 변경됐습니다.")
    with closing(sqlite3.connect(target)) as connection:
        counts = apply_corrections(connection)
    return {"source_sha256": source_hash, "target": str(target), **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true",
                        help="기존 staging DB에 보정만 다시 적용한다(멱등).")
    args = parser.parse_args()
    if args.apply_only:
        if args.target.resolve() == FINANCIAL_DB_PATH.resolve():
            raise ValueError("운영 DB에는 --apply-only를 사용할 수 없습니다.")
        with closing(sqlite3.connect(args.target)) as connection:
            print(apply_corrections(connection))
        return
    print(build_staging(args.source, args.target))


if __name__ == "__main__":
    main()
