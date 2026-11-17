from __future__ import annotations

import unittest

from src.config import FINANCIAL_DB_PATH
from src.quality.financial_db_audit import run_audit

MUST_BE_CLEAN = (
    "stored source value = raw API field",
    "single currency/unit (KRW; BPS for KPI ratios)",
    "no duplicate fact keys",
    "no duplicate metric keys",
    "no CFS/OFS mixing per company",
    "Q1 quarterly = Q1 cumulative (API)",
    "calculated quarter reproduces from inputs",
    "assets = liabilities + equity (API)",
    "DIRECT metric = source fact value",
    "DERIVED metric reproduces from inputs",
    "DOCUMENT_EXTRACTED has document locator",
    "CAPEX quarter = cumulative outflow difference",
    "no CAPEX rows for banks",
    "one standard name per standard account_id",
    "metric rows: definition_and_unit",
    "metric rows: period",
    "metric rows: scope",
    "metric rows: provenance",
    "metric rows: status",
    "metric rows: kpi_inputs",
)


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class FinancialDbAuditTest(unittest.TestCase):
    def test_operational_database_passes_hard_checks(self) -> None:
        report = run_audit()
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], 0)
        findings = {finding["check"]: finding for finding in report["findings"]}
        for name in MUST_BE_CLEAN:
            with self.subTest(check=name):
                self.assertEqual(findings[name]["issues"], 0, findings[name]["examples"])
        missing = findings["financial values and filing chunks per company-period"]["examples"]
        # HVM listed on KOSDAQ in June 2024; no regular reports exist before 2024 H1.
        self.assertTrue(all(item["company"] == "에이치브이엠" for item in missing))


if __name__ == "__main__":
    unittest.main()
