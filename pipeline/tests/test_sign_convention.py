from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH
from src.expense_sign_pipeline import apply_expense_signs
from src.db.financial_metrics import materialize_financial_metric_values
from src.db.sign_convention_corrections import (
    FACT_CORRECTIONS,
    METRIC_CORRECTIONS,
    REVIEW_FLAGS,
    apply_corrections,
)
from src.llm.analyzer import _quarterly_financial_context
from src.preprocess.period_normalizer import _quarter_difference


class QuarterDifferenceTest(unittest.TestCase):
    def test_same_sign_is_plain_difference(self) -> None:
        self.assertEqual(
            _quarter_difference("00547583", 2024, "FY", "ifrs-full_FeeAndCommissionExpense", 1_000, 700),
            (300, None),
        )

    def test_only_verified_inputs_are_sign_aligned(self) -> None:
        self.assertEqual(
            _quarter_difference("00547583", 2023, "FY", "ifrs-full_FeeAndCommissionExpense",
                                1_057_187_000_000, -766_549_000_000),
            (290_638_000_000, "sign_aligned"),
        )

    def test_unverified_reversals_keep_value_and_are_flagged(self) -> None:
        # Tax expense through Q3 turning into a full-year tax benefit is a real
        # reversal; it must not be "corrected" without external evidence.
        self.assertEqual(
            _quarter_difference("00547583", 2025, "FY", "ifrs-full_IncomeTaxExpenseContinuingOperations",
                                -120, 100),
            (-220, "sign_conflict_review"),
        )
        # Same account and year as a verified row, different inputs: not aligned.
        self.assertEqual(
            _quarter_difference("00547583", 2023, "FY", "ifrs-full_FeeAndCommissionExpense", 500, -200),
            (700, "sign_conflict_review"),
        )


class DerivedMetricSignTest(unittest.TestCase):
    def _connection(self, expense: int, year: int = 2023) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE companies(corp_code TEXT PRIMARY KEY, company_name TEXT);
            CREATE TABLE periods(period_id INTEGER PRIMARY KEY, year INTEGER, report_type TEXT);
            CREATE TABLE financial_facts(
                fact_id INTEGER PRIMARY KEY, corp_code TEXT, period_id INTEGER, rcept_no TEXT,
                fs_div TEXT, sj_div TEXT, account_key TEXT, account_id TEXT, original_account_nm TEXT,
                ord INTEGER, value_type TEXT, currency TEXT, normalized_value INTEGER,
                calculated_value INTEGER, source_values TEXT, is_calculated INTEGER);
            INSERT INTO companies VALUES ('01133217','카카오뱅크');
            """
        )
        connection.execute("INSERT INTO periods VALUES (1, ?, 'FY')", (year,))
        from src.db.financial_metrics import initialize_financial_metric_schema, upsert_company_profiles
        initialize_financial_metric_schema(connection)
        upsert_company_profiles(connection, [{
            "corp_code": "01133217", "industry_profile": "INTERNET_BANK", "preferred_fs_div": "OFS",
            "comparison_scope": "bank_separate", "valid_from": "2023-01-01",
        }])
        facts = (
            (1, "ifrs-full_RevenueFromInterest", "이자수익", 2_048_170),
            (2, "ifrs-full_InterestExpense", "이자비용", expense),
        )
        for fact_id, account_id, name, value in facts:
            connection.execute(
                """INSERT INTO financial_facts VALUES
                   (?, '01133217', 1, 'R', 'OFS', 'CIS', ?, ?, ?, ?, 'annual', 'KRW', ?, NULL, '{}', 0)""",
                (fact_id, account_id, account_id, name, fact_id, value),
            )
        return connection

    def test_net_interest_income_ignores_expense_presentation_sign(self) -> None:
        for expense in (932_200, -932_200):
            with self.subTest(expense=expense), closing(self._connection(expense)) as connection:
                materialize_financial_metric_values(connection)
                row = connection.execute(
                    """SELECT value, calculation_method, source_values FROM financial_metric_values
                       WHERE metric_code='NET_INTEREST_INCOME' AND source_kind='DERIVED'"""
                ).fetchone()
                self.assertEqual(row[0], 1_115_970)
                self.assertEqual(row[1], "INTEREST_INCOME - ABS(INTEREST_EXPENSE)")
                self.assertEqual(json.loads(row[2])["expense_sign_normalized"], expense < 0)

    def test_unverified_negative_expense_is_review_required(self) -> None:
        cases = ((2023, -932_200, "approved"), (2024, -932_200, "review_required"),
                 (2024, 932_200, "approved"))
        for year, expense, expected in cases:
            with self.subTest(year=year, expense=expense), \
                    closing(self._connection(expense, year)) as connection:
                materialize_financial_metric_values(connection)
                status = connection.execute(
                    """SELECT review_status FROM financial_metric_values
                       WHERE metric_code='NET_INTEREST_INCOME' AND source_kind='DERIVED'"""
                ).fetchone()[0]
                self.assertEqual(status, expected)


class SignCorrectionScriptTest(unittest.TestCase):
    def test_corrections_are_targeted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as v9:
                apply_expense_signs(v9)  # v9 expense signs (no-op once rolled out)
            with closing(sqlite3.connect(target)) as connection:
                untouched_before = connection.execute(
                    "SELECT COUNT(*), TOTAL(value) FROM financial_metric_values WHERE metric_value_id NOT IN ("
                    + ",".join(str(item.metric_value_id) for item in METRIC_CORRECTIONS) + ")"
                ).fetchone()
                apply_corrections(connection)
                second = apply_corrections(connection)
                self.assertEqual(second["facts_updated"] + second["metrics_updated"], 0)
                self.assertEqual(second["facts_already"], len(FACT_CORRECTIONS))
                self.assertEqual(second["metrics_already"], len(METRIC_CORRECTIONS))
                # After residual-v8 the Woori flag is superseded by the verified value.
                self.assertEqual(second["review_already"] + second["review_superseded"], len(REVIEW_FLAGS))
                for item in METRIC_CORRECTIONS:
                    value = connection.execute(
                        "SELECT value FROM financial_metric_values WHERE metric_value_id=?",
                        (item.metric_value_id,),
                    ).fetchone()[0]
                    self.assertEqual(value, item.new_value)
                untouched_after = connection.execute(
                    "SELECT COUNT(*), TOTAL(value) FROM financial_metric_values WHERE metric_value_id NOT IN ("
                    + ",".join(str(item.metric_value_id) for item in METRIC_CORRECTIONS) + ")"
                ).fetchone()
                self.assertEqual(untouched_before, untouched_after)

                # The corrected snapshot must match what the fixed materialization
                # produces: every row by value/status, touched rows column by column.
                key = "corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind"
                full = ("value,unit,source_rcept_no,source_account_id,source_account_name,"
                        "source_fact_key,calculation_method,source_values,review_status")
                touched = ",".join(str(i) for i in
                                   [m.metric_value_id for m in METRIC_CORRECTIONS]
                                   + [f.metric_value_id for f in REVIEW_FLAGS])
                touched_keys = {row[:8] for row in connection.execute(
                    f"SELECT {key} FROM financial_metric_values WHERE metric_value_id IN ({touched})")}
                snapshot = {row[:8]: row[8:] for row in connection.execute(
                    f"SELECT {key},{full} FROM financial_metric_values")}
                with connection:
                    materialize_financial_metric_values(connection)
                connection.row_factory = None
                rebuilt = {row[:8]: row[8:] for row in connection.execute(
                    f"SELECT {key},{full} FROM financial_metric_values")}
                # The rebuild may add bank Q4 nets that the snapshot predates, and fact KPIs of
                # non-bank profiles the operational DB has not loaded yet (healthcare/HVM 2023-2025).
                added = rebuilt.keys() - snapshot.keys()
                self.assertEqual(snapshot.keys() - rebuilt.keys(), set())
                banks = {row[0] for row in connection.execute(
                    "SELECT corp_code FROM company_financial_profiles "
                    "WHERE industry_profile IN ('BANK_HOLDING', 'INTERNET_BANK')")}
                self.assertTrue(all((rebuilt[key][6] or "").startswith("FY annual - Q3 cumulative")
                                    for key in added if key[0] in banks))
                for row_key, values in snapshot.items():
                    if row_key in touched_keys:
                        self.assertEqual(values, rebuilt[row_key], row_key)
                    else:
                        self.assertEqual((values[0], values[-1]), (rebuilt[row_key][0], rebuilt[row_key][-1]),
                                         row_key)


class RuntimeQ4GuardTest(unittest.TestCase):
    def test_opposite_sign_inputs_do_not_produce_runtime_q4(self) -> None:
        rows = _quarterly_financial_context("분기별 이자비용 추이", "카카오뱅크", [2023])
        fabricated = [
            row for row in rows
            if row["quarter"] == 4 and row.get("calculated_value") == -1_609_546_000_000
        ]
        self.assertEqual(fabricated, [])


if __name__ == "__main__":
    unittest.main()
