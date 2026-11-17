"""Corrections confirmed by the residual verification (v8), applied to a staging copy.

1. Calculated quarters whose two cumulative inputs were presented with the
   same sign in the filings (both in parentheses, or both without) although
   the API returned them with opposite signs.  The stored arithmetic
   difference is wrong; the quarter is current + previous (the previous
   cumulative restated in the current report's convention).
2. Bank metric rows that project those facts.
3. KB Financial 2023 Q4 for three core lines (review_required).  The stored FY (2023 annual
   report) already applies the variable fee approach adopted at 2023 year end,
   while the stored Q3 cumulative (original Q3 report) does not.  The 2024 Q3
   report presents the 2023 Q3 cumulative restated under that approach next
   to the same 2023 FY values, so FY - restated Q3 is on one basis.

Every update is guarded by the current value, so a rerun is a no-op and an
unexpected value aborts the transaction.  The same results come out of the
regular pipeline: ``period_normalizer`` sign-aligns these exact inputs (with
the same correction record) and ``refresh_derived_metrics`` derives the KB
slots (``verified_quarter_overrides.RESTATED_METRIC_QUARTERS``).
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
from src.db.financial_metrics import RESTATED_QUARTER_METHOD, derive_restated_metric_quarters
from src.db.verified_quarter_overrides import RESTATED_METRIC_QUARTERS

CORRECTION_ID = "residual-verification-v8"
PREVIOUS_REPORT = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}


@dataclass(frozen=True)
class PresentationFlip:
    fact_id: int
    corp_code: str
    year: int
    report_type: str
    account_key: str
    current_cumulative: int
    previous_cumulative: int
    old_value: int
    new_value: int
    evidence_rcept_nos: tuple[str, str]
    presented: str  # sign both filings show, e.g. "(1,894,916) / (1,078,580)"


FACT_CORRECTIONS = (
    PresentationFlip(10033, "00164779", 2023, "FY", "name:장기투자자산의취득",
                     30537000000, -28830000000, 59367000000, 1707000000,
                     ("20240319000684", "20231114002574"), "both in parentheses"),
    PresentationFlip(10473, "00252135", 2025, "FY", "name:재무활동으로부터의현금유출",
                     828559000, -647314250, 1475873250, 181244750,
                     ("20260318000323", "20251112000542"), "both in parentheses"),
    PresentationFlip(32360, "00145880", 2024, "H1", "name:재무활동으로인한현금유출액",
                     -6078419758092, 2973711903290, -9052131661382, -3104707854802,
                     ("20240814001754", "20240516001479"), "both in parentheses"),
    PresentationFlip(32673, "00155319", 2024, "H1", "ifrs-full_DividendsPaidClassifiedAsFinancingActivities",
                     -462623611804, 734224443, -463357836247, -461889387361,
                     ("20240814003519", "20240516002077"), "both in parentheses"),
    PresentationFlip(43815, "00937324", 2025, "Q3",
                     "ifrs-full_CashFlowsUsedInObtainingControlOfSubsidiariesOrOtherBusinessesClassifiedAsInvestingActivities",
                     1235539726710, -1235539726710, 2471079453420, 0,
                     ("20251113000643", "20250814003829"), "both 1,235,539,726,710 without parentheses"),
    PresentationFlip(54714, "00382199", 2024, "FY", "name:자기주식의소각비용",
                     102000000, -56000000, 158000000, 46000000,
                     ("20250318000993", "20241114001711"), "both in parentheses"),
    PresentationFlip(56298, "01350869", 2023, "FY",
                     "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets",
                     -1894916000000, 1078580000000, -2973496000000, -816336000000,
                     ("20240516001472", "20231114002524"), "(1,894,916) / (1,078,580)"),
    PresentationFlip(56475, "01350869", 2025, "H1", "dart_AcquisitionOfTreasuryShares",
                     -149860000000, 20673000000, -170533000000, -129187000000,
                     ("20250814004065", "20250609000315"), "both in parentheses"),
    PresentationFlip(56476, "01350869", 2025, "Q3", "dart_AcquisitionOfTreasuryShares",
                     150000000000, -149860000000, 299860000000, 140000000,
                     ("20251114002781", "20250814004065"), "(150,000) / (149,860)"),
    PresentationFlip(56640, "01350869", 2025, "FY", "name:종속기업변동에따른순현금흐름",
                     120121000000, -78023000000, 198144000000, 42098000000,
                     ("20260313001235", "20251114002781"), "(120,121) / (78,023)"),
)


def correction_record(item: PresentationFlip) -> dict:
    """Provenance kept in source_values; quarter regeneration emits the same record."""
    return {"id": CORRECTION_ID, "previous_value": item.old_value,
            "evidence_rcept_nos": list(item.evidence_rcept_nos), "presented": item.presented}


def _fact_payload(item: PresentationFlip) -> tuple[str, str]:
    current, previous = item.report_type, PREVIOUS_REPORT[item.report_type]
    method = (f"{current} cumulative - {previous} cumulative (sign_aligned) "
              f"[{current}:{item.current_cumulative};{previous}:{item.previous_cumulative}]")
    sources = json.dumps({
        current: item.current_cumulative,
        previous: item.previous_cumulative,
        "sign_note": "sign_aligned",
        "correction": correction_record(item),
    }, ensure_ascii=False)
    return method, sources


def apply_corrections(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {"facts_updated": 0, "facts_already": 0, "metrics_updated": 0, "metrics_already": 0,
              "restated_added": 0, "restated_already": 0}
    connection.row_factory = sqlite3.Row
    with connection:
        for item in FACT_CORRECTIONS:
            row = connection.execute(
                """SELECT f.corp_code, p.year, p.report_type, f.account_key, f.value_type, f.is_calculated,
                          f.calculated_value
                   FROM financial_facts f JOIN periods p USING(period_id) WHERE f.fact_id=?""",
                (item.fact_id,)).fetchone()
            expected = (item.corp_code, item.year, item.report_type, item.account_key, "quarterly", 1)
            if row is None or tuple(row)[:6] != expected:
                raise RuntimeError(f"financial_facts {item.fact_id} 식별 정보 불일치: {row and tuple(row)}")
            if row["calculated_value"] == item.new_value:
                counts["facts_already"] += 1
            elif row["calculated_value"] != item.old_value:
                raise RuntimeError(f"financial_facts {item.fact_id} 예상 밖 값: {row['calculated_value']}")
            else:
                method, sources = _fact_payload(item)
                connection.execute(
                    """UPDATE financial_facts SET calculated_value=?, calculation_method=?, source_values=?
                       WHERE fact_id=? AND calculated_value=?""",
                    (item.new_value, method, sources, item.fact_id, item.old_value))
                counts["facts_updated"] += 1
            # Metric rows projecting this fact (DIRECT, same inputs) follow it.
            for metric in connection.execute(
                    """SELECT metric_value_id, value, source_values FROM financial_metric_values
                       WHERE source_kind='DIRECT' AND corp_code=? AND year=? AND report_type=?
                         AND value_type='quarterly'""",
                    (item.corp_code, item.year, item.report_type)).fetchall():
                # Sign-standardized expense rows are compared by their filed value.
                filed = filed_value(metric["value"], metric["source_values"])
                inputs = json.loads(metric["source_values"] or "{}")
                if filed not in (item.old_value, item.new_value) or \
                        (inputs.get(item.report_type), inputs.get(PREVIOUS_REPORT[item.report_type])) != \
                        (item.current_cumulative, item.previous_cumulative):
                    continue
                if filed == item.new_value:
                    counts["metrics_already"] += 1
                    continue
                # The filing presentation resolves the sign question that kept
                # the row in review (sign_convention_corrections.REVIEW_FLAGS).
                connection.execute(
                    """UPDATE financial_metric_values SET value=?, source_values=?, review_status='approved'
                       WHERE metric_value_id=? AND value=?""",
                    (item.new_value, _fact_payload(item)[1], metric["metric_value_id"], item.old_value))
                counts["metrics_updated"] += 1
        count_sql = "SELECT COUNT(*) FROM financial_metric_values WHERE calculation_method=?"
        existing = connection.execute(count_sql, (RESTATED_QUARTER_METHOD,)).fetchone()[0]
        derive_restated_metric_quarters(connection)
        added = connection.execute(count_sql, (RESTATED_QUARTER_METHOD,)).fetchone()[0] - existing
        if existing + added != len(RESTATED_METRIC_QUARTERS):
            raise RuntimeError("KB 재작성 기준 Q4 슬롯 입력값이 고정값과 다릅니다.")
        counts["restated_added"], counts["restated_already"] = added, existing
    connection.row_factory = None
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_staging(source: Path, target: Path) -> dict[str, object]:
    if target.resolve() in (FINANCIAL_DB_PATH.resolve(), source.resolve()):
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
    parser.add_argument("--apply-only", action="store_true", help="기존 staging DB에 보정만 다시 적용한다(멱등).")
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
