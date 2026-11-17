from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from src.config import FINANCIAL_DB_PATH
from src.db import expense_sign
from src.db.financial_metrics import materialize_financial_metric_values
from src.expense_sign_pipeline import apply_expense_signs
from src.quality import financial_db_audit
from src.quality.expense_sign_evidence import judge

EXPENSES = expense_sign.EXPENSE_METRICS
COLUMNS = ("corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind,"
           "value,calculation_method,source_values,review_status")


def _copy(directory: str) -> Path:
    target = Path(directory) / "financial.db"
    with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    return target


def _sign_finding(db: sqlite3.Connection) -> dict:
    return next(f for f in financial_db_audit.check_metric_layer(db) if "analysis sign" in f["check"])


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class ExpenseSignTest(unittest.TestCase):
    def test_standardization_is_targeted_reproducible_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(_copy(directory))) as db:
            facts_before = set(db.execute("SELECT * FROM financial_facts"))
            others_before = set(db.execute(
                f"SELECT {COLUMNS} FROM financial_metric_values WHERE metric_code NOT IN (?,?,?)", EXPENSES))
            apply_expense_signs(db)
            db.row_factory = None
            staged = sorted(db.execute(f"SELECT {COLUMNS} FROM financial_metric_values"))
            apply_expense_signs(db)
            db.row_factory = None
            self.assertEqual(sorted(db.execute(f"SELECT {COLUMNS} FROM financial_metric_values")), staged)

            self.assertEqual(set(db.execute("SELECT * FROM financial_facts")), facts_before)
            self.assertEqual(set(db.execute(
                f"SELECT {COLUMNS} FROM financial_metric_values WHERE metric_code NOT IN (?,?,?)", EXPENSES)),
                others_before)  # net interest/fee income and everything else unchanged
            rows = db.execute(
                """SELECT year, value, source_values FROM financial_metric_values
                   WHERE metric_code IN (?,?,?) AND corp_code IN
                     (SELECT corp_code FROM company_financial_profiles
                      WHERE industry_profile IN ('BANK_HOLDING','INTERNET_BANK'))""", EXPENSES).fetchall()
            # 120 rows per full year (5 banks x 3 metrics x 8 slots); 2026 holds Q1/H1 only.
            per_year = {year: 0 for year in (2023, 2024, 2025)}
            for year, _, _ in rows:
                per_year[year] = per_year.get(year, 0) + 1
            self.assertEqual({y: n for y, n in per_year.items() if y <= 2025}, {2023: 120, 2024: 120, 2025: 120})
            self.assertIn(per_year.get(2026, 0), (0, 60))
            for _, value, sources in rows:
                record = json.loads(sources)[expense_sign.KEY]
                self.assertEqual(record["status"], "standardized")
                self.assertEqual(abs(value), abs(record["original_value"]))
                self.assertGreater(value, 0)  # every loaded period is a proven expense
            woori = db.execute(
                """SELECT value FROM financial_metric_values WHERE corp_code='01350869' AND year=2024
                     AND metric_code='CREDIT_LOSS_EXPENSE' AND value_type='quarterly' ORDER BY report_type""")
            self.assertEqual([r[0] for r in woori], [462525000000, 408980000000, 366512000000, 478278000000])
            self.assertEqual(_sign_finding(db)["issues"], 0)

            with db:  # a full rebuild reproduces the standardized rows exactly
                materialize_financial_metric_values(db)
            db.row_factory = None
            rebuilt = set(db.execute(f"SELECT {COLUMNS} FROM financial_metric_values WHERE metric_code IN (?,?,?)",
                                     EXPENSES))
            self.assertEqual(rebuilt, {r for r in staged if r[3] in EXPENSES})

            # The audit catches a flipped sign and a changed size.
            metric_id = db.execute(
                "SELECT metric_value_id FROM financial_metric_values WHERE metric_code='CREDIT_LOSS_EXPENSE' "
                "AND corp_code='01350869' AND year=2023 AND report_type='FY' AND value_type='annual'").fetchone()[0]
            db.execute("UPDATE financial_metric_values SET value=-value WHERE metric_value_id=?", (metric_id,))
            self.assertGreaterEqual(_sign_finding(db)["issues"], 1)
            db.execute("UPDATE financial_metric_values SET value=-value+1 WHERE metric_value_id=?", (metric_id,))
            self.assertGreaterEqual(_sign_finding(db)["issues"], 1)

    def test_rows_without_evidence_keep_filed_value_in_review(self) -> None:
        manifest = {k: v for k, v in expense_sign._manifest().items()
                    if not (k[0] == "01350869" and k[1] == 2024 and k[2] == "Q3" and k[4] == "CREDIT_LOSS_EXPENSE")}
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(_copy(directory))) as db, \
                mock.patch.object(expense_sign, "_manifest", lambda: manifest):
            apply_expense_signs(db)
            db.row_factory = None
            rows = dict(((r, t), (v, s)) for r, t, v, s in db.execute(
                """SELECT report_type, value_type, value, review_status FROM financial_metric_values
                   WHERE corp_code='01350869' AND year=2024 AND metric_code='CREDIT_LOSS_EXPENSE'"""))
            self.assertEqual(rows[("Q3", "cumulative")], (-1253770000000, "review_required"))
            self.assertEqual(rows[("Q3", "quarterly")], (-478278000000, "review_required"))
            self.assertEqual(rows[("FY", "quarterly")][1], "review_required")
            self.assertEqual(rows[("H1", "cumulative")], (775492000000, "approved"))
            self.assertEqual(_sign_finding(db)["issues"], 0)

    def test_evidence_from_filed_statements(self) -> None:
        hana = judge("00547583", 2025, "Q1", "CFS", "CREDIT_LOSS_EXPENSE", "신용손실충당금전입액", 297226000000)
        self.assertEqual((hana["meaning"], hana["basis"]), ("expense", "subtotal"))
        woori = judge("01350869", 2023, "FY", "CFS", "CREDIT_LOSS_EXPENSE", "신용손실에대한손상차손", -1894916000000)
        self.assertEqual((woori["meaning"], woori["basis"]), ("expense", "reference_line"))
        interest = judge("00382199", 2023, "FY", "CFS", "INTEREST_EXPENSE", "이자비용", -16761289000000)
        self.assertEqual((interest["meaning"], interest["basis"]), ("expense", "subtotal"))


if __name__ == "__main__":
    unittest.main()
