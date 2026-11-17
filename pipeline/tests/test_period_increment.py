from __future__ import annotations

import copy
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

from src.collect.financial_collector import DartApiError
from src.config import FINANCIAL_DB_PATH, RAW_DIR
from src.period_increment_pipeline import download_document, filing_deadline, replace_slot, status_rows

KAKAO = ("01133217", "카카오뱅크", "323410")


def _frame(items: list[dict], year: int, report: str, code: str, name: str) -> pd.DataFrame:
    return pd.DataFrame([{**a, "company_name": name, "stock_code": KAKAO[2], "year": year, "quarter": report,
                          "report_code": code, "report_name": name, "fs_div_requested": "OFS"} for a in items])


class StatusTest(unittest.TestCase):
    def test_deadlines_and_status_rows(self) -> None:
        self.assertEqual(filing_deadline(2026, "Q3"), date(2026, 11, 14))
        self.assertEqual(filing_deadline(2026, "FY"), date(2027, 3, 31))
        rows = status_rows([
            {"corp_code": "1", "year": 2026, "report_type": "Q1", "fs_div": "CFS", "api_status": "000",
             "state": "COLLECTED", "api_rcept_no": "20260515000001"},
            {"corp_code": "1", "year": 2026, "report_type": "Q3", "fs_div": "OFS", "api_status": "013",
             "state": "NOT_DUE", "api_rcept_no": None},
        ])
        self.assertEqual((rows[0]["selected_fs_div"], rows[0]["missing_reason"], rows[0]["ofs_status"]),
                         ("CFS", None, "NOT_TRIED"))
        self.assertEqual((rows[1]["selected_fs_div"], rows[1]["missing_reason"], rows[1]["ofs_status"]),
                         (None, "NOT_DUE", "013"))

    def test_error_payload_is_reported_not_saved(self) -> None:
        class Client:
            def _get(self, endpoint, params):
                return type("R", (), {"content": "<result><status>800</status><message>시스템 점검</message></result>"
                                      .encode("utf-8")})()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DartApiError) as raised:
                download_document(Client(), "1", 2026, "Q1", "20260515000001", Path(directory))
            self.assertIn("시스템 점검", str(raised.exception))
            self.assertEqual(list(Path(directory).rglob("*.zip")), [])

    def test_existing_documents_are_not_overwritten(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("20260515000001.xml", "<new/>")

        class Client:
            def _get(self, endpoint, params):
                return type("R", (), {"content": buffer.getvalue()})()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "filings" / "xml" / "1" / "2026" / "Q1" / "20260515000001" / "20260515000001.xml"
            target.parent.mkdir(parents=True)
            target.write_text("<kept/>")
            documents = download_document(Client(), "1", 2026, "Q1", "20260515000001", Path(directory))
            self.assertEqual(target.read_text(), "<kept/>")
            self.assertEqual([d["main"] for d in documents], [True])


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file() and (RAW_DIR / "financial" / KAKAO[0] / "2025" / "Q3_OFS.json").is_file(),
                     "운영 Financial DB 또는 원본 없음")
class ReplaceSlotTest(unittest.TestCase):
    def test_corrected_slot_replaces_only_that_company_year(self) -> None:
        payload = json.loads((RAW_DIR / "financial" / KAKAO[0] / "2025" / "Q3_OFS.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as db:
                outside_facts = ("SELECT * FROM financial_facts WHERE NOT (corp_code=? AND period_id IN "
                                 "(SELECT period_id FROM periods WHERE year=2025))")
                outside_metrics = "SELECT * FROM financial_metric_values WHERE NOT (corp_code=? AND year=2025)"
                facts_before = set(db.execute(outside_facts, (KAKAO[0],)))
                metrics_before = set(db.execute(outside_metrics, (KAKAO[0],)))
                all_before = set(db.execute("SELECT * FROM financial_facts"))
                frame = _frame(payload["list"], 2025, "Q3", "11014", "3분기보고서")
                self.assertEqual(replace_slot(db, KAKAO[0], 2025, "Q3", frame, "identical"), {"changed": False})
                self.assertEqual(set(db.execute("SELECT * FROM financial_facts")), all_before)

                items = copy.deepcopy(payload["list"])
                for item in items:
                    item["rcept_no"] = "20991231000001"
                result = replace_slot(db, KAKAO[0], 2025, "Q3", _frame(items, 2025, "Q3", "11014", "3분기보고서"),
                                      "re-filed")
                self.assertTrue(result["changed"])
                self.assertEqual(set(db.execute(outside_facts, (KAKAO[0],))), facts_before)
                self.assertEqual(set(db.execute(outside_metrics, (KAKAO[0],))), metrics_before)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM financial_facts_history").fetchone()[0],
                                 result["archived_facts"])
                self.assertEqual({r[0] for r in db.execute(
                    """SELECT DISTINCT f.rcept_no FROM financial_facts f JOIN periods p USING(period_id)
                       WHERE f.corp_code=? AND p.year=2025 AND p.report_type='Q3' AND f.is_calculated=0""",
                    (KAKAO[0],))}, {"20991231000001"})
                self.assertEqual(replace_slot(db, KAKAO[0], 2025, "Q3",
                                              _frame(items, 2025, "Q3", "11014", "3분기보고서"), "rerun"),
                                 {"changed": False})
                self.assertEqual(db.execute(
                    """SELECT COUNT(*) FROM (SELECT 1 FROM financial_facts GROUP BY corp_code,period_id,fs_div,
                       sj_div,account_key,ord,value_type,is_calculated HAVING COUNT(*)>1)""").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
