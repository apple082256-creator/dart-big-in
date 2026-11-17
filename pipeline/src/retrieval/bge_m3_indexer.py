from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from src.config import OPERATIONAL_COMPANIES, PROJECT_ROOT
from src.preprocess.text_chunker_v2 import CHUNKS_V2_DIR, CHUNK_VERSION
from src.retrieval.lexical_scoring import effective_query, lexical_scores
from src.retrieval.operational_config import (
    BGE_DB_PATH,
    BGE_FAISS_PATH,
    EXPECTED_BGE_COMPANIES,
    EXPECTED_BGE_DIMENSIONS,
    EXPECTED_BGE_MODEL,
    EXPECTED_BGE_REVISION,
)


# PyTorch and faiss can load competing OpenMP runtimes on macOS. Keeping the
# query path single-threaded avoids a reproducible native segfault (exit 139).
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")


MODEL_NAME = EXPECTED_BGE_MODEL
MODEL_REVISION = EXPECTED_BGE_REVISION
DIMENSIONS = EXPECTED_BGE_DIMENSIONS
MODEL_CACHE = PROJECT_ROOT / "db" / "vector" / "model_cache" / "bge_m3"
BUILD_REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "quality" / "bge_m3_build_report.json"


SCHEMA = """
CREATE TABLE IF NOT EXISTS vector_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS filing_chunks (
    vector_id INTEGER PRIMARY KEY,
    chunk_id TEXT NOT NULL UNIQUE,
    corp_name TEXT NOT NULL,
    corp_code TEXT NOT NULL,
    year INTEGER NOT NULL,
    report_type TEXT NOT NULL,
    rcept_no TEXT NOT NULL,
    section_name TEXT NOT NULL,
    subsection_name TEXT,
    source_file TEXT NOT NULL,
    source_document_id TEXT,
    source_document_sha256 TEXT,
    original_text TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_version TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    search_priority REAL NOT NULL,
    selection_reason TEXT NOT NULL,
    is_searchable INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    embedded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bge_filter
ON filing_chunks(corp_code, corp_name, year, report_type, rcept_no, is_searchable);
CREATE INDEX IF NOT EXISTS idx_bge_chunk_id ON filing_chunks(chunk_id);
"""


def _configure_cache() -> None:
    MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(MODEL_CACHE))
    os.environ.setdefault("HF_HUB_CACHE", str(MODEL_CACHE / "hub"))


@lru_cache(maxsize=1)
def load_tokenizer():
    _configure_cache()
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(MODEL_NAME, revision=MODEL_REVISION, cache_dir=str(MODEL_CACHE / "hub"), use_fast=True)


@lru_cache(maxsize=1)
def load_model():
    _configure_cache()
    import torch
    from sentence_transformers import SentenceTransformer

    requested_device = os.environ.get("BGE_M3_DEVICE", "auto")
    if requested_device not in {"auto", "cuda", "cpu", "mps"}:
        raise ValueError("BGE_M3_DEVICE must be auto, cuda, cpu, or mps")
    if requested_device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA를 요청했지만 GPU 접근 또는 드라이버 호환성을 확인할 수 없습니다.")
    # CUDA는 자동 사용하되 macOS MPS는 이 모델에서 종료될 수 있어 명시 요청 때만 사용한다.
    device = requested_device if requested_device != "auto" else (
        "cuda" if torch.cuda.is_available() else "cpu"
    )
    print(f"BGE-M3 device: {device}", flush=True)
    model = SentenceTransformer(
        MODEL_NAME,
        revision=MODEL_REVISION,
        cache_folder=str(MODEL_CACHE / "hub"),
        device=device,
    )
    model.max_seq_length = 8192
    requested_dtype = os.environ.get("BGE_M3_INFERENCE_DTYPE", "float32").strip().lower()
    if requested_dtype not in {"float32", "float16"}:
        raise ValueError("BGE_M3_INFERENCE_DTYPE must be float32 or float16")
    if requested_dtype == "float16":
        if device != "cuda":
            raise ValueError("float16 BGE-M3 추론은 CUDA에서만 지원합니다.")
        model.half()
    dimension_getter = getattr(model, "get_embedding_dimension", None)
    actual_dimensions = (
        dimension_getter() if dimension_getter else model.get_sentence_embedding_dimension()
    )
    if actual_dimensions != DIMENSIONS:
        raise ValueError("모델의 실제 출력 차원이 1024가 아닙니다.")
    actual_revision = model_revision(model)
    if actual_revision != MODEL_REVISION:
        raise ValueError(
            f"BGE-M3 모델 revision 불일치: expected={MODEL_REVISION}, actual={actual_revision}"
        )
    return model


def model_revision(model=None) -> str:
    model = model or load_model()
    try:
        revision = model[0].auto_model.config._commit_hash
        if revision:
            return str(revision)
    except (AttributeError, IndexError, TypeError):
        pass
    snapshot = MODEL_CACHE / "hub" / "models--BAAI--bge-m3" / "snapshots" / MODEL_REVISION
    if snapshot.exists():
        return MODEL_REVISION
    return "unknown"


def _iter_chunks(
    companies: tuple[str, ...] = OPERATIONAL_COMPANIES,
    chunks_dir: Path | None = None,
) -> list[dict]:
    chunks_dir = chunks_dir or CHUNKS_V2_DIR
    rows: list[dict] = []
    for path in sorted(chunks_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunk = json.loads(line)
                if chunk.get("corp_name") in companies:
                    rows.append(chunk)
    return rows


def _validate_vectors(vectors: np.ndarray) -> None:
    if vectors.ndim != 2 or vectors.shape[1] != DIMENSIONS:
        raise ValueError(f"BGE-M3 벡터 차원 오류: {vectors.shape}, 예상 (*, {DIMENSIONS})")
    if not np.isfinite(vectors).all():
        raise ValueError("BGE-M3 벡터에 NaN 또는 무한대가 있습니다.")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms == 0):
        raise ValueError("BGE-M3 0벡터가 있습니다.")
    if not np.allclose(norms, 1.0, atol=2e-3):
        raise ValueError(f"BGE-M3 정규화 오류: norm 범위 {norms.min()}~{norms.max()}")


def _next_vector_id(connection: sqlite3.Connection) -> int:
    """Allocate strictly after the largest persisted ID without renumbering."""
    return int(connection.execute(
        "SELECT COALESCE(MAX(vector_id), -1) + 1 FROM filing_chunks"
    ).fetchone()[0])


def _write_faiss_index(connection: sqlite3.Connection, path: Path | None = None) -> int:
    import faiss

    path = path or BGE_FAISS_PATH
    rows = connection.execute(
        "SELECT vector_id, embedding FROM filing_chunks WHERE is_searchable=1 ORDER BY vector_id"
    ).fetchall()
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(DIMENSIONS))
    if rows:
        ids = np.asarray([row[0] for row in rows], dtype=np.int64)
        vectors = np.vstack([np.frombuffer(row[1], dtype=np.float32) for row in rows]).astype(np.float32)
        _validate_vectors(vectors)
        index.add_with_ids(vectors, ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    faiss.write_index(index, str(temporary))
    temporary.replace(path)
    return int(index.ntotal)


def build_bge_m3_index(
    batch_size: int = 4,
    *,
    chunks_dir: Path | None = None,
    db_path: Path | None = None,
    faiss_path: Path | None = None,
    report_path: Path | None = None,
) -> dict[str, object]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    chunks_dir = chunks_dir or CHUNKS_V2_DIR
    db_path = db_path or BGE_DB_PATH
    faiss_path = faiss_path or BGE_FAISS_PATH
    report_path = report_path or BUILD_REPORT_PATH
    chunks = _iter_chunks(chunks_dir=chunks_dir)
    if not chunks:
        raise RuntimeError("chunks_v2가 없습니다. 먼저 python -m src.filing_pipeline_v2를 실행하세요.")
    if len({chunk["chunk_id"] for chunk in chunks}) != len(chunks):
        raise ValueError("입력 chunk_id가 중복됩니다.")
    model = load_model()
    revision = model_revision(model)
    if revision == "unknown":
        raise ValueError("재현 가능한 모델 revision을 확인할 수 없습니다.")
    started = time.perf_counter()
    inserted = updated = skipped = failed = 0
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(filing_chunks)")}
        for column in ("source_document_id", "source_document_sha256"):
            if column not in columns:
                connection.execute(f"ALTER TABLE filing_chunks ADD COLUMN {column} TEXT")
        existing = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT chunk_id, content_hash, embedding_model, model_revision FROM filing_chunks"
            )
        }
        pending = [
            chunk for chunk in chunks
            if existing.get(chunk["chunk_id"]) != (chunk["content_hash"], MODEL_NAME, revision)
        ]
        # Length bucketing changes neither text nor encoding parameters, but
        # avoids spending most GPU work on padding a mixed-length batch.
        pending.sort(key=lambda chunk: (int(chunk["token_count"]), chunk["chunk_id"]))
        skipped = len(chunks) - len(pending)
        next_id = _next_vector_id(connection)
        # Let SentenceTransformer keep the GPU-safe model batch while grouping
        # SQLite writes into a larger transaction. This avoids an fsync and a
        # terminal write for every tiny GPU batch without changing embeddings.
        transaction_size = max(batch_size, 256)
        for offset in range(0, len(pending), transaction_size):
            batch = pending[offset:offset + transaction_size]
            texts = [
                " ".join(filter(None, (row.get("section_name"), row.get("subsection_name"), row["chunk_text"])))
                for row in batch
            ]
            try:
                vectors = np.asarray(
                    model.encode(
                        texts, batch_size=batch_size, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_numpy=True,
                    ),
                    dtype=np.float32,
                )
                _validate_vectors(vectors)
            except Exception:
                failed += len(batch)
                raise
            for chunk, vector in zip(batch, vectors):
                current = existing.get(chunk["chunk_id"])
                if current is None:
                    vector_id = next_id
                    next_id += 1
                    inserted += 1
                else:
                    vector_id = connection.execute(
                        "SELECT vector_id FROM filing_chunks WHERE chunk_id=?", (chunk["chunk_id"],)
                    ).fetchone()[0]
                    updated += 1
                connection.execute(
                    """INSERT INTO filing_chunks (
                        vector_id, chunk_id, corp_name, corp_code, year, report_type, rcept_no,
                        section_name, subsection_name, source_file, source_document_id,
                        source_document_sha256, original_text, chunk_text,
                        content_hash, chunk_version, token_count, content_type, search_priority,
                        selection_reason, is_searchable, embedding, embedding_model, model_revision,
                        embedding_dimension
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        corp_name=excluded.corp_name, corp_code=excluded.corp_code,
                        year=excluded.year, report_type=excluded.report_type, rcept_no=excluded.rcept_no,
                        section_name=excluded.section_name, subsection_name=excluded.subsection_name,
                        source_file=excluded.source_file,
                        source_document_id=excluded.source_document_id,
                        source_document_sha256=excluded.source_document_sha256,
                        original_text=excluded.original_text,
                        chunk_text=excluded.chunk_text, content_hash=excluded.content_hash,
                        chunk_version=excluded.chunk_version, token_count=excluded.token_count,
                        content_type=excluded.content_type, search_priority=excluded.search_priority,
                        selection_reason=excluded.selection_reason, is_searchable=excluded.is_searchable,
                        embedding=excluded.embedding, embedding_model=excluded.embedding_model,
                        model_revision=excluded.model_revision,
                        embedding_dimension=excluded.embedding_dimension,
                        embedded_at=CURRENT_TIMESTAMP""",
                    (
                        vector_id, chunk["chunk_id"], chunk["corp_name"], chunk["corp_code"],
                        int(chunk["year"]), chunk["report_type"], chunk["rcept_no"],
                        chunk["section_name"], chunk.get("subsection_name"), chunk["source_file"],
                        chunk.get("source_document_id"), chunk.get("source_document_sha256"),
                        chunk["original_text"], chunk["chunk_text"], chunk["content_hash"],
                        chunk.get("chunk_version", CHUNK_VERSION), int(chunk["token_count"]),
                        chunk["content_type"], float(chunk["search_priority"]),
                        chunk["selection_reason"], int(bool(chunk.get("is_searchable", True))),
                        vector.tobytes(), MODEL_NAME, revision, DIMENSIONS,
                    ),
                )
            connection.commit()
            print(f"BGE-M3 embedding {min(offset + len(batch), len(pending))}/{len(pending)}", flush=True)
        metadata = {
            "embedding_model": MODEL_NAME,
            "model_revision": revision,
            "embedding_dimension": str(DIMENSIONS),
            "normalize_embeddings": "true",
            "chunk_version": CHUNK_VERSION,
            "faiss_index_type": "IndexIDMap2(IndexFlatIP)",
            "inference_dtype": os.environ.get("BGE_M3_INFERENCE_DTYPE", "float32").strip().lower(),
        }
        connection.executemany(
            "INSERT OR REPLACE INTO vector_metadata(key,value) VALUES (?,?)", metadata.items()
        )
        faiss_count = (_write_faiss_index(connection) if faiss_path == BGE_FAISS_PATH
                       else _write_faiss_index(connection, faiss_path))
        sqlite_count = connection.execute("SELECT COUNT(*) FROM filing_chunks WHERE is_searchable=1").fetchone()[0]
        connection.execute(
            "INSERT OR REPLACE INTO vector_metadata(key,value) VALUES (?,?)",
            ("searchable_vector_count", str(sqlite_count)),
        )
    elapsed = time.perf_counter() - started
    if db_path == BGE_DB_PATH and faiss_path == BGE_FAISS_PATH:
        _load_faiss_index.cache_clear()
        validate_bge_operational_assets.cache_clear()
    report = {
        "model": MODEL_NAME, "model_revision": revision, "dimensions": DIMENSIONS,
        "source_chunks": len(chunks), "inserted": inserted, "updated": updated,
        "skipped_existing": skipped, "failed": failed, "sqlite_searchable": sqlite_count,
        "faiss_vectors": faiss_count, "elapsed_seconds": elapsed,
        "sqlite_path": _display_path(db_path),
        "faiss_path": _display_path(faiss_path),
        "embedding_device": str(getattr(model, "device", "unknown")),
        "inference_dtype": os.environ.get("BGE_M3_INFERENCE_DTYPE", "float32").strip().lower(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


@lru_cache(maxsize=1)
def _load_faiss_index():
    import faiss
    if not BGE_FAISS_PATH.is_file():
        raise FileNotFoundError(f"BGE-M3 FAISS 인덱스가 없습니다: {BGE_FAISS_PATH}")
    return faiss.read_index(str(BGE_FAISS_PATH))


@lru_cache(maxsize=1)
def validate_bge_operational_assets() -> dict[str, object]:
    """Fail closed when the promoted SQLite/FAISS pair is not the validated build."""
    if not BGE_DB_PATH.is_file():
        raise FileNotFoundError(f"BGE-M3 SQLite DB가 없습니다: {BGE_DB_PATH}")
    if not BGE_FAISS_PATH.is_file():
        raise FileNotFoundError(f"BGE-M3 FAISS 인덱스가 없습니다: {BGE_FAISS_PATH}")

    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM vector_metadata"))
        rows = connection.execute(
            """SELECT vector_id, chunk_id, corp_name, embedding_model, model_revision,
                      embedding_dimension, length(embedding)
               FROM filing_chunks WHERE is_searchable=1 ORDER BY vector_id"""
        ).fetchall()

    expected_metadata = {
        "embedding_model": MODEL_NAME,
        "model_revision": MODEL_REVISION,
        "embedding_dimension": str(DIMENSIONS),
        "normalize_embeddings": "true",
        "faiss_index_type": "IndexIDMap2(IndexFlatIP)",
    }
    mismatched = {
        key: {"expected": expected, "actual": metadata.get(key)}
        for key, expected in expected_metadata.items()
        if metadata.get(key) != expected
    }
    if mismatched:
        raise RuntimeError(f"BGE-M3 인덱스 메타데이터 불일치: {mismatched}")
    metadata_count = metadata.get("searchable_vector_count")
    if metadata_count is not None and int(metadata_count) != len(rows):
        raise RuntimeError(
            "BGE-M3 검색 가능 레코드 수 메타데이터 불일치: "
            f"metadata={metadata_count}, actual={len(rows)}"
        )

    vector_ids = [int(row[0]) for row in rows]
    chunk_ids = [str(row[1]) for row in rows]
    if len(vector_ids) != len(set(vector_ids)):
        raise RuntimeError("BGE-M3 SQLite에 중복 vector_id가 있습니다.")
    if len(chunk_ids) != len(set(chunk_ids)):
        raise RuntimeError("BGE-M3 SQLite에 중복 chunk_id가 있습니다.")
    invalid_rows = [
        row[0] for row in rows
        if row[3] != MODEL_NAME
        or row[4] != MODEL_REVISION
        or int(row[5]) != DIMENSIONS
        or int(row[6]) != DIMENSIONS * np.dtype(np.float32).itemsize
    ]
    if invalid_rows:
        raise RuntimeError(f"BGE-M3 SQLite 벡터 호환성 오류: vector_id={invalid_rows[:5]}")

    companies = tuple(sorted({str(row[2]) for row in rows}))
    expected_companies = tuple(sorted(EXPECTED_BGE_COMPANIES))
    if companies != expected_companies:
        raise RuntimeError(
            f"BGE-M3 지원 기업 불일치: expected={expected_companies}, actual={companies}"
        )

    index = _load_faiss_index()
    if int(index.d) != DIMENSIONS:
        raise RuntimeError(f"BGE-M3 FAISS 차원 불일치: expected={DIMENSIONS}, actual={index.d}")
    if int(index.ntotal) != len(rows):
        raise RuntimeError(
            f"BGE-M3 FAISS/SQLite 수 불일치: faiss={index.ntotal}, sqlite={len(rows)}"
        )
    try:
        import faiss
        faiss_ids = {int(value) for value in faiss.vector_to_array(index.id_map)}
    except (AttributeError, TypeError) as exc:
        raise RuntimeError("BGE-M3 FAISS 인덱스에서 ID map을 읽을 수 없습니다.") from exc
    sqlite_ids = set(vector_ids)
    orphan_faiss = sorted(faiss_ids - sqlite_ids)
    missing_faiss = sorted(sqlite_ids - faiss_ids)
    if orphan_faiss or missing_faiss:
        raise RuntimeError(
            "BGE-M3 FAISS/SQLite ID 연결 오류: "
            f"orphan_faiss={orphan_faiss[:5]}, missing_faiss={missing_faiss[:5]}"
        )
    return {
        "status": "PASS",
        "model": MODEL_NAME,
        "model_revision": MODEL_REVISION,
        "dimensions": DIMENSIONS,
        "searchable_vectors": len(rows),
        "companies": list(EXPECTED_BGE_COMPANIES),
        "sqlite_path": str(BGE_DB_PATH),
        "faiss_path": str(BGE_FAISS_PATH),
    }


def combine_bge_scores(
    dense_scores: np.ndarray,
    lexical_scores_array: np.ndarray,
    search_priorities: np.ndarray,
    *,
    hybrid: bool,
    apply_priority: bool,
    dense_weight: float = 0.60,
) -> np.ndarray:
    """Combine BGE scores without coupling lexical and priority policies."""
    if not 0.0 <= dense_weight <= 1.0:
        raise ValueError("dense_weight는 0과 1 사이여야 합니다.")
    dense_scores = np.asarray(dense_scores, dtype=np.float32)
    lexical_scores_array = np.asarray(lexical_scores_array, dtype=np.float32)
    search_priorities = np.asarray(search_priorities, dtype=np.float32)
    if dense_scores.shape != lexical_scores_array.shape or dense_scores.shape != search_priorities.shape:
        raise ValueError("dense, lexical, search_priority 점수 배열의 shape이 같아야 합니다.")
    if hybrid:
        final = dense_weight * dense_scores + (1.0 - dense_weight) * lexical_scores_array
    else:
        final = dense_scores.copy()
    if apply_priority:
        final *= 0.90 + 0.10 * search_priorities
    return final


def bge_retrieval_method_name(*, hybrid: bool, apply_priority: bool) -> str:
    if hybrid:
        return "bge_m3_priority_hybrid" if apply_priority else "bge_m3_hybrid"
    return "bge_m3_priority_dense" if apply_priority else "bge_m3_dense"


def search_bge_m3(
    query: str,
    corp_name: str | None = None,
    corp_code: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    rcept_no: str | None = None,
    top_k: int = 5,
    hybrid: bool = False,
    apply_priority: bool = False,
    dense_weight: float = 0.60,
    corp_codes: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if not 0 <= dense_weight <= 1:
        raise ValueError("dense_weight must be between 0 and 1")

    # Production searches fail closed if the promoted immutable asset pair is
    # missing or incompatible. Tests and explicit alternate paths can still
    # exercise the generic search implementation with temporary assets.
    from src.retrieval.operational_config import BGE_DB_PATH as OPERATIONAL_BGE_DB_PATH
    if BGE_DB_PATH == OPERATIONAL_BGE_DB_PATH:
        validate_bge_operational_assets()

    clauses = ["is_searchable=1"]
    params: list[object] = []
    normalized_codes = tuple(dict.fromkeys(
        str(code).strip() for code in (corp_codes or ()) if str(code).strip()
    ))
    if corp_codes is not None and not normalized_codes:
        return []
    if corp_code is not None and normalized_codes and corp_code not in normalized_codes:
        return []
    if normalized_codes:
        clauses.append("corp_code IN (" + ",".join("?" for _ in normalized_codes) + ")")
        params.extend(normalized_codes)
    for column, value in (
        ("corp_name", corp_name), ("corp_code", corp_code), ("year", year),
        ("report_type", report_type), ("rcept_no", rcept_no),
    ):
        if value is not None:
            clauses.append(f"{column}=?")
            params.append(value)
    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT vector_id, chunk_id, corp_name, corp_code, year, report_type, rcept_no,
                      section_name, subsection_name, source_file, chunk_text, content_hash,
                      chunk_version, token_count, content_type, search_priority, selection_reason,
                      embedding_model, model_revision, embedding_dimension, embedding
               FROM filing_chunks WHERE """ + " AND ".join(clauses) + " ORDER BY vector_id",
            params,
        ).fetchall()
    if not rows:
        return []
    query_text = effective_query(query, corp_name, year)
    query_vector = np.asarray(
        load_model().encode([query_text], normalize_embeddings=True, show_progress_bar=False),
        dtype=np.float32,
    )
    _validate_vectors(query_vector)
    # Import faiss after PyTorch has initialized its runtime. On macOS the
    # reverse import order can load two OpenMP runtimes and abort the process.
    import faiss
    faiss.omp_set_num_threads(1)
    # SQLite applies the metadata filter first. Reading the matching vectors from
    # its BLOB column avoids a macOS faiss IndexIDMap2.reconstruct segfault while
    # preserving exact IndexFlatIP scoring on the filtered subset.
    vectors = np.vstack([
        np.frombuffer(row["embedding"], dtype=np.float32) for row in rows
    ]).astype(np.float32)
    filtered_index = faiss.IndexFlatIP(DIMENSIONS)
    filtered_index.add(vectors)
    dense_scores_all, positions = filtered_index.search(query_vector, len(rows))
    dense_by_position = np.empty(len(rows), dtype=np.float32)
    dense_by_position[positions[0]] = dense_scores_all[0]
    lexical = lexical_scores(query_text, rows)
    priorities = np.asarray([float(row["search_priority"]) for row in rows], dtype=np.float32)
    final = combine_bge_scores(
        dense_by_position, lexical, priorities,
        hybrid=hybrid, apply_priority=apply_priority, dense_weight=dense_weight,
    )
    method = bge_retrieval_method_name(hybrid=hybrid, apply_priority=apply_priority)
    best = np.argsort(final)[::-1][:top_k]
    results: list[dict] = []
    for rank, index in enumerate(best, start=1):
        item = dict(rows[int(index)])
        item.pop("embedding", None)
        item.update({
            "dense_score": float(dense_by_position[int(index)]),
            "lexical_score": float(lexical[int(index)]),
            "search_priority_applied": apply_priority,
            "priority_multiplier": float(0.90 + 0.10 * priorities[int(index)]) if apply_priority else 1.0,
            "final_score": float(final[int(index)]),
            "score": float(final[int(index)]),
            "rank": rank,
            "retrieval_method": method,
        })
        results.append(item)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="BGE-M3 Dense 임베딩 + FAISS 인덱스 생성")
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(build_bge_m3_index(args.batch_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
