from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from unittest import mock

import src.llm.analyzer as analyzer
import src.retrieval.financial_retriever as retriever
from src.config import FINANCIAL_DB_PATH
from src.expense_sign_pipeline import apply_expense_signs
from src.db import residual_corrections
from src.db.financial_metrics import _has_unverified_sign_conflict, materialize_financial_metric_values
from src.industry_kpi_pipeline import apply_industry_kpis
from src.retrieval.industry_kpi import get_industry_kpis, is_bank_company, kpi_coverage

GENERAL = {"KRX_SEMI": "SEMICONDUCTOR", "KRX_HEALTH": "HEALTHCARE", "KRX_STEEL": "STEEL",
           "KRX_AUTO": "AUTOMOTIVE"}


class VerifiedSignConflictTest(unittest.TestCase):
    def test_document_verified_real_reversal_is_approved(self) -> None:
        row = {
            "is_calculated": 1,
            "source_values": json.dumps({
                "H1": 708_619_472_560,
                "Q1": -359_297_424_811,
                "match": "verified_evidence",
                "verified_evidence": {
                    "category": "document_real_reversal",
                    "basis": "document",
                    "rcept_nos": ["20260814000957", "20260515000988"],
                },
            }),
        }
        self.assertFalse(_has_unverified_sign_conflict(row))

    def test_unverified_opposite_signs_still_require_review(self) -> None:
        row = {
            "is_calculated": 1,
            "source_values": json.dumps({"H1": 100, "Q1": -20}),
        }
        self.assertTrue(_has_unverified_sign_conflict(row))


def _bps(numerator: int, denominator: int) -> int:
    return int((Decimal(numerator) * 10000 / Decimal(denominator)).quantize(Decimal(1), ROUND_HALF_UP))


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class ImplicitProfileKpiTest(unittest.TestCase):
    def test_kpis_without_stored_profile_are_served(self) -> None:
        """KPI rows built with transient profiles stay reachable through the service API."""
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as db:
            stored = dict(db.execute("SELECT corp_code, COUNT(*) FROM financial_metric_values "
                                     "WHERE corp_code NOT IN (SELECT corp_code FROM company_financial_profiles) "
                                     "GROUP BY corp_code"))
        for corp_code, count in stored.items():
            rows = get_industry_kpis(corp_code)
            self.assertEqual(len(rows), count, corp_code)
            self.assertTrue(all(r["industry_profile"] in ("SEMICONDUCTOR", "AUTOMOTIVE", "STEEL", "HEALTHCARE")
                                for r in rows))


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class IndustryKpiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.before = Path(cls.directory.name) / "before.db"
        cls.after = Path(cls.directory.name) / "after.db"
        for target in (cls.before, cls.after):
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as db:
                residual_corrections.apply_corrections(db)  # v8 state (no-op once rolled out)
                apply_expense_signs(db)  # v9 expense signs (no-op once rolled out)
        with closing(sqlite3.connect(cls.after)) as db:
            cls.first = apply_industry_kpis(db)
            cls.second = apply_industry_kpis(db)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def _rows(self, path: Path, sql: str) -> list[tuple]:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
            return sorted(tuple(r) for r in db.execute(sql))

    def test_additive_and_idempotent(self) -> None:
        with closing(sqlite3.connect(f"file:{self.before}?mode=ro", uri=True)) as db:
            general_without_profile = db.execute(
                """SELECT COUNT(*) FROM krx_index_constituents k WHERE k.is_current=1
                     AND k.index_code IN ('KRX_SEMI','KRX_HEALTH','KRX_STEEL','KRX_AUTO')
                     AND k.corp_code NOT IN (SELECT corp_code FROM company_financial_profiles)""").fetchone()[0]
        self.assertEqual(self.first["profiles_added"], general_without_profile)  # 20, or 19 with 에이치브이엠
        self.assertEqual((self.second["profiles_added"], self.second["base_rows_added"]), (0, 0))
        self.assertEqual(self.second["metric_values_before"], self.second["metric_values_after"])
        for table in ("financial_facts", "capex_facts", "company_financial_profiles WHERE corp_code IN "
                      "(SELECT corp_code FROM company_financial_profiles WHERE industry_profile LIKE '%BANK%')"):
            self.assertEqual(self._rows(self.before, f"SELECT * FROM {table}"),
                             self._rows(self.after, f"SELECT * FROM {table}"), table)
        existing = self._rows(self.before, "SELECT * FROM financial_metric_values")
        after = set(self._rows(self.after, "SELECT * FROM financial_metric_values"))
        self.assertTrue(set(existing) <= after)

    def test_profiles_follow_krx_groups_and_statement_scope(self) -> None:
        with closing(sqlite3.connect(f"file:{self.after}?mode=ro", uri=True)) as db:
            rows = db.execute(
                """SELECT k.index_code, p.industry_profile, p.preferred_fs_div, p.capex_applicable,
                          (SELECT GROUP_CONCAT(DISTINCT fs_div) FROM financial_facts f WHERE f.corp_code=p.corp_code)
                   FROM company_financial_profiles p JOIN krx_index_constituents k
                     ON k.corp_code=p.corp_code AND k.is_current=1
                   WHERE k.index_code IN ('KRX_SEMI','KRX_HEALTH','KRX_STEEL','KRX_AUTO')"""
            ).fetchall()
        self.assertEqual(len(rows), 20)
        for index_code, profile, fs_div, capex, fact_scope in rows:
            self.assertEqual(profile, GENERAL[index_code])
            self.assertEqual(fs_div, fact_scope)
            self.assertEqual(capex, 1)

    def test_ratios_recompute_from_source_accounts(self) -> None:
        with closing(sqlite3.connect(f"file:{self.after}?mode=ro", uri=True)) as db:
            fact = {}
            for corp, year, report, value_type, account, value in db.execute(
                    """SELECT f.corp_code, p.year, p.report_type, f.value_type, f.account_id,
                              COALESCE(f.normalized_value, f.calculated_value)
                       FROM financial_facts f JOIN periods p USING(period_id)
                       WHERE f.account_id IN ('ifrs-full_Revenue','dart_OperatingIncomeLoss',
                                              'ifrs-full_Liabilities','ifrs-full_Equity')
                         AND f.sj_div IN ('IS','CIS','BS')
                       ORDER BY COALESCE(f.ord, 999999), f.fact_id DESC"""):
                fact[(corp, year, report, value_type, account)] = value
            checked = 0
            for corp, year, report, value_type, metric, value, sources in db.execute(
                    """SELECT corp_code, year, report_type, value_type, metric_code, value, source_values
                       FROM financial_metric_values
                       WHERE metric_code IN ('OPERATING_MARGIN','DEBT_RATIO')"""):
                inputs = json.loads(sources)
                num_account, den_account = (("dart_OperatingIncomeLoss", "ifrs-full_Revenue")
                                            if metric == "OPERATING_MARGIN"
                                            else ("ifrs-full_Liabilities", "ifrs-full_Equity"))
                numerator = fact.get((corp, year, report, value_type, num_account))
                denominator = fact.get((corp, year, report, value_type, den_account))
                if numerator is None or denominator is None:
                    continue  # input came from an alternative tag or a derived quarter
                if inputs["numerator"]["value"] != numerator or inputs["denominator"]["value"] != denominator:
                    continue  # a different (higher-priority or calculated) source was selected
                self.assertEqual(value, _bps(numerator, denominator), (corp, year, report, metric))
                checked += 1
            self.assertGreater(checked, 300)

    def test_rebuild_reproduces_kpis(self) -> None:
        copy = Path(self.directory.name) / "rebuild.db"
        with closing(sqlite3.connect(self.after)) as src, closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        key = "corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind"
        with closing(sqlite3.connect(copy)) as db:
            before = {r[:8]: r[8:] for r in db.execute(f"SELECT {key},value,review_status FROM financial_metric_values")}
            with db:
                materialize_financial_metric_values(db)
            db.row_factory = None
            after = {r[:8]: r[8:] for r in db.execute(f"SELECT {key},value,review_status FROM financial_metric_values")}
        self.assertEqual(before, after)

    def test_kpi_api_and_coverage(self) -> None:
        samsung = "00126380"
        rows = get_industry_kpis(samsung, 2024, "FY", value_type="annual", path=self.after)
        self.assertIn("OPERATING_MARGIN", {r["metric_code"] for r in rows})
        self.assertTrue(all(r["industry_profile"] == "SEMICONDUCTOR" for r in rows))
        coverage = kpi_coverage(self.after)
        self.assertEqual(len(coverage), 20)
        revenue = coverage["삼성전자"]["metrics"]["REVENUE"]
        self.assertEqual(revenue["missing"], [])  # every period with facts (2023-2025, 2026 Q1/H1)
        self.assertEqual(revenue["periods"], revenue["of"])
        self.assertGreaterEqual(revenue["of"], 12)
        self.assertTrue(is_bank_company("신한지주", self.after))
        self.assertFalse(is_bank_company("삼성전자", self.after))

    def test_auto_scope_changes_only_for_separate_only_company(self) -> None:
        """With profiles, AUTO picks OFS for the OFS-only company; the other 24 are unchanged."""
        with closing(sqlite3.connect(f"file:{self.before}?mode=ro", uri=True)) as db:
            names = [r[0] for r in db.execute("SELECT company_name FROM companies ORDER BY company_name")]
            periods = list(db.execute("SELECT year, report_type FROM periods ORDER BY year, report_type"))
        for name in names:
            for year, report in periods:
                after = retriever.get_financial_context(name, year, report, fs_div="AUTO", path=self.after)
                if name == "에이치브이엠":
                    self.assertEqual(after, retriever.get_financial_context(
                        name, year, report, fs_div="OFS", path=self.after))
                else:
                    self.assertEqual(after, retriever.get_financial_context(
                        name, year, report, fs_div="AUTO", path=self.before), (name, year, report))

    def test_existing_queries_unchanged_with_backend_patch(self) -> None:
        """Full-account queries and bank metrics are unchanged; the analyzer keeps
        its results once the bank-only patch is applied (simulated here)."""
        with closing(sqlite3.connect(f"file:{self.before}?mode=ro", uri=True)) as db:
            names = [r[0] for r in db.execute("SELECT company_name FROM companies ORDER BY company_name")]
            periods = list(db.execute("SELECT year, report_type FROM periods ORDER BY year, report_type"))
        for fs_div in ("CFS", "OFS"):
            for name in names:
                for year, report in periods[::3]:
                    self.assertEqual(
                        retriever.get_financial_context(name, year, report, fs_div=fs_div, path=self.before),
                        retriever.get_financial_context(name, year, report, fs_div=fs_div, path=self.after))
        for name in ("하나금융지주", "KB금융", "신한지주", "우리금융지주", "카카오뱅크"):
            for year, report in periods:
                self.assertEqual(retriever.get_financial_metrics(name, year, report, path=self.before),
                                 retriever.get_financial_metrics(name, year, report, path=self.after))

        def patched_rows(path: Path):
            def rows(corp_name, year, report_type, account_name):
                metric_rows = analyzer._finance_metric_context(corp_name, year, report_type, account_name) \
                    if is_bank_company(corp_name, path) else []
                return metric_rows or retriever.get_financial_context(
                    corp_name, year, report_type, account_name=account_name)
            return rows

        for name in ("삼성전자", "현대자동차", "셀트리온", "POSCO홀딩스"):
            results = []
            for path in (self.before, self.after):
                with mock.patch.object(retriever, "FINANCIAL_DB_PATH", path), \
                        mock.patch.object(analyzer, "_financial_rows_for_account", patched_rows(path)):
                    results.append(analyzer._quarterly_financial_context("분기별 매출 추이", name, [2024]))
            self.assertEqual(results[0], results[1], name)


if __name__ == "__main__":
    unittest.main()
