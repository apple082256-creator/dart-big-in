import json
import unittest

from src.quality.vector_retrieval_goldset_v2_reviewed import OUTPUT_PATH, SPEC, compact


@unittest.skipUnless(OUTPUT_PATH.exists(), "검수 정답셋 미생성")
class ReviewedGoldsetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.goldset = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))

    def test_size_and_coverage(self):
        self.assertGreaterEqual(len(self.goldset), 30)
        self.assertEqual(len({row["question_type"] for row in self.goldset}), 13)
        self.assertEqual(len({row["corp_name"] for row in self.goldset}), 5)
        self.assertLessEqual({2023, 2024, 2025}, {row["year"] for row in self.goldset})
        self.assertLessEqual({"Q1", "H1", "Q3", "FY"}, {row["report_type"] for row in self.goldset})

    def test_matches_spec_and_ids_are_unique(self):
        self.assertEqual(len(self.goldset), len(SPEC))
        self.assertEqual(len({row["question_id"] for row in self.goldset}), len(self.goldset))
        for row, (topic, corp, year, report_type, evidence) in zip(self.goldset, SPEC):
            self.assertEqual((row["question_type"], row["corp_name"], row["year"], row["report_type"]),
                             (topic, corp, year, report_type))
            self.assertEqual(row["evidence_passages"], list(evidence))

    def test_expected_chunks_are_acceptable_and_reviewed(self):
        for row in self.goldset:
            self.assertEqual(row["review_status"], "reviewed")
            self.assertTrue(row["expected_chunk_ids"])
            self.assertLessEqual(set(row["expected_chunk_ids"]), set(row["acceptable_chunk_ids"]))
            self.assertTrue(all(chunk_id.startswith(row["expected_rcept_no"] + ":")
                                for chunk_id in row["acceptable_chunk_ids"]))
            self.assertEqual(row["legacy_coverage"],
                             bool(row["acceptable_by_index"]["feature_hashing"] and row["acceptable_by_index"]["e5"]))

    def test_compact_ignores_whitespace(self):
        self.assertEqual(compact("11.3조원\n의 시설투자"), compact("11.3조원의 시설투자"))


if __name__ == "__main__":
    unittest.main()
