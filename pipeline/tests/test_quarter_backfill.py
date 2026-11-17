from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from src.config import FINANCIAL_DB_PATH
from src.expense_sign_pipeline import apply_expense_signs
from src.financial_cleanup_pipeline import apply_cleanup
from src.db import residual_corrections
from src.db.financial_metrics import materialize_financial_metric_values
from src.db.quarter_backfill import compute_quarters
from src.financial_residual_fix_pipeline import apply_residual_fixes
from src.preprocess.period_normalizer import _add_calculated_quarterly_values, is_cf_balance_line


def _frame(rows: list[tuple]) -> pd.DataFrame:
    columns = ["corp_code", "year", "fs_div", "sj_div", "account_key", "ord", "report_type",
               "value_type", "normalized_value", "raw_value", "original_account_nm"]
    return pd.DataFrame(rows, columns=columns)


def _quarters(frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    result = _add_calculated_quarterly_values(frame)
    return result.iloc[len(frame):].to_dict("records"), result.attrs["quarter_match_review"]


class AccountIdMatchingTest(unittest.TestCase):
    def test_same_order_keeps_original_pairing(self) -> None:
        rows, review = _quarters(_frame([
            ("C", 2023, "OFS", "CIS", "ifrs-full_ProfitLoss", 31, "FY", "annual", 354, "354", "당기순이익"),
            ("C", 2023, "OFS", "CIS", "ifrs-full_ProfitLoss", 31, "Q3", "cumulative", 279, "279", "분기순이익"),
        ]))
        self.assertEqual([r["calculated_value"] for r in rows], [75])
        self.assertNotIn("match", json.loads(rows[0]["source_values"]))
        self.assertEqual(review, [])

    def test_changed_order_is_matched_by_official_account_id(self) -> None:
        rows, review = _quarters(_frame([
            ("C", 2023, "OFS", "CIS", "ifrs-full_ProfitLoss", 31, "FY", "annual", 354, "354", "IX. 당기순이익"),
            ("C", 2023, "OFS", "CIS", "ifrs-full_ProfitLoss", 28, "Q3", "cumulative", 279, "279", "IX. 분기순이익"),
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["calculated_value"], 75)
        sources = json.loads(rows[0]["source_values"])
        self.assertEqual((sources["match"], sources["ord"]), ("account_id", {"FY": 31, "Q3": 28}))
        self.assertEqual(review, [])

    def test_uncertain_pairs_are_reviewed_not_calculated(self) -> None:
        cases = {
            "account_key_not_official_id": [
                ("C", 2023, "CFS", "CF", "name:기타", 5, "FY", "annual", 10, "10", "기타"),
                ("C", 2023, "CFS", "CF", "name:기타", 4, "Q3", "cumulative", 6, "6", "기타"),
            ],
            "account_id_not_unique_in_report": [
                ("C", 2023, "CFS", "CF", "dart_X", 5, "FY", "annual", 10, "10", "가"),
                ("C", 2023, "CFS", "CF", "dart_X", 9, "FY", "annual", 3, "3", "나"),
                ("C", 2023, "CFS", "CF", "dart_X", 4, "Q3", "cumulative", 6, "6", "가"),
            ],
            "sign_conflict": [
                ("C", 2023, "CFS", "CIS", "ifrs-full_IncomeTaxExpenseContinuingOperations", 5, "FY", "annual",
                 -12, "-12", "법인세비용"),
                ("C", 2023, "CFS", "CIS", "ifrs-full_IncomeTaxExpenseContinuingOperations", 4, "Q3", "cumulative",
                 10, "10", "법인세비용"),
            ],
            "account_name_changed": [
                ("C", 2023, "CFS", "CF", "dart_RepaymentsOfBonds", 5, "FY", "annual", 10, "10", "전환사채의 조기상환"),
                ("C", 2023, "CFS", "CF", "dart_RepaymentsOfBonds", 4, "Q3", "cumulative", 6, "6", "사채의 조기상환"),
            ],
        }
        for reason, data in cases.items():
            with self.subTest(reason=reason):
                rows, review = _quarters(_frame(data))
                self.assertEqual([r for r in rows if r["report_type"] == "FY"], [])
                self.assertIn(reason, {item["reason"] for item in review})


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class ResidualFixPipelineTest(unittest.TestCase):
    def test_stored_quarters_regenerate_exactly(self) -> None:
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as db:
            result = compute_quarters(db)
        # Before the v10 rollout the stored quarters of cash flow balance lines
        # (opening/closing cash) are no longer regenerated; nothing else is missing.
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as db:
            for key in result["missing_from_regeneration"]:
                account_id, name, sj_div, calculated = db.execute(
                    """SELECT account_id, original_account_nm, sj_div, is_calculated FROM financial_facts
                       WHERE corp_code=? AND period_id=? AND fs_div=? AND sj_div=? AND account_key=?
                         AND ord IS ? AND value_type=? AND is_calculated=?""", key).fetchone()
                self.assertTrue(sj_div == "CF" and calculated == 1 and is_cf_balance_line(account_id, name), key)
        self.assertEqual(result["not_stored_ord_pass"], [])
        # Before the v8 rollout the only differences are the pinned residual
        # corrections: stored = old value, regenerated = corrected value.
        pending = {item.fact_id: item for item in residual_corrections.FACT_CORRECTIONS}
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as db:
            ids = {tuple(m["key"]): db.execute(
                """SELECT fact_id FROM financial_facts WHERE corp_code=? AND period_id=? AND fs_div=?
                     AND sj_div=? AND account_key=? AND ord IS ? AND value_type=? AND is_calculated=1""",
                m["key"][:7]).fetchone()[0] for m in result["value_mismatch"]}
        for mismatch in result["value_mismatch"]:
            item = pending[ids[tuple(mismatch["key"])]]
            self.assertEqual((mismatch["stored"], mismatch["recomputed"]), (item.old_value, item.new_value))
        # Text differences are limited to the pending corrections and to legacy
        # opposite-sign rows stored before the review note existed.
        for mismatch in result["text_mismatch"]:
            stored = json.loads(mismatch["stored"][1])
            recomputed = json.loads(mismatch["recomputed"][1])
            self.assertEqual(recomputed.pop("sign_note"), "sign_conflict_review")
            self.assertEqual(stored, recomputed)

    def test_fixes_are_additive_idempotent_and_rebuildable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as v9:
                apply_expense_signs(v9)  # v9 expense signs (no-op once rolled out)
                apply_cleanup(v9)  # v10 cleanup (no-op once rolled out)
            with closing(sqlite3.connect(target)) as db:
                residual_corrections.apply_corrections(db)  # v8 state (no-op once rolled out)
                facts_before = set(db.execute("SELECT * FROM financial_facts"))
                apply_residual_fixes(db)
                db.row_factory = None
                snapshot = sorted(db.execute("SELECT * FROM financial_metric_values"))
                facts_after = set(db.execute("SELECT * FROM financial_facts"))
                self.assertTrue(facts_before <= facts_after)  # API rows untouched

                again = apply_residual_fixes(db)
                db.row_factory = None
                self.assertEqual((again["facts_inserted"], again["metric_rows_added"]), (0, 0))
                self.assertEqual(sorted(db.execute("SELECT * FROM financial_metric_values")), snapshot)

                kakao = dict(db.execute(
                    """SELECT year, value FROM financial_metric_values
                       WHERE corp_code='01133217' AND report_type='FY' AND value_type='quarterly'
                         AND metric_code='NET_INCOME'"""))
                self.assertEqual((kakao[2023], kakao[2024]), (75_660_000_000, 84_457_000_000))

                woori = db.execute(
                    """SELECT year, report_type, value, review_status, source_values
                       FROM financial_metric_values
                       WHERE corp_code='01350869' AND metric_code='LOANS_AT_AMORTISED_COST_NOTE'
                       ORDER BY year, report_type"""
                ).fetchall()
                self.assertEqual(len(woori), 12)
                self.assertTrue(all(row[3] == "approved" for row in woori))
                q1 = next(row for row in woori if row[:2] == (2023, "Q1"))
                self.assertEqual(q1[2], 337_391_677_000_000)
                self.assertEqual(json.loads(q1[4])["composition"]["합계"], 355_155_285_000_000)
                combined = db.execute(
                    """SELECT value FROM financial_metric_values WHERE corp_code='01350869' AND year=2023
                         AND report_type='Q1' AND metric_code='LOANS_AT_AMORTISED_COST'"""
                ).fetchone()
                self.assertEqual(combined[0], 355_155_285_000_000)  # combined line kept as is

                verified = dict(((c, y, m), v) for c, y, m, v in db.execute(
                    """SELECT corp_code, year, metric_code, value FROM financial_metric_values
                       WHERE report_type='FY' AND value_type='quarterly' AND review_status='approved'
                         AND source_values LIKE '%verified_override%'"""))
                self.assertEqual(verified, {
                    ("00382199", 2023, "OPERATING_INCOME"): 904_484_000_000,
                    ("01350869", 2025, "CREDIT_LOSS_EXPENSE"): 583_845_000_000,
                    ("01350869", 2025, "NET_INCOME"): 341_705_000_000,
                    ("00382199", 2024, "CREDIT_LOSS_EXPENSE"): 613_535_000_000,
                    ("00688996", 2024, "CREDIT_LOSS_EXPENSE"): 565_143_000_000,
                    ("01350869", 2024, "CREDIT_LOSS_EXPENSE"): 462_525_000_000,
                    ("01133217", 2023, "OPERATING_INCOME"): 102_793_000_000,
                    ("00382199", 2024, "INTEREST_EXPENSE"): 4_461_635_000_000,
                    # Analysis sign (expense positive); filed values -69,617 / -254,854.
                    ("01133217", 2023, "FEE_COMMISSION_EXPENSE"): 69_617_000_000,
                    ("01133217", 2023, "INTEREST_EXPENSE"): 254_854_000_000,
                })
                # KB 2023 Q4 from FY and the Q3 cumulative restated to the FY basis;
                # kept in review because stored Q1-Q3 stay on the earlier basis.
                kb = {r[0]: r[1:] for r in db.execute(
                    """SELECT metric_code, value, review_status FROM financial_metric_values
                       WHERE corp_code='00688996' AND year=2023 AND report_type='FY' AND value_type='quarterly'
                         AND metric_code IN ('OWNERS_NET_INCOME','PRETAX_INCOME','OPERATING_INCOME')""")}
                self.assertEqual(kb, {"OWNERS_NET_INCOME": (255_418_000_000, "review_required"),
                                      "PRETAX_INCOME": (263_036_000_000, "review_required"),
                                      "OPERATING_INCOME": (295_972_000_000, "review_required")})

                key = "corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind"
                before = {r[:8]: r[8:] for r in db.execute(f"SELECT {key},value,review_status FROM financial_metric_values")}
                with db:
                    materialize_financial_metric_values(db)
                db.row_factory = None
                after = {r[:8]: r[8:] for r in db.execute(f"SELECT {key},value,review_status FROM financial_metric_values")}
                self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
