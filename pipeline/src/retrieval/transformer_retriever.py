from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path

import numpy as np

from src.config import PROJECT_ROOT
from src.retrieval.lexical_scoring import effective_query, lexical_scores
from src.retrieval.vector_retriever import VECTOR_DB_PATH


MODEL_NAME = "intfloat/multilingual-e5-small"
DIMENSIONS = 384
TRANSFORMER_DB_PATH = PROJECT_ROOT / "db" / "vector" / "filings_e5.db"
MODEL_CACHE = PROJECT_ROOT / "db" / "vector" / "model_cache"
QUERY_MODEL_CACHE = MODEL_CACHE / "e5_v2"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


SCHEMA = """
CREATE TABLE IF NOT EXISTS vector_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS filing_chunks (
    chunk_id TEXT PRIMARY KEY,
    corp_name TEXT NOT NULL, corp_code TEXT NOT NULL, year INTEGER NOT NULL,
    report_type TEXT NOT NULL, rcept_no TEXT NOT NULL,
    section_name TEXT NOT NULL, subsection_name TEXT,
    source_file TEXT NOT NULL, chunk_text TEXT NOT NULL,
    embedding BLOB NOT NULL, embedding_model TEXT NOT NULL, dimensions INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_e5_filter ON filing_chunks(corp_code, year, report_type, rcept_no);
"""


@lru_cache(maxsize=1)
def _model():
    os.environ.setdefault("HF_HOME", str(QUERY_MODEL_CACHE))
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, cache_folder=str(QUERY_MODEL_CACHE))


def build_transformer_database(
    source_path: Path = VECTOR_DB_PATH,
    output_path: Path = TRANSFORMER_DB_PATH,
    batch_size: int = 64,
) -> dict[str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source:
        source.row_factory = sqlite3.Row
        rows = source.execute(
            """SELECT chunk_id, corp_name, corp_code, year, report_type, rcept_no,
                      section_name, subsection_name, source_file, chunk_text
               FROM filing_chunks ORDER BY chunk_id"""
        ).fetchall()
    model = _model()
    inserted = 0
    with sqlite3.connect(output_path) as target:
        target.executescript(SCHEMA)
        existing = {row[0] for row in target.execute("SELECT chunk_id FROM filing_chunks")}
        pending = [row for row in rows if row["chunk_id"] not in existing]
        target.executemany(
            "INSERT OR REPLACE INTO vector_metadata VALUES (?, ?)",
            (("embedding_model", MODEL_NAME), ("dimensions", str(DIMENSIONS)), ("source_db", str(source_path))),
        )
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            texts = [
                "passage: " + " ".join(filter(None, (row["section_name"], row["subsection_name"], row["chunk_text"])))
                for row in batch
            ]
            vectors = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
            target.executemany(
                "INSERT INTO filing_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (*tuple(row), np.asarray(vector, dtype=np.float32).tobytes(), MODEL_NAME, DIMENSIONS)
                    for row, vector in zip(batch, vectors)
                ],
            )
            inserted += len(batch)
            print(f"transformer embedding {min(offset + batch_size, len(pending))}/{len(pending)}", flush=True)
    return {"source_chunks": len(rows), "inserted": inserted, "total": len(rows)}


def search_filing_context_transformer(
    query: str,
    corp_name: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    top_k: int = 5,
    path: Path = TRANSFORMER_DB_PATH,
    hybrid: bool = False,
    dense_weight: float = 0.60,
    corp_code: str | None = None,
    rcept_no: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (("corp_name", corp_name), ("corp_code", corp_code), ("year", year), ("report_type", report_type), ("rcept_no", rcept_no)):
        if value is not None:
            clauses.append(f"{column} = ?"); params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT chunk_id, corp_name, corp_code, year, report_type, rcept_no,
                      section_name, subsection_name, source_file, chunk_text, embedding
               FROM filing_chunks""" + where,
            params,
        ).fetchall()
    if not rows:
        return []
    query_text = effective_query(query, corp_name, year)
    query_vector = _model().encode(["query: " + query_text], normalize_embeddings=True, show_progress_bar=False)[0]
    matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    dense_scores = matrix @ query_vector
    lexical = lexical_scores(query_text, rows)
    scores = dense_weight * dense_scores + (1.0 - dense_weight) * lexical if hybrid else dense_scores
    best = np.argsort(scores)[::-1][:top_k]
    results: list[dict] = []
    for index in best:
        item = dict(rows[int(index)]); item.pop("embedding")
        item["dense_score"] = float(dense_scores[int(index)])
        item["lexical_score"] = float(lexical[int(index)])
        item["final_score"] = float(scores[int(index)])
        item["score"] = item["final_score"]
        item["rank"] = len(results) + 1
        item["retrieval_method"] = "multilingual_e5_hybrid" if hybrid else "multilingual_e5_dense"
        results.append(item)
    return results


if __name__ == "__main__":
    result = build_transformer_database()
    print(f"E5 Vector DB 완료: {result}")
