from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import src.llm.analyzer as analyzer
import src.retrieval.financial_retriever as financial_retriever
import src.retrieval.industry_kpi as industry_kpi
from src.config import FINANCIAL_DB_PATH
from src.healthcare_rnd_pipeline import apply_healthcare_rnd, document_id, extract_candidates
from src.retrieval.financial_retriever import get_financial_metrics


def _chunk(text: str) -> sqlite3.Row:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection.execute(
        """SELECT 'chunk-1' chunk_id,'테스트' corp_name,'00000000' corp_code,2025 year,
                  'FY' report_type,'20260000000001' rcept_no,'II. 사업의 내용' section_name,
                  '6. 주요계약 및 연구개발활동' subsection_name,'report.xml' source_file,? original_text""",
        (text,),
    ).fetchone()


class HealthcareRndParserTest(unittest.TestCase):
    def test_extract_connected_rnd_table_and_unit(self) -> None:
        candidates = extract_candidates(_chunk(
            "연구개발비용 현황 (연결 재무제표 기준) (단위 : 천원) "
            "연구개발비용 합계 12,345 9,999 회계처리 판매비와 관리비 10,000 "
            "개발비(무형자산) 2,345 연구개발비 / 매출액 비율 "
            "[연구개발비용계÷당기매출액×100] 12.34% 10.00%"
        ))
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.value, 12_345_000)
        self.assertEqual((candidate.fs_div, candidate.scope), ("CFS", "consolidated"))
        self.assertEqual(candidate.ratio_percent, "12.34")
        self.assertEqual(candidate.components["판매비와 관리비"], "10,000")


@unittest.skipUnless(FINANCIAL_DB_PATH.is_file(), "운영 Financial DB 없음")
class HealthcareRndIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.target = Path(cls.directory.name) / "financial.db"
        with closing(sqlite3.connect(f"file:{FINANCIAL_DB_PATH.resolve()}?mode=ro", uri=True)) as source, \
                closing(sqlite3.connect(cls.target)) as destination:
            source.backup(destination)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.directory.cleanup()

    def test_coverage_query_and_idempotency(self) -> None:
        with closing(sqlite3.connect(self.target)) as connection:
            first = apply_healthcare_rnd(connection)
            count_after_first = connection.execute(
                "SELECT COUNT(*) FROM financial_metric_values WHERE metric_code LIKE 'RND_%'"
            ).fetchone()[0]
            second = apply_healthcare_rnd(connection)
            count_after_second = connection.execute(
                "SELECT COUNT(*) FROM financial_metric_values WHERE metric_code LIKE 'RND_%'"
            ).fetchone()[0]
            periods = connection.execute("SELECT COUNT(*) FROM periods").fetchone()[0]
        # 5 healthcare companies x every loaded period (12 for 2023-2025, 14 with 2026 Q1/H1)
        self.assertEqual(first["validation"]["coverage"], {"RND_EXPENSE": 5 * periods, "RND_TO_REVENUE": 5 * periods})
        self.assertEqual(first["validation"]["duplicate_natural_keys"], 0)
        self.assertEqual(first["validation"]["missing_slots"], 0)
        self.assertEqual(second["document_rows"], first["document_rows"])
        self.assertEqual(count_after_second, count_after_first)

        metrics = get_financial_metrics("셀트리온", 2025, "FY", "RND_EXPENSE", path=self.target)
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["source_kind"], "DOCUMENT_EXTRACTED")
        self.assertTrue(metrics[0]["source_rcept_no"])
        sources = json.loads(metrics[0]["source_values"])
        self.assertEqual(
            metrics[0]["source_document_id"],
            document_id(metrics[0]["source_rcept_no"], sources["locator"]["source_path"]),
        )
        self.assertEqual(len(metrics[0]["source_document_id"]), 64)

        with mock.patch.object(financial_retriever, "FINANCIAL_DB_PATH", self.target), \
                mock.patch.object(industry_kpi, "FINANCIAL_DB_PATH", self.target):
            result = analyzer.analyze_company(
                "유한양행 외 헬스케어 4사의 연구개발비와 R&D/매출액 비율을 비교해줘",
                "유한양행", 2025, "FY",
            )
        self.assertEqual(len(result["financial_evidence"]), 10)
        self.assertEqual(
            {row["corp_name"] for row in result["financial_evidence"]},
            {"셀트리온", "삼성바이오로직스", "알테오젠", "HLB", "유한양행"},
        )


if __name__ == "__main__":
    unittest.main()
