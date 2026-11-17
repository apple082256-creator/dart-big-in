from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.db.financial_db import initialize_database
from src.db.krx_index_db import (
    KRX_INDEX_SEEDS,
    copy_krx_index_layer,
    get_companies_by_index,
    get_indices_by_company,
    replace_current_constituents,
    resolve_constituents_by_stock_code,
)
from src.retrieval.financial_retriever import (
    get_financial_context,
    get_financial_context_by_corp_codes,
    get_financial_context_by_index,
)


class KrxIndexIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "financial.db"
        with initialize_database(self.path) as connection, connection:
            connection.executemany(
                "INSERT INTO companies(corp_code, company_name, stock_code) VALUES (?, ?, ?)",
                (("00000001", "기업A", "000001"), ("00000002", "기업B", "000002")),
            )
            connection.execute(
                "INSERT INTO periods(year, report_type, report_code, report_name) VALUES (2025, 'FY', '11011', '사업보고서')"
            )
            period_id = connection.execute("SELECT period_id FROM periods").fetchone()[0]
            facts = []
            for number, code in enumerate(("00000001", "00000002"), start=1):
                facts.append((
                    code, period_id, "CFS", "IS", f"revenue-{number}", "ifrs_Revenue",
                    "매출액", "매출액", "flow", "annual", "2025-01-01", "2025-12-31",
                    "KRW", "thstrm_amount", str(number * 100), number * 100, None, None, None,
                    0, number, f"202603{number:02d}000001",
                ))
            connection.executemany(
                """INSERT INTO financial_facts(
                       corp_code, period_id, fs_div, sj_div, account_key, account_id,
                       original_account_nm, standard_account_nm, period_type, value_type,
                       period_start, period_end, currency, source_value_field, raw_value,
                       normalized_value, calculated_value, calculation_method, source_values,
                       is_calculated, ord, rcept_no
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                facts,
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_schema_seeds_internal_indices_including_bank(self) -> None:
        with sqlite3.connect(self.path) as connection:
            indices = connection.execute(
                "SELECT index_code, index_name, display_name FROM krx_indices ORDER BY index_code"
            ).fetchall()
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(krx_index_constituents)"
            ).fetchall()
        self.assertEqual(set(indices), set(KRX_INDEX_SEEDS))
        self.assertIn(("KRX_STEEL", "KRX 철강", "철강"), indices)
        self.assertIn(("KRX_BANK", "KRX 은행", "은행·금융지주"), indices)
        self.assertEqual({row[2] for row in foreign_keys}, {"companies", "krx_indices"})

    def test_many_to_many_current_history_and_reverse_lookup(self) -> None:
        replace_current_constituents(
            "KRX_SEMI",
            ({"corp_code": "00000001", "weight": 0.4}, {"corp_code": "00000002", "weight": 0.3}),
            source_as_of="2025-01-01",
            path=self.path,
        )
        replace_current_constituents(
            "KRX_AUTO", ({"corp_code": "00000001", "weight": 0.1},),
            source_as_of="2025-01-01", path=self.path,
        )
        replace_current_constituents(
            "KRX_SEMI", ({"corp_code": "00000002", "weight": 0.5},),
            source_as_of="2025-06-01", path=self.path,
        )

        current = get_companies_by_index("KRX_SEMI", path=self.path)
        historical = get_companies_by_index("KRX_SEMI", "2025-03-01", path=self.path)
        memberships = get_indices_by_company("00000001", "2025-03-01", path=self.path)
        self.assertEqual([row["corp_code"] for row in current], ["00000002"])
        self.assertEqual({row["corp_code"] for row in historical}, {"00000001", "00000002"})
        self.assertEqual({row["index_code"] for row in memberships}, {"KRX_SEMI", "KRX_AUTO"})
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0], 2)

    def test_stock_code_mapping_never_falls_back_to_name(self) -> None:
        matched, unresolved = resolve_constituents_by_stock_code(
            (
                {"stock_code": "1", "corp_name": "틀린 이름", "weight": 0.5},
                {"stock_code": "999999", "corp_name": "기업A", "weight": 0.4},
            ),
            path=self.path,
        )
        self.assertEqual(matched[0]["corp_code"], "00000001")
        self.assertEqual(matched[0]["corp_name"], "기업A")
        self.assertEqual(unresolved[0]["mapping_status"], "stock_code_not_found")

    def test_index_layer_survives_financial_database_rebuild_copy(self) -> None:
        replace_current_constituents(
            "KRX_SEMI", ({"corp_code": "00000001", "weight": 0.4},),
            source_as_of="2025-01-01", path=self.path,
        )
        target = Path(self.temp.name) / "rebuilt.db"
        with initialize_database(target) as connection, connection:
            connection.executemany(
                "INSERT INTO companies(corp_code, company_name, stock_code) VALUES (?, ?, ?)",
                (("00000001", "기업A", "000001"), ("00000002", "기업B", "000002")),
            )
        copy_krx_index_layer(self.path, target)
        self.assertEqual(
            [row["corp_code"] for row in get_companies_by_index("KRX_SEMI", path=target)],
            ["00000001"],
        )

    def test_financial_multi_company_and_legacy_single_company_queries(self) -> None:
        replace_current_constituents(
            "KRX_SEMI",
            ({"corp_code": "00000001"}, {"corp_code": "00000002"}),
            source_as_of="2025-01-01",
            path=self.path,
        )
        rows = get_financial_context_by_corp_codes(
            ["00000001", "00000002"], 2025, "FY", "매출액", path=self.path
        )
        index_rows = get_financial_context_by_index(
            "KRX_SEMI", 2025, "FY", "매출액", path=self.path
        )
        single = get_financial_context("기업A", 2025, "FY", "매출액", path=self.path)
        self.assertEqual({row["corp_code"] for row in rows}, {"00000001", "00000002"})
        self.assertEqual({row["corp_code"] for row in index_rows}, {"00000001", "00000002"})
        self.assertEqual([row["corp_code"] for row in single], ["00000001"])
        self.assertEqual(get_financial_context_by_corp_codes([], 2025, "FY", path=self.path), [])

    def test_mysql_schema_contains_index_layer_and_keys(self) -> None:
        schema = Path("infra/mysql/init/01_schema.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE krx_indices", schema)
        self.assertIn("CREATE TABLE krx_index_constituents", schema)
        self.assertIn("fk_krx_constituent_index", schema)
        self.assertIn("fk_krx_constituent_company", schema)

    def test_mysql_schema_contains_raw_xml_and_chunk_mapping(self) -> None:
        schema = Path("infra/mysql/init/01_schema.sql").read_text(encoding="utf-8")
        migration = Path(
            "infra/mysql/migrations/004_add_filing_source_documents.sql"
        ).read_text(encoding="utf-8")
        for sql in (schema, migration):
            self.assertIn("filing_source_documents", sql)
            self.assertIn("chunk_source_documents", sql)
            self.assertIn("raw_xml", sql)
            self.assertIn("LONGBLOB", sql)
            self.assertIn("fk_chunk_source_document", sql)


if __name__ == "__main__":
    unittest.main()
