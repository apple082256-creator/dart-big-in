from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import src.retrieval.financial_retriever as retriever
from src.company_profile_fix_pipeline import HVM_PROFILE, apply_profile_fix
from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT
from src.db.financial_metrics import add_missing_fact_metrics, materialize_financial_metric_values

MIGRATIONS = PROJECT_ROOT / "infra" / "mysql" / "migrations"
# The view is redefined by later migrations (007 adds the negative-CAPEX filter);
# the latest definition is the one deployed and mirrored in init/01_schema.sql.
MIGRATION = sorted(path for path in MIGRATIONS.glob("*.sql")
                   if "CREATE OR REPLACE VIEW standardized_financials" in path.read_text(encoding="utf-8"))[-1]


class StandardViewMigrationTest(unittest.TestCase):
    def test_core_accounts_are_matched_by_official_tag(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        tags = re.findall(r"SELECT '([^']+)', '([^']+)', '(IS|BS|CF)', (\d)", sql)
        mapped = {(name, tag) for name, tag, _, _ in tags}
        for expected in (("당기순이익", "ifrs-full_ProfitLoss"), ("영업이익", "dart_OperatingIncomeLoss"),
                         ("영업이익", "ifrs-full_ProfitLossFromOperatingActivities"),
                         ("영업활동현금흐름", "ifrs-full_CashFlowsFromUsedInOperatingActivities"),
                         ("자본총계", "ifrs-full_Equity")):
            self.assertIn(expected, mapped)
        self.assertTrue(all(tag.startswith(("ifrs-full_", "dart_")) for _, tag, _, _ in tags))
        # one row per slot, balances only from the balance sheet
        self.assertIn("PARTITION BY c.corp_code, c.year, c.report_type, c.fs_div, c.tag_standard_account, c.value_type",
                      sql)
        self.assertIn("f.sj_div = 'BS' AND f.value_type = 'point_in_time'", sql)
        self.assertIn("WHERE r.pick = 1", sql)
        self.assertIn(sql[sql.index("WITH tags"):].strip(), (PROJECT_ROOT / "infra" / "mysql" / "init" /
                                                            "01_schema.sql").read_text(encoding="utf-8"))


def _metric_rows(db: sqlite3.Connection) -> dict[tuple, tuple]:
    return {tuple(row[:8]): tuple(row[8:]) for row in db.execute(
        "SELECT corp_code, year, report_type, metric_code, value_type, fs_div, scope, source_kind, "
        "value, review_status, source_values FROM financial_metric_values")}


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class SeparateOnlyProfileTest(unittest.TestCase):
    def test_profile_selects_ofs_without_changing_others_or_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = Path(directory) / "before.db"
            after = Path(directory) / "after.db"
            for target in (before, after):
                with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                        closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
            with closing(sqlite3.connect(after)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                self.assertIn(apply_profile_fix(db)["added"], (0, 1))
                self.assertEqual(apply_profile_fix(db)["added"], 0)
                metrics = _metric_rows(db)
                with db:
                    add_missing_fact_metrics(db)  # no FK error from the profile
                # Existing KPI rows stay as they are; the operational DB holds only part of
                # the 2023-2025 fact KPIs, so a fill may add rows but never change one.
                filled = _metric_rows(db)
                self.assertEqual({key: filled.get(key) for key in metrics}, metrics)
                with db:
                    materialize_financial_metric_values(db)
                # A rebuild may enrich source_values; values and review states must not move.
                rebuilt = _metric_rows(db)
                self.assertEqual({key: (rebuilt.get(key) or ())[:2] for key in metrics
                                  if key[0] != HVM_PROFILE["corp_code"]},
                                 {key: value[:2] for key, value in metrics.items()
                                  if key[0] != HVM_PROFILE["corp_code"]})
                # The separate-only profile yields separate-statement KPIs only.
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM financial_metric_values WHERE corp_code=? AND fs_div<>'OFS'",
                    (HVM_PROFILE["corp_code"],)).fetchone()[0], 0)
            for year, report in ((2024, "FY"), (2025, "H1"), (2025, "FY")):
                self.assertEqual(retriever.get_financial_context("에이치브이엠", year, report, path=after),
                                 retriever.get_financial_context("에이치브이엠", year, report, fs_div="OFS",
                                                                 path=before))
                self.assertTrue(retriever.get_financial_context("에이치브이엠", year, report, path=after))
                for name in ("POSCO홀딩스", "카카오뱅크", "삼성전자"):
                    self.assertEqual(retriever.get_financial_context(name, year, report, path=after),
                                     retriever.get_financial_context(name, year, report, path=before))


if __name__ == "__main__":
    unittest.main()
