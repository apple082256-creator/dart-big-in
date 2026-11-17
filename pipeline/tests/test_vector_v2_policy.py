import sqlite3
import unittest

from src.quality.vector_v2_policy import is_relevant, promotion_decision
from src.retrieval.lexical_scoring import lexical_scores


class VectorV2PolicyTests(unittest.TestCase):
    def setUp(self):
        self.gold = dict(corp_name="삼성전자", year=2025, report_type="FY",
                         expected_rcept_no="receipt", expected_chunk_ids=["v2:1"],
                         acceptable_chunk_ids=["v1:1"], expected_sections=["사업"],
                         evidence_terms=["매출"])
        self.result = dict(corp_name="삼성전자", year=2025, report_type="FY",
                           rcept_no="receipt", chunk_id="v2:1", section_name="사업",
                           chunk_text="매출 증가")

    def test_explicit_ids_and_receipt(self):
        self.assertTrue(is_relevant(self.result, self.gold))
        self.assertTrue(is_relevant({**self.result, "chunk_id": "v1:1"}, self.gold))
        self.assertFalse(is_relevant({**self.result, "chunk_id": "unreviewed"}, self.gold))
        for key, value in (("corp_name", "다른기업"), ("year", 2024),
                           ("report_type", "Q1"), ("rcept_no", "other")):
            self.assertFalse(is_relevant({**self.result, key: value}, self.gold))

    def test_unreviewed_or_unvalidated_cannot_promote(self):
        current = dict(hit_at_1=.4, hit_at_5=.8, mrr=.5)
        candidate = dict(hit_at_1=.6, hit_at_5=.9, mrr=.7, other_corp_count=0,
                         other_year_count=0, other_report_type_count=0,
                         rcept_no_match_rate=1., p95_latency_ms=50)
        self.assertEqual(promotion_decision(current, candidate, validation={}, goldset=[self.gold]), "NEEDS_TUNING")
        self.assertEqual(promotion_decision(current, {**candidate, "other_year_count": 1},
                                           validation={"status": "PASS"}, goldset=[self.gold]), "KEEP_CURRENT_DEFAULT")

    def test_sqlite_rows_in_keyword_scoring(self):
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT '사업' AS section_name, NULL AS subsection_name, '매출 증가' AS chunk_text").fetchall()
            self.assertGreater(float(lexical_scores("매출", rows)[0]), 0)


if __name__ == "__main__":
    unittest.main()
