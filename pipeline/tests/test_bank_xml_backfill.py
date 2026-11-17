from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.bank_xml_backfill_pipeline import apply_backfill, document_id
from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT
from src.expense_sign_pipeline import apply_expense_signs
from src.db.financial_metrics import materialize_financial_metric_values
from src.extract.bank_statement_xml import extract_statement_lines, line_name, parse_amount

SHINHAN_Q1 = PROJECT_ROOT / "data/raw/filings/xml/00382199/2023/Q1/20230515002373/20230515002373.xml"
COLUMNS = ("corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind,value,"
           "source_document_id,source_chunk_id,calculation_method,source_values,review_status")


def _content_hash(connection: sqlite3.Connection) -> str:
    rows = sorted((tuple(row) for row in connection.execute(
        f"SELECT {COLUMNS} FROM financial_metric_values")), key=repr)
    return hashlib.sha256(repr(rows).encode()).hexdigest()


class StatementParsingTest(unittest.TestCase):
    def test_amounts_keep_sign_and_never_invent_zero(self) -> None:
        self.assertEqual(parse_amount("(6,820,627)"), -6_820_627)
        self.assertEqual(parse_amount("-3,919,097"), -3_919_097)
        self.assertEqual(parse_amount("1,234"), 1_234)
        self.assertIsNone(parse_amount("-"))
        self.assertIsNone(parse_amount(""))
        self.assertIsNone(parse_amount("2,552 원"))

    def test_line_numbering_is_removed_without_eating_names(self) -> None:
        self.assertEqual(line_name("Ⅵ. 상각후원가측정대출채권"), "상각후원가측정대출채권")
        self.assertEqual(line_name("(1) 순이자손익"), "순이자손익")
        self.assertEqual(line_name("자 산 총 계"), "자산총계")

    @unittest.skipUnless(SHINHAN_Q1.is_file(), "원본 XML 없음")
    def test_extracts_current_column_with_unit_and_period(self) -> None:
        result = extract_statement_lines(SHINHAN_Q1, "CFS", 2023, "Q1")
        self.assertEqual(result.issues, [])
        self.assertEqual(result.statements["BS"]["unit"], "백만원")
        self.assertEqual(result.statements["CIS"]["layouts"], ["paired"])
        lines = result.lines
        self.assertEqual(lines["TOTAL_ASSETS"].krw, 676_175_588_000_000)
        self.assertEqual(lines["INTEREST_EXPENSE"].krw, -3_919_097_000_000)
        self.assertEqual(lines["TOTAL_ASSETS"].krw,
                         lines["TOTAL_LIABILITIES"].krw + lines["TOTAL_EQUITY"].krw)

    @unittest.skipUnless(SHINHAN_Q1.is_file(), "원본 XML 없음")
    def test_wrong_period_is_reported_not_guessed(self) -> None:
        result = extract_statement_lines(SHINHAN_Q1, "CFS", 2023, "H1")
        self.assertEqual(result.lines, {})
        self.assertTrue(any("period end" in issue for issue in result.issues))


class BackfillPipelineTest(unittest.TestCase):
    def test_backfill_is_additive_idempotent_and_survives_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "financial.db"
            with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as src, \
                    closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            with closing(sqlite3.connect(target)) as v9:
                apply_expense_signs(v9)  # v9 expense signs (no-op once rolled out)
            with closing(sqlite3.connect(target)) as connection:
                connection.execute("PRAGMA foreign_keys=ON")
                untouched = sorted(tuple(row) for row in connection.execute(
                    f"SELECT {COLUMNS} FROM financial_metric_values "
                    "WHERE source_kind='DIRECT' OR source_kind='DERIVED' AND source_values NOT LIKE "
                    "'%component_source_kind%' AND calculation_method NOT LIKE 'FY annual - Q3 cumulative%'"))
                apply_backfill(connection)
                first = _content_hash(connection)
                result = apply_backfill(connection)
                self.assertEqual(_content_hash(connection), first)
                self.assertEqual(result["slots"], 10)
                self.assertEqual(result["document_rows"], 164)

                # Statement values of the ten API-missing slots and what derives from them
                # (note-based loans and other document rows are counted elsewhere).
                document = connection.execute(
                    "SELECT COUNT(*) FROM financial_metric_values WHERE source_kind='DOCUMENT_EXTRACTED' "
                    "AND source_type='dart_document_xml'"
                ).fetchone()[0]
                derived = connection.execute(
                    "SELECT COUNT(*) FROM financial_metric_values WHERE source_kind='DERIVED' "
                    "AND source_values LIKE '%\"component_source_kind\": \"DOCUMENT_EXTRACTED\"%'"
                ).fetchone()[0]
                self.assertEqual((document, derived), (164, 44))  # 38 Q2 quarters + 6 KakaoBank nets
                self.assertEqual(untouched, sorted(tuple(row) for row in connection.execute(
                    f"SELECT {COLUMNS} FROM financial_metric_values "
                    "WHERE source_kind='DIRECT' OR source_kind='DERIVED' AND source_values NOT LIKE "
                    "'%component_source_kind%' AND calculation_method NOT LIKE 'FY annual - Q3 cumulative%'")))

                # Statement extraction only fills slots the API left empty (note-based
                # loans are a separate metric and may sit next to API values).
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM financial_metric_values v WHERE source_kind='DOCUMENT_EXTRACTED' "
                    "AND source_type='dart_document_xml' AND EXISTS (SELECT 1 FROM financial_metric_values d WHERE d.source_kind='DIRECT' "
                    "AND d.corp_code=v.corp_code AND d.year=v.year AND d.report_type=v.report_type)"
                ).fetchone()[0], 0)

                row = connection.execute(
                    """SELECT value, source_rcept_no, source_document_id, source_values, review_status
                       FROM financial_metric_values
                       WHERE corp_code='00688996' AND year=2023 AND report_type='Q1'
                         AND metric_code='INTEREST_EXPENSE' AND value_type='cumulative'"""
                ).fetchone()
                sources = json.loads(row[3])
                self.assertEqual(row[0], 4_080_990_000_000)  # aligned to KB's positive API series
                self.assertTrue(sources["sign"]["aligned"])
                self.assertEqual(sources["presented"]["text"], "(4,080,990)")
                self.assertEqual(row[2], document_id(
                    row[1], "data/raw/filings/xml/00688996/2023/Q1/20231005000478/20231005000478.xml"))

                kakao_q4 = dict(connection.execute(
                    """SELECT year||' '||metric_code, value FROM financial_metric_values
                       WHERE corp_code='01133217' AND report_type='FY' AND value_type='quarterly'
                         AND year IN (2023, 2024) AND source_kind='DERIVED'
                         AND metric_code IN ('NET_INTEREST_INCOME','NET_FEE_COMMISSION_INCOME')"""))
                self.assertEqual(kakao_q4, {
                    "2023 NET_INTEREST_INCOME": 311_284_000_000,
                    "2023 NET_FEE_COMMISSION_INCOME": -3_893_000_000,
                    "2024 NET_INTEREST_INCOME": 326_620_000_000,
                    "2024 NET_FEE_COMMISSION_INCOME": 9_906_000_000,
                })
                kb_income = connection.execute(
                    """SELECT value, review_status, source_values FROM financial_metric_values
                       WHERE corp_code='00688996' AND year=2023 AND report_type='Q1'
                         AND metric_code='NET_INCOME' AND value_type='cumulative'"""
                ).fetchone()
                kb_sources = json.loads(kb_income[2])
                self.assertEqual(kb_income[:2], (1_499_214_000_000, "approved"))  # as first reported
                self.assertEqual(kb_sources["restatement"]["restated_value"], 1_510_291_000_000)
                woori_loans = connection.execute(
                    """SELECT review_status, source_values FROM financial_metric_values
                       WHERE corp_code='01350869' AND year=2023 AND report_type='Q1'
                         AND metric_code='LOANS_AT_AMORTISED_COST'"""
                ).fetchone()
                self.assertEqual(woori_loans[0], "review_required")
                self.assertEqual(json.loads(woori_loans[1])["verification"], "REVIEW_REQUIRED")

                applied = {r[:8]: (r[8], r[-1]) for r in connection.execute(
                    f"SELECT {COLUMNS} FROM financial_metric_values")}
                with connection:
                    materialize_financial_metric_values(connection)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM financial_metric_values WHERE source_kind='DOCUMENT_EXTRACTED' "
                    "AND source_type='dart_document_xml'"
                ).fetchone()[0], 164)
                rebuilt = {r[:8]: (r[8], r[-1]) for r in connection.execute(
                    f"SELECT {COLUMNS} FROM financial_metric_values")}
                # The rebuild keeps every applied row. It may add fact KPIs the operational
                # DB has not loaded yet (non-bank profiles' 2023-2025 rows), never bank rows.
                self.assertEqual({key: rebuilt.get(key) for key in applied}, applied)
                banks = {row[0] for row in connection.execute(
                    "SELECT corp_code FROM company_financial_profiles "
                    "WHERE industry_profile IN ('BANK_HOLDING', 'INTERNET_BANK')")}
                self.assertEqual({key for key in rebuilt.keys() - applied.keys() if key[0] in banks}, set())


if __name__ == "__main__":
    unittest.main()
