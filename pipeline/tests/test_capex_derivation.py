from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, MAPPING_DIR
from src.db.capex_db import EXCLUDED_TERMS, STANDARD_CAPEX_NAME, build_capex_database
from src.retrieval.financial_retriever import get_financial_context


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CapexDerivationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        root = Path(cls.temp_dir.name)
        cls.database_path = root / "financial_capex_test.db"
        source_hash = _sha256(FINANCIAL_DB_PATH)
        mapping_path = MAPPING_DIR / "account_mapping_core.csv"
        mapping_hash = _sha256(mapping_path)
        cls.result = build_capex_database(
            source_path=FINANCIAL_DB_PATH,
            target_path=cls.database_path,
            validation_path=root / "capex_validation.csv",
        )
        cls.source_hash_unchanged = source_hash == _sha256(FINANCIAL_DB_PATH)
        cls.mapping_hash_unchanged = mapping_hash == _sha256(mapping_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _rows(self, company: str, year: int, report_type: str) -> list[dict]:
        return get_financial_context(
            company,
            year,
            report_type,
            account_name=STANDARD_CAPEX_NAME,
            path=self.database_path,
        )

    def test_original_database_is_unchanged(self) -> None:
        self.assertTrue(self.result["source_unchanged"])
        self.assertTrue(self.source_hash_unchanged)
        self.assertTrue(self.mapping_hash_unchanged)

    def test_jusung_2024_h1_component_fallback_and_q2(self) -> None:
        rows = self._rows("주성엔지니어링", 2024, "HY")
        cumulative = next(row for row in rows if row["value_type"] == "cumulative")
        quarterly = next(row for row in rows if row["value_type"] == "quarterly")

        self.assertEqual(cumulative["cash_outflow_amount"], 11_066_442_727)
        self.assertEqual(cumulative["value_source"], "derived")
        self.assertEqual(cumulative["source_account_count"], 6)
        self.assertEqual(quarterly["calculated_value"], 5_023_179_778)
        self.assertEqual(quarterly["cash_outflow_amount"], 5_023_179_778)

        source_names = json.loads(cumulative["source_account_names"])
        self.assertFalse(any(term in name for name in source_names for term in EXCLUDED_TERMS))

        q1_cumulative = next(
            row for row in self._rows("주성엔지니어링", 2024, "Q1")
            if row["value_type"] == "cumulative"
        )
        h1_values = {
            item["account_id"]: abs(item["normalized_value"])
            for item in json.loads(cumulative["source_values"])
        }
        q1_values = {
            item["account_id"]: abs(item["normalized_value"])
            for item in json.loads(q1_cumulative["source_values"])
        }
        component_deltas = sum(
            h1_values.get(account_id, 0) - q1_values.get(account_id, 0)
            for account_id in h1_values.keys() | q1_values.keys()
        )
        self.assertEqual(component_deltas, quarterly["cash_outflow_amount"])

    def test_wonik_uses_direct_account_without_double_counting(self) -> None:
        rows = self._rows("원익IPS", 2024, "H1")
        cumulative = next(row for row in rows if row["value_type"] == "cumulative")
        self.assertEqual(cumulative["cash_outflow_amount"], 13_089_034_300)
        self.assertEqual(cumulative["value_source"], "direct")
        self.assertEqual(cumulative["source_account_count"], 1)
        self.assertEqual(cumulative["direct_account_found"], 1)

    def test_negative_cumulative_difference_is_not_absolutized(self) -> None:
        cases = (("HLB", 2023, "CFS"), ("에이치브이엠", 2025, "OFS"))
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            for company, year, fs_div in cases:
                row = connection.execute(
                    """SELECT cf.* FROM capex_facts cf
                       JOIN companies c USING(corp_code) JOIN periods p USING(period_id)
                       WHERE c.company_name=? AND p.year=? AND p.report_type='H1'
                         AND cf.fs_div=? AND cf.value_type='quarterly'""",
                    (company, year, fs_div),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertLess(row["calculated_value"], 0)
                self.assertIsNone(row["cash_outflow_amount"])
                self.assertEqual(row["review_required"], 1)
                self.assertIn("standalone CAPEX unavailable", row["note"])

    def test_five_company_classification(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT c.company_name, GROUP_CONCAT(DISTINCT cf.value_source)
                FROM capex_facts cf JOIN companies c USING(corp_code)
                GROUP BY c.company_name ORDER BY c.company_name
                """
            ).fetchall()
        modes = dict(rows)
        original_companies = {
            "삼성전자", "SK하이닉스", "한미반도체", "주성엔지니어링", "원익IPS",
        }
        self.assertTrue(original_companies <= set(modes))
        self.assertEqual(modes["주성엔지니어링"], "derived")
        for company in {"삼성전자", "SK하이닉스", "한미반도체", "원익IPS"}:
            self.assertEqual(modes[company], "direct")

        with closing(sqlite3.connect(self.database_path)) as connection:
            duplicates = connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT corp_code, period_id, fs_div, value_type, COUNT(*) AS count_rows
                    FROM capex_facts
                    GROUP BY corp_code, period_id, fs_div, value_type
                    HAVING count_rows > 1
                )
                """
            ).fetchone()[0]
        self.assertEqual(duplicates, 0)


if __name__ == "__main__":
    unittest.main()
