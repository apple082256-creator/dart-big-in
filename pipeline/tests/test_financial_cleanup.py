from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import pandas as pd

from src.config import FINANCIAL_DB_PATH
from src.db.capex_db import materialize_capex_tables
from src.db.quarter_backfill import _raw_frame
from src.db.verified_quarter_overrides import evidence_quarter_index
from src.financial_cleanup_pipeline import apply_cleanup
from src.preprocess.period_normalizer import _add_calculated_quarterly_values, is_cf_balance_line


def _cumulative(rows: list[tuple]) -> pd.DataFrame:
    """(report_type, account_id, account_key, name, value, ord) cumulative CF rows of one company/year."""
    records = []
    for report, account_id, key, name, value, ord_value in rows:
        records.append({"corp_code": "00000001", "year": 2025, "report_type": report, "fs_div": "CFS",
                        "sj_div": "CF", "account_key": key, "account_id": account_id,
                        "original_account_nm": name, "value_type": "annual" if report == "FY" else "cumulative",
                        "normalized_value": value, "raw_value": str(value), "ord": ord_value, "period_id": 1})
    frame = pd.DataFrame(records)
    frame["ord"] = frame["ord"].astype("Int64")
    return frame


class BalanceLineTest(unittest.TestCase):
    def test_balance_lines_get_no_quarter(self) -> None:
        self.assertTrue(is_cf_balance_line("dart_CashAndCashEquivalentsAtEndOfPeriodCf", "기말현금"))
        self.assertTrue(is_cf_balance_line("-표준계정코드 미사용-", "반기말의 현금및현금성자산"))
        self.assertFalse(is_cf_balance_line("-표준계정코드 미사용-", "현금및현금성자산의 순증가"))
        frame = _cumulative([
            ("Q1", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "dart_CashAndCashEquivalentsAtEndOfPeriodCf",
             "기말현금", 100, 50),
            ("H1", "dart_CashAndCashEquivalentsAtEndOfPeriodCf", "dart_CashAndCashEquivalentsAtEndOfPeriodCf",
             "기말현금", 120, 50),
            ("Q1", "ifrs-full_DividendsPaid", "ifrs-full_DividendsPaid", "배당금지급", -10, 30),
            ("H1", "ifrs-full_DividendsPaid", "ifrs-full_DividendsPaid", "배당금지급", -25, 30),
        ])
        result = _add_calculated_quarterly_values(frame)
        derived = result.iloc[len(frame):]
        self.assertEqual(set(derived["account_key"]), {"ifrs-full_DividendsPaid"})


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class EvidenceManifestTest(unittest.TestCase):
    def test_manifest_applies_only_to_exact_inputs(self) -> None:
        entry = next(e for e in evidence_quarter_index().values() if e["sign_aligned"])
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as db:
            frame = _raw_frame(db)
        group = frame[(frame["corp_code"] == entry["corp_code"]) & (frame["year"] == entry["year"])
                      & (frame["fs_div"] == entry["fs_div"]) & (frame["sj_div"] == entry["sj_div"])
                      & (frame["account_key"] == entry["account_key"])].copy()

        def derived(data: pd.DataFrame) -> pd.DataFrame:
            out = _add_calculated_quarterly_values(data).iloc[len(data):]
            return out[out["report_type"] == entry["report_type"]]

        rows = derived(group)
        self.assertEqual(rows["calculated_value"].tolist(), [entry["quarter"]])
        # A re-filed value (one unit different) is not derived from the pinned evidence.
        changed = group.copy()
        mask = (changed["report_type"] == entry["report_type"]) & changed["value_type"].isin(["cumulative", "annual"])
        changed.loc[mask, "normalized_value"] = changed.loc[mask, "normalized_value"] + 1
        self.assertTrue(derived(changed).empty)

    def test_cleanup_is_additive_on_api_rows_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as db:
                api_before = set(db.execute("SELECT * FROM financial_facts WHERE is_calculated=0"))
                metrics_before = {row[0]: row for row in db.execute("SELECT * FROM financial_metric_values")}
                first = apply_cleanup(db)
                second = apply_cleanup(db)
                self.assertEqual(set(db.execute("SELECT * FROM financial_facts WHERE is_calculated=0")), api_before)
                # Existing KPI rows are untouched. The fill may add fact KPIs the operational DB
                # has not loaded yet (healthcare/HVM 2023-2025), so compare the existing rows only.
                metrics_after = {row[0]: row for row in db.execute("SELECT * FROM financial_metric_values")}
                self.assertEqual({key: metrics_after.get(key) for key in metrics_before}, metrics_before)
                self.assertEqual((second["balance_line_rows_removed"], second["verified_quarters_inserted"]), (0, 0))
                self.assertEqual(second["stored_quarters_before"], first["stored_quarters_before"]
                                 - first["balance_line_rows_removed"] + first["verified_quarters_inserted"])
                self.assertEqual(db.execute(
                    """SELECT COUNT(*) FROM financial_facts WHERE is_calculated=1 AND sj_div='CF'
                         AND account_id IN ('dart_CashAndCashEquivalentsAtEndOfPeriodCf',
                                            'dart_CashAndCashEquivalentsAtBeginningOfPeriodCf')""").fetchone()[0], 0)

    def test_capex_rebuild_reproduces_rows_and_skips_banks(self) -> None:
        columns = ("corp_code,period_id,fs_div,value_type,cash_outflow_amount,capex_method,source_values,"
                   "is_calculated,review_required")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                before = set(dst.execute(f"SELECT {columns} FROM capex_facts"))
            materialize_capex_tables(target, validation_path=Path(directory) / "capex.csv")
            with closing(sqlite3.connect(target)) as db:
                after = set(db.execute(f"SELECT {columns} FROM capex_facts"))
                changed_before = {row for row in before if row[4] in {2_587_175_412, 912_275_556}}
                changed_after = {
                    row for row in after
                    if row[0] in {"00199252", "01011368"} and row[3] == "quarterly" and row[4] is None
                }
                if changed_before:
                    self.assertEqual(before - changed_before, after - changed_after)
                    self.assertEqual(len(changed_before), 2)
                else:
                    self.assertEqual(before, after)
                self.assertEqual(len(changed_after), 2)
                self.assertTrue(all(row[8] == 1 for row in changed_after))
                self.assertEqual(db.execute(
                    """SELECT COUNT(*) FROM capex_facts WHERE corp_code IN
                         (SELECT corp_code FROM company_financial_profiles WHERE capex_applicable=0)""").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
