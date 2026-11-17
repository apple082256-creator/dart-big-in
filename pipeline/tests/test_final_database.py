from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, PRE_PROMOTION_FINANCIAL_DB_PATH
from src.db.final_db import build_final_database, load_processed_inputs
from src.quality.final_db_regression import REQUIRED_FINAL_VIEW_COLUMNS, run_final_regression


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class FinalDatabaseTest(unittest.TestCase):
    def test_fresh_final_database_preserves_base_facts_and_adds_capex(self) -> None:
        original_hash = _sha256(FINANCIAL_DB_PATH)
        frame, mapping = load_processed_inputs()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "financial_final.db"
            result = build_final_database(
                frame,
                mapping,
                target_path=database_path,
                capex_validation_path=root / "capex_final_validation.csv",
            )
            regression = run_final_regression(
                old_path=PRE_PROMOTION_FINANCIAL_DB_PATH,
                final_path=database_path,
                output_path=root / "financial_final_regression.csv",
            )
            with closing(sqlite3.connect(database_path)) as connection:
                view_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(financial_facts_final)")
                }

        self.assertEqual(result["integrity"], "ok")
        self.assertEqual(result["base_fact_count"], 10_849)
        self.assertEqual(result["capex_fact_count"], 120)
        self.assertTrue(regression["base_facts_identical"])
        self.assertEqual(regression["non_capex_change_count"], 0)
        self.assertEqual(regression["capex_failure_count"], 0)
        self.assertTrue(REQUIRED_FINAL_VIEW_COLUMNS <= view_columns)
        self.assertEqual(original_hash, _sha256(FINANCIAL_DB_PATH))


if __name__ == "__main__":
    unittest.main()
