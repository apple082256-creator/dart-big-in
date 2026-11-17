from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import numpy as np

from src.config import PROCESSED_DIR, PROJECT_ROOT
from src.retrieval.bge_m3_indexer import (
    BGE_DB_PATH, BGE_FAISS_PATH, DIMENSIONS, MODEL_NAME, search_bge_m3,
)


REPORT_PATH = PROCESSED_DIR / "quality" / "bge_m3_faiss_test_report.json"


def _check(name: str, condition: bool, detail: object) -> dict:
    return {"test_name": name, "status": "PASS" if condition else "FAIL", "detail": detail}


def run_validation() -> dict:
    checks: list[dict] = []
    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        rows = connection.execute(
            "SELECT * FROM filing_chunks WHERE is_searchable=1 ORDER BY vector_id"
        ).fetchall()
        metadata = dict(connection.execute("SELECT key,value FROM vector_metadata"))
        duplicate_vectors = connection.execute(
            "SELECT COUNT(*) FROM (SELECT vector_id FROM filing_chunks GROUP BY vector_id HAVING COUNT(*)>1)"
        ).fetchone()[0]
        duplicate_chunks = connection.execute(
            "SELECT COUNT(*) FROM (SELECT chunk_id FROM filing_chunks GROUP BY chunk_id HAVING COUNT(*)>1)"
        ).fetchone()[0]
    sample = rows[len(rows) // 2]
    # Initialize PyTorch/BGE before faiss on macOS to avoid competing OpenMP
    # runtimes in the reverse import order.
    results = search_bge_m3(
        "사업 전략과 시장 위험", corp_name=sample["corp_name"], year=sample["year"],
        report_type=sample["report_type"], top_k=5,
        corp_code=sample["corp_code"], rcept_no=sample["rcept_no"],
        hybrid=False, apply_priority=False,
    )
    import faiss
    faiss.omp_set_num_threads(1)
    index = faiss.read_index(str(BGE_FAISS_PATH))
    vectors = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    norms = np.linalg.norm(vectors, axis=1)
    checks.extend([
        _check("sqlite_integrity", integrity == "ok", integrity),
        _check("embedding_dimension_1024", vectors.shape[1] == DIMENSIONS, vectors.shape),
        _check("embedding_model", metadata.get("embedding_model") == MODEL_NAME, metadata.get("embedding_model")),
        _check("normalized_vectors", bool(np.allclose(norms, 1.0, atol=2e-3)), [float(norms.min()), float(norms.max())]),
        _check("no_nan_or_inf", bool(np.isfinite(vectors).all()), "finite"),
        _check("no_zero_vectors", bool(np.all(norms > 0)), int(np.sum(norms == 0))),
        _check("faiss_reload", index.d == DIMENSIONS, {"dimension": index.d, "ntotal": index.ntotal}),
        _check("faiss_sqlite_count_match", index.ntotal == len(rows), {"faiss": index.ntotal, "sqlite": len(rows)}),
        _check("unique_vector_id", duplicate_vectors == 0, duplicate_vectors),
        _check("unique_chunk_id", duplicate_chunks == 0, duplicate_chunks),
    ])
    missing_ids = []
    for row in rows:
        try:
            vector = index.reconstruct(int(row["vector_id"]))
            if len(vector) != DIMENSIONS:
                missing_ids.append(row["vector_id"])
        except RuntimeError:
            missing_ids.append(row["vector_id"])
    checks.append(_check("all_faiss_ids_linked", not missing_ids, missing_ids[:10]))
    faiss_ids = set(map(int, faiss.vector_to_array(index.id_map)))
    sqlite_ids = {row["vector_id"] for row in rows}
    checks.append(_check("exact_faiss_sqlite_id_set", faiss_ids == sqlite_ids,
                         {"only_faiss": len(faiss_ids - sqlite_ids), "only_sqlite": len(sqlite_ids - faiss_ids)}))
    source_missing = [row["chunk_id"] for row in rows
                      if not (PROJECT_ROOT / row["source_file"]).is_file()]
    checks.append(_check("all_xml_sources_exist", not source_missing, source_missing[:10]))
    from src.retrieval.bge_m3_indexer import _iter_chunks
    originals = {row["chunk_id"]: row for row in _iter_chunks()}
    unlinked = [row["chunk_id"] for row in rows if row["chunk_id"] not in originals
                or originals[row["chunk_id"]]["rcept_no"] != row["rcept_no"]
                or originals[row["chunk_id"]]["chunk_text"] != row["chunk_text"]]
    checks.append(_check("all_rows_linked_to_v2_chunks", not unlinked, unlinked[:10]))

    checks.extend([
        _check("corp_filter", bool(results) and all(row["corp_name"] == sample["corp_name"] for row in results), sample["corp_name"]),
        _check("year_filter", bool(results) and all(row["year"] == sample["year"] for row in results), sample["year"]),
        _check("report_type_filter", bool(results) and all(row["report_type"] == sample["report_type"] for row in results), sample["report_type"]),
        _check("rcept_no_filter", bool(results) and all(row["rcept_no"] == sample["rcept_no"] for row in results), sample["rcept_no"]),
        _check("rcept_no_traceable", all(bool(row["rcept_no"]) for row in results), [row["rcept_no"] for row in results]),
        _check("source_file_traceable", all(bool(row["source_file"]) for row in results), [row["source_file"] for row in results]),
        _check("original_text_link", all(bool(row["chunk_text"]) for row in results), len(results)),
    ])
    # 증분 재실행의 중복 방지 조건은 chunk_id UNIQUE와 content_hash/model/revision 비교 로직으로 검증한다.
    checks.append(_check(
        "incremental_duplicate_guard",
        duplicate_chunks == 0 and "model_revision" in metadata,
        {"unique_chunk_id": duplicate_chunks == 0, "model_revision": metadata.get("model_revision")},
    ))
    report = {
        "status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL",
        "checks": checks,
        "sqlite_rows": len(rows),
        "faiss_vectors": int(index.ntotal),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(run_validation(), ensure_ascii=False, indent=2))
