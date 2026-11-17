from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np

from src.config import OPERATIONAL_COMPANIES, PROCESSED_DIR
from src.retrieval.lexical_scoring import HIGH_VALUE_TERMS, effective_query, lexical_scores
from src.retrieval.local_embedding import DIMENSIONS, MODEL_NAME, embed_text
from src.retrieval.operational_config import LEGACY_VECTOR_DB_PATH


VECTOR_DB_PATH = LEGACY_VECTOR_DB_PATH
LOW_VALUE_TERMS = ("목 차", "목차", "임원 및 직원", "임원보수", "주주총회")


SCHEMA = """
CREATE TABLE IF NOT EXISTS vector_metadata (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS filing_chunks (
    chunk_id TEXT PRIMARY KEY,
    corp_name TEXT NOT NULL, corp_code TEXT NOT NULL, year INTEGER NOT NULL,
    report_type TEXT NOT NULL, rcept_no TEXT NOT NULL,
    section_name TEXT NOT NULL, subsection_name TEXT,
    source_file TEXT NOT NULL, chunk_text TEXT NOT NULL,
    content_hash TEXT NOT NULL, embedding BLOB NOT NULL,
    embedding_model TEXT NOT NULL, dimensions INTEGER NOT NULL,
    selection_reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vector_filter
ON filing_chunks(corp_code, year, report_type, rcept_no);
"""


def _eligible(chunk: dict) -> tuple[bool, str]:
    text = chunk.get("chunk_text") or chunk.get("cleaned_text", "")
    heading = f"{chunk.get('section_name', '')} {chunk.get('subsection_name', '')}"
    if len(text) < 100:
        return False, "too_short"
    if any(term in heading for term in LOW_VALUE_TERMS):
        return False, "low_priority_section"
    meaningful = sum(char.isalpha() or "가" <= char <= "힣" for char in text)
    if meaningful / max(len(text), 1) < 0.18:
        return False, "mostly_numeric_or_structural"
    if any(term in heading for term in HIGH_VALUE_TERMS):
        return True, "high_priority_section"
    return True, "general_narrative"


def build_vector_database(
    path: Path = VECTOR_DB_PATH,
    companies: tuple[str, ...] = OPERATIONAL_COMPANIES,
) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk_files = sorted((PROCESSED_DIR / "filings" / "chunks").rglob("*.jsonl"))
    inserted = skipped = duplicates = selected_files = ignored_other_companies = 0
    seen_by_report: set[tuple[str, str]] = set()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("DELETE FROM filing_chunks")
        connection.executemany(
            "INSERT OR REPLACE INTO vector_metadata(key, value) VALUES (?, ?)",
            (("embedding_model", MODEL_NAME), ("dimensions", str(DIMENSIONS))),
        )
        for chunk_file in chunk_files:
            file_selected = False
            for line in chunk_file.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("corp_name") not in companies:
                    ignored_other_companies += 1
                    continue
                if not file_selected:
                    selected_files += 1
                    file_selected = True
                eligible, reason = _eligible(chunk)
                if not eligible:
                    skipped += 1
                    continue
                text = chunk.get("chunk_text") or chunk["cleaned_text"]
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                duplicate_key = (chunk["rcept_no"], content_hash)
                if duplicate_key in seen_by_report:
                    duplicates += 1
                    continue
                seen_by_report.add(duplicate_key)
                embedding_text = " ".join(
                    filter(None, (chunk.get("section_name"), chunk.get("subsection_name"), text))
                )
                vector = embed_text(embedding_text)
                connection.execute(
                    """INSERT OR REPLACE INTO filing_chunks VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk["chunk_id"], chunk["corp_name"], chunk["corp_code"], int(chunk["year"]),
                        chunk["report_type"], chunk["rcept_no"], chunk["section_name"],
                        chunk.get("subsection_name"), chunk["source_file"], text, content_hash,
                        vector.tobytes(), MODEL_NAME, DIMENSIONS, reason,
                    ),
                )
                inserted += 1
    return {
        "inserted": inserted, "skipped": skipped, "duplicates": duplicates,
        "files": selected_files, "ignored_other_companies": ignored_other_companies,
    }


def append_vector_database(
    path: Path = VECTOR_DB_PATH,
    companies: tuple[str, ...] = OPERATIONAL_COMPANIES,
) -> dict[str, int]:
    """Append selected companies without deleting or rewriting existing rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    chunk_files = sorted((PROCESSED_DIR / "filings" / "chunks").rglob("*.jsonl"))
    inserted = skipped = duplicates = existing_rows = selected_files = 0
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT OR IGNORE INTO vector_metadata(key, value) VALUES (?, ?)",
            (("embedding_model", MODEL_NAME), ("dimensions", str(DIMENSIONS))),
        )
        known_ids = {row[0] for row in connection.execute("SELECT chunk_id FROM filing_chunks")}
        seen_by_report = {
            (row[0], row[1])
            for row in connection.execute("SELECT rcept_no, content_hash FROM filing_chunks")
        }
        for chunk_file in chunk_files:
            file_selected = False
            for line in chunk_file.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("corp_name") not in companies:
                    continue
                if not file_selected:
                    selected_files += 1
                    file_selected = True
                if chunk["chunk_id"] in known_ids:
                    existing_rows += 1
                    continue
                eligible, reason = _eligible(chunk)
                if not eligible:
                    skipped += 1
                    continue
                text = chunk.get("chunk_text") or chunk["cleaned_text"]
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                duplicate_key = (chunk["rcept_no"], content_hash)
                if duplicate_key in seen_by_report:
                    duplicates += 1
                    continue
                embedding_text = " ".join(
                    filter(None, (chunk.get("section_name"), chunk.get("subsection_name"), text))
                )
                vector = embed_text(embedding_text)
                connection.execute(
                    """INSERT INTO filing_chunks VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk["chunk_id"], chunk["corp_name"], chunk["corp_code"], int(chunk["year"]),
                        chunk["report_type"], chunk["rcept_no"], chunk["section_name"],
                        chunk.get("subsection_name"), chunk["source_file"], text, content_hash,
                        vector.tobytes(), MODEL_NAME, DIMENSIONS, reason,
                    ),
                )
                known_ids.add(chunk["chunk_id"])
                seen_by_report.add(duplicate_key)
                inserted += 1
        connection.commit()
    return {
        "inserted": inserted, "skipped": skipped, "duplicates": duplicates,
        "existing": existing_rows, "files": selected_files,
    }


def search_filing_context(
    query: str,
    corp_name: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    top_k: int = 5,
    path: Path = VECTOR_DB_PATH,
    corp_code: str | None = None,
    rcept_no: str | None = None,
    corp_codes: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    clauses: list[str] = []
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
    for column, value in (("corp_name", corp_name), ("corp_code", corp_code), ("year", year), ("report_type", report_type), ("rcept_no", rcept_no)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT chunk_id, corp_name, corp_code, year, report_type, rcept_no, section_name, subsection_name, source_file, chunk_text, embedding FROM filing_chunks" + where,
            params,
        ).fetchall()
    if not rows:
        return []
    query_text = effective_query(query, corp_name, year)
    query_vector = embed_text(query_text)
    matrix = np.vstack([np.frombuffer(row["embedding"], dtype=np.float32) for row in rows])
    vector_scores = matrix @ query_vector
    lexical = lexical_scores(query_text, rows)
    scores = 0.35 * vector_scores + 0.65 * lexical
    best = np.argsort(scores)[::-1][:top_k]
    results: list[dict] = []
    for index in best:
        item = dict(rows[int(index)])
        item.pop("embedding")
        item["dense_score"] = float(vector_scores[int(index)])
        item["lexical_score"] = float(lexical[int(index)])
        item["final_score"] = float(scores[int(index)])
        item["score"] = item["final_score"]
        item["rank"] = len(results) + 1
        item["retrieval_method"] = "feature_hashing_hybrid"
        results.append(item)
    return results
