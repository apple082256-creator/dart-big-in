from __future__ import annotations

import inspect
import sqlite3
import unittest

import numpy as np

from src.retrieval.bge_m3_indexer import (
    BGE_DB_PATH,
    BGE_FAISS_PATH,
    DIMENSIONS,
    bge_retrieval_method_name,
    combine_bge_scores,
    search_bge_m3,
)


class BgeM3ScoringModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dense = np.asarray([0.2, 0.8], dtype=np.float32)
        self.lexical = np.asarray([0.9, 0.1], dtype=np.float32)
        self.priorities = np.asarray([0.0, 1.0], dtype=np.float32)

    def test_four_scoring_modes_are_independent(self) -> None:
        np.testing.assert_allclose(
            combine_bge_scores(
                self.dense, self.lexical, self.priorities,
                hybrid=False, apply_priority=False,
            ),
            [0.2, 0.8],
        )
        np.testing.assert_allclose(
            combine_bge_scores(
                self.dense, self.lexical, self.priorities,
                hybrid=False, apply_priority=True,
            ),
            [0.18, 0.8],
        )
        np.testing.assert_allclose(
            combine_bge_scores(
                self.dense, self.lexical, self.priorities,
                hybrid=True, apply_priority=False,
            ),
            [0.48, 0.52],
        )
        np.testing.assert_allclose(
            combine_bge_scores(
                self.dense, self.lexical, self.priorities,
                hybrid=True, apply_priority=True,
            ),
            [0.432, 0.52],
        )

    def test_scoring_mode_names_are_explicit(self) -> None:
        self.assertEqual(
            bge_retrieval_method_name(hybrid=False, apply_priority=False),
            "bge_m3_dense",
        )
        self.assertEqual(
            bge_retrieval_method_name(hybrid=False, apply_priority=True),
            "bge_m3_priority_dense",
        )
        self.assertEqual(
            bge_retrieval_method_name(hybrid=True, apply_priority=False),
            "bge_m3_hybrid",
        )
        self.assertEqual(
            bge_retrieval_method_name(hybrid=True, apply_priority=True),
            "bge_m3_priority_hybrid",
        )

    def test_invalid_scoring_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            combine_bge_scores(
                self.dense, self.lexical[:1], self.priorities,
                hybrid=False, apply_priority=False,
            )
        with self.assertRaises(ValueError):
            combine_bge_scores(
                self.dense, self.lexical, self.priorities,
                hybrid=True, apply_priority=False, dense_weight=1.1,
            )

    def test_search_defaults_to_pure_dense(self) -> None:
        parameters = inspect.signature(search_bge_m3).parameters
        self.assertFalse(parameters["hybrid"].default)
        self.assertFalse(parameters["apply_priority"].default)


@unittest.skipUnless(BGE_DB_PATH.exists() and BGE_FAISS_PATH.exists(), "BGE-M3 v2 산출물이 아직 없습니다.")
class BgeM3FaissArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import faiss
        cls.index = faiss.read_index(str(BGE_FAISS_PATH))
        cls.connection = sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True)
        cls.connection.row_factory = sqlite3.Row
        cls.rows = cls.connection.execute(
            "SELECT * FROM filing_chunks WHERE is_searchable=1 ORDER BY vector_id"
        ).fetchall()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_dimension_and_counts(self) -> None:
        self.assertEqual(self.index.d, DIMENSIONS)
        self.assertEqual(self.index.ntotal, len(self.rows))

    def test_vectors_are_normalized_and_finite(self) -> None:
        vectors = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in self.rows])
        self.assertTrue(np.isfinite(vectors).all())
        self.assertTrue(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=2e-3))

    def test_ids_are_unique_and_reconstructable(self) -> None:
        vector_ids = [row["vector_id"] for row in self.rows]
        chunk_ids = [row["chunk_id"] for row in self.rows]
        self.assertEqual(len(vector_ids), len(set(vector_ids)))
        self.assertEqual(len(chunk_ids), len(set(chunk_ids)))
        for vector_id in vector_ids[:25]:
            self.assertEqual(len(self.index.reconstruct(int(vector_id))), DIMENSIONS)

    def test_source_traceability(self) -> None:
        self.assertTrue(all(row["rcept_no"] and row["source_file"] for row in self.rows))


if __name__ == "__main__":
    unittest.main()
