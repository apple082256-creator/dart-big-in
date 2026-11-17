from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from src.quality.bge_m3_four_mode_e2e import (
    ApiError,
    BASELINE_OUTPUTS,
    BGE_DB_PATH,
    ExactGeminiClient,
    METHODS,
    NEW_METHODS,
    TEST_IDS,
    _compact_chunk,
    _assert_frozen_retrievals_complete,
    _generate_new_candidates,
    _label_map,
    _load_baseline,
    _record_key,
    replace_prompt_chunks,
)


@unittest.skipUnless(BASELINE_OUTPUTS.exists(), "기존 E2E outputs가 없습니다.")
class FourModeBaselineReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = _load_baseline()
        cls.records = {
            (record["test_id"], record["retrieval_method"]): record
            for record in cls.baseline["outputs"]
        }

    def test_existing_prompt_is_reconstructed_without_changing_conditions(self) -> None:
        for test_id in ("I01", "K01"):
            record = self.records[(test_id, "bge_m3_dense")]
            chunks = [
                _compact_chunk(chunk, method="bge_m3_dense", priority=0.0)
                for chunk in record["retrieved_chunks"]
            ]
            rebuilt = replace_prompt_chunks(record["user_prompt"], chunks, record["feature_type"])
            self.assertEqual(rebuilt, record["user_prompt"])

    def test_quota_error_blocks_repeated_calls_to_same_model_in_one_run(self) -> None:
        class QuotaClient:
            def __init__(self) -> None:
                self.models: list[str] = []

            def generate(self, **kwargs):
                self.models.append(kwargs["model"])
                raise ApiError("quota", status_code=429, quota_error=True)

        checkpoint = {
            "retrievals": {},
            "candidates": {},
            "failures": [],
            "call_stats": {
                "candidate_successes": 0, "judge_successes": 0, "http_attempts": 0,
                "retries": 0, "quota_errors": 0, "errors": 0,
            },
        }
        for test_id in TEST_IDS:
            reference = self.records[(test_id, "bge_m3_dense")]
            for method in NEW_METHODS:
                checkpoint["retrievals"][_record_key(test_id, method)] = {
                    "chunks": [
                        _compact_chunk(chunk, method=method, priority=0.0)
                        for chunk in reference["retrieved_chunks"]
                    ],
                    "latency_ms": 1.0,
                }
        client = QuotaClient()
        baseline_records = {
            _record_key(test_id, method): record
            for (test_id, method), record in self.records.items()
        }
        with patch("src.quality.bge_m3_four_mode_e2e._atomic_json"):
            blocked = _generate_new_candidates(checkpoint, baseline_records, client)
        expected_models = set(self.baseline["experiment"]["candidate_models"].values())
        self.assertEqual(set(client.models), expected_models)
        self.assertEqual(len(client.models), len(expected_models))
        self.assertEqual(blocked, expected_models)

    @unittest.skipUnless(BGE_DB_PATH.exists(), "BGE SQLite가 없습니다.")
    def test_legacy_hybrid_scores_are_priority_hybrid(self) -> None:
        with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
            priorities = dict(connection.execute("SELECT chunk_id, search_priority FROM filing_chunks"))
        checked = 0
        for record in self.baseline["outputs"]:
            if record["retrieval_method"] != "bge_m3_hybrid":
                continue
            for chunk in record["retrieved_chunks"]:
                base = 0.60 * float(chunk["dense_score"]) + 0.40 * float(chunk["lexical_score"])
                expected = base * (0.90 + 0.10 * float(priorities[chunk["chunk_id"]]))
                self.assertAlmostEqual(float(chunk["final_score"]), expected, places=6)
                checked += 1
        self.assertEqual(checked, 50)


class FourModeBlindAndClientTests(unittest.TestCase):
    def test_frozen_resume_requires_all_40_original_asset_retrievals(self) -> None:
        checkpoint = {
            "retrievals": {
                _record_key(test_id, method): {}
                for test_id in TEST_IDS
                for method in METHODS
            },
            "asset_hashes": {
                "db/vector/filings_bge_m3.db":
                    "8b528ebf2c98bf9456325c90a9e6bf8366c3f6f0d8c6208ae9df7288df239dbb",
                "db/vector/filings_bge_m3.faiss":
                    "17ddc13efd6488138c8b63f65698fc6b7ad37c39a7224e83e3266fd70e64a6c7",
            },
        }
        _assert_frozen_retrievals_complete(checkpoint)

    def test_blind_label_map_is_stable_and_complete(self) -> None:
        first = _label_map("I01")
        self.assertEqual(first, _label_map("I01"))
        self.assertEqual(set(first), {"Method A", "Method B", "Method C", "Method D"})
        self.assertEqual(set(first.values()), set(METHODS))

    def test_exact_client_does_not_fallback_to_another_model(self) -> None:
        class Response:
            ok = True
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {
                    "candidates": [{
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": '{"answer":"ok"}'}]},
                    }],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                }

        with patch("src.quality.bge_m3_four_mode_e2e.requests.post", return_value=Response()) as post:
            raw, metadata = ExactGeminiClient("secret", max_attempts=1).generate(
                model="fixed-model",
                system_prompt="system",
                user_prompt="user",
                schema={"type": "OBJECT"},
                max_tokens=10,
                temperature=0.1,
            )
        self.assertEqual(json.loads(raw), {"answer": "ok"})
        self.assertEqual(metadata["model"], "fixed-model")
        self.assertIn("/fixed-model:generateContent", post.call_args.args[0])
        self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
