"""Exercise the real SQLite/FAISS pipeline with deterministic test vectors."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from src.retrieval import bge_m3_indexer as bge


class FakeModel:
    def __init__(self):
        self.encoded = 0

    def encode(self, texts, **kwargs):
        self.encoded += len(texts)
        vectors = np.zeros((len(texts), 1024), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors


class BgePipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.model = FakeModel()
        self.chunks = []
        for number, (corp, year, report, receipt) in enumerate((
            ("삼성전자", 2025, "FY", "r1"), ("삼성전자", 2024, "FY", "r2"),
            ("SK하이닉스", 2025, "FY", "r3"), ("삼성전자", 2025, "Q1", "r4"),
        )):
            text = f"반도체 매출과 투자 {number}"
            self.chunks.append(dict(chunk_id=f"{receipt}:v2:0:0", corp_name=corp,
                corp_code=corp, year=year, report_type=report, rcept_no=receipt,
                section_name="사업", subsection_name=None, source_file="source.xml",
                original_text=text, chunk_text=text, content_hash=hashlib.sha256(text.encode()).hexdigest(),
                chunk_version=bge.CHUNK_VERSION, token_count=10, content_type="short_statement",
                search_priority=.35, selection_reason="보존", is_searchable=True))
        self.patches = [
            patch.object(bge, "BGE_DB_PATH", self.root / "bge.db"),
            patch.object(bge, "BGE_FAISS_PATH", self.root / "bge.faiss"),
            patch.object(bge, "BUILD_REPORT_PATH", self.root / "build.json"),
            patch.object(bge, "PROJECT_ROOT", self.root),
            patch.object(bge, "load_model", return_value=self.model),
            patch.object(bge, "model_revision", return_value="test-revision"),
            patch.object(bge, "_iter_chunks", return_value=self.chunks),
        ]
        for item in self.patches:
            item.start()
        # Pass the patched path explicitly: Python default arguments are bound at definition time.
        original_writer = bge._write_faiss_index
        item = patch.object(bge, "_write_faiss_index", side_effect=lambda conn: original_writer(conn, bge.BGE_FAISS_PATH))
        item.start()
        self.patches.append(item)
        bge._load_faiss_index.cache_clear()
        bge.build_bge_m3_index(batch_size=2)

    def tearDown(self):
        bge._load_faiss_index.cache_clear()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_roundtrip_and_exact_id_mapping(self):
        import faiss
        index = faiss.read_index(str(bge.BGE_FAISS_PATH))
        with sqlite3.connect(bge.BGE_DB_PATH) as connection:
            rows = connection.execute("SELECT vector_id,chunk_id,embedding FROM filing_chunks").fetchall()
        self.assertEqual(index.d, 1024)
        self.assertEqual(index.ntotal, len(rows))
        self.assertEqual(set(faiss.vector_to_array(index.id_map)), {row[0] for row in rows})
        self.assertEqual(len({row[1] for row in rows}), len(rows))
        for vector_id, _, blob in rows:
            stored = np.frombuffer(blob, dtype=np.float32)
            np.testing.assert_array_equal(stored, index.reconstruct(vector_id))
            self.assertAlmostEqual(float(np.linalg.norm(stored)), 1.)

    def test_incremental_rerun_does_not_encode_or_duplicate(self):
        previous = self.model.encoded
        report = bge.build_bge_m3_index(batch_size=2)
        self.assertEqual(self.model.encoded, previous)
        self.assertEqual(report["skipped_existing"], 4)
        self.assertEqual(report["inserted"], 0)
        self.assertEqual(report["faiss_vectors"], 4)

    def test_all_filters_and_original_link(self):
        results = bge.search_bge_m3("매출", corp_name="삼성전자", corp_code="삼성전자",
                                  year=2025, report_type="FY", rcept_no="r1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], self.chunks[0]["chunk_id"])
        self.assertEqual(results[0]["chunk_text"], self.chunks[0]["original_text"])
        self.assertEqual(results[0]["source_file"], "source.xml")
        self.assertEqual(bge.search_bge_m3("매출", corp_name="없는기업"), [])

    def test_multiple_corp_codes_are_filtered_before_dense_search(self):
        results = bge.search_bge_m3(
            "매출", corp_codes=["SK하이닉스"], year=2025, report_type="FY", top_k=10
        )
        self.assertEqual({row["corp_code"] for row in results}, {"SK하이닉스"})
        self.assertEqual(bge.search_bge_m3("매출", corp_codes=[]), [])
        self.assertEqual(
            bge.search_bge_m3("매출", corp_code="삼성전자", corp_codes=["SK하이닉스"]),
            [],
        )

    def test_invalid_vectors_rejected(self):
        for vector in (np.zeros((1,1024), dtype=np.float32), np.ones((1,384), dtype=np.float32),
                       np.full((1,1024), np.nan, dtype=np.float32), np.ones((1,1024), dtype=np.float32)):
            with self.assertRaises(ValueError):
                bge._validate_vectors(vector)


if __name__ == "__main__":
    unittest.main()
