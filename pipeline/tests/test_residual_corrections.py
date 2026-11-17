from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.expense_sign_pipeline import apply_expense_signs
from src.financial_cleanup_pipeline import apply_cleanup
from src.db.financial_metrics import materialize_financial_metric_values
from src.db.quarter_backfill import compute_quarters
from src.db.residual_corrections import FACT_CORRECTIONS, apply_corrections
from src.db.verified_quarter_overrides import RESTATED_METRIC_QUARTERS
from src.quality.residual_verification import judge_pair, presented_sign


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class ResidualCorrectionsTest(unittest.TestCase):
    def test_staging_corrections_are_exact_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as v9:
                apply_expense_signs(v9)  # v9 expense signs (no-op once rolled out)
                apply_cleanup(v9)  # v10 cleanup (no-op once rolled out)
            with closing(sqlite3.connect(target)) as db:
                facts_before = {r[0]: r for r in db.execute("SELECT * FROM financial_facts")}
                metrics_before = {r[0]: r for r in db.execute("SELECT * FROM financial_metric_values")}
                first = apply_corrections(db)
                second = apply_corrections(db)
                facts_after = {r[0]: r for r in db.execute("SELECT * FROM financial_facts")}
                metrics_after = {r[0]: r for r in db.execute("SELECT * FROM financial_metric_values")}
                kb = dict(db.execute(
                    """SELECT metric_code, value FROM financial_metric_values
                       WHERE corp_code='00688996' AND year=2023 AND report_type='FY' AND value_type='quarterly'
                         AND metric_code IN ('OWNERS_NET_INCOME','PRETAX_INCOME','OPERATING_INCOME')"""))
                woori = db.execute(
                    "SELECT value, review_status FROM financial_metric_values WHERE metric_value_id=964").fetchone()
                kb_status = {r[0] for r in db.execute(
                    """SELECT review_status FROM financial_metric_values
                       WHERE corp_code='00688996' AND year=2023 AND report_type='FY' AND value_type='quarterly'
                         AND metric_code IN ('OWNERS_NET_INCOME','PRETAX_INCOME','OPERATING_INCOME')""")}

                # Regenerating every quarter from the API cumulatives reproduces
                # the corrected rows exactly (value, formula, inputs record).
                regenerated = compute_quarters(db)
                corrected_ids = {item.fact_id for item in FACT_CORRECTIONS}
                self.assertEqual(regenerated["value_mismatch"], [])
                self.assertEqual(regenerated["missing_from_regeneration"], [])
                self.assertFalse(corrected_ids & {m["fact_id"] for m in regenerated["text_mismatch"]})

                # A full metric rebuild reproduces value, formula, inputs and review status.
                columns = ("corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind,"
                           "value,calculation_method,source_values,review_status")
                staged = sorted(db.execute(f"SELECT {columns} FROM financial_metric_values"))
                with db:
                    materialize_financial_metric_values(db)
                db.row_factory = None
                rebuilt = sorted(db.execute(f"SELECT {columns} FROM financial_metric_values"))
                self.assertEqual(len(rebuilt), len(staged))
                # Only legacy net-income formula text ('A - B' -> 'A - ABS(B)') differs;
                # value and review status are the same.
                for old, new in zip(staged, rebuilt):
                    if old != new:
                        self.assertEqual((old[:9], old[11]), (new[:9], new[11]))
                        self.assertEqual(old[9].replace(" - ", " - ABS(") + ")", new[9])

        # Before the v8 rollout the first run applies everything; afterwards it is a no-op.
        self.assertEqual((first["facts_updated"] + first["facts_already"],
                          first["metrics_updated"] + first["metrics_already"],
                          first["restated_added"] + first["restated_already"]), (10, 1, 3))
        self.assertEqual((second["facts_updated"], second["metrics_updated"], second["restated_added"]), (0, 0, 0))
        changed = {k for k in facts_before if facts_before[k] != facts_after[k]}
        self.assertEqual(len(changed), first["facts_updated"])
        self.assertTrue(changed <= {item.fact_id for item in FACT_CORRECTIONS})
        self.assertEqual(facts_before.keys(), facts_after.keys())
        self.assertTrue({k for k in metrics_before if metrics_before[k] != metrics_after[k]} <= {964})
        self.assertEqual(len(metrics_after) - len(metrics_before), first["restated_added"])
        for item in FACT_CORRECTIONS:
            self.assertEqual(facts_after[item.fact_id][17], item.new_value)
            self.assertEqual(item.new_value, item.current_cumulative + item.previous_cumulative)
            self.assertEqual(json.loads(facts_after[item.fact_id][19])["sign_note"], "sign_aligned")
        self.assertEqual(woori, (816336000000, "approved"))  # analysis sign; filed -816,336
        self.assertEqual(kb_status, {"review_required"})
        self.assertEqual(kb, {i.metric_code: i.fy_value - i.restated_q3_value for i in RESTATED_METRIC_QUARTERS})
        self.assertEqual(kb["OWNERS_NET_INCOME"], 255418000000)

    def test_document_evidence_matches_known_cases(self) -> None:
        # Woori 2023 credit loss: FY and Q3 both in parentheses -> presentation flip.
        self.assertEqual(presented_sign("01350869", 2023, "FY", "CFS", "CIS", "신용손실에대한손상차손",
                                        -1894916000000), "-")
        verdict = judge_pair("01350869", 2023, "CFS", "CIS",
                             "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets",
                             {"report_type": "FY", "value": -1894916000000, "name": "신용손실에대한손상차손"},
                             {"report_type": "Q3", "value": 1078580000000, "name": "신용손실에대한손상차손"}, False)
        self.assertEqual((verdict["verdict"], verdict["quarter"]), ("presentation_flip", -816336000000))


if __name__ == "__main__":
    unittest.main()
