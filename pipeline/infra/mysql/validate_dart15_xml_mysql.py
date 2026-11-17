"""Validate a complete local→MySQL snapshot and optionally promote it.

The filename is retained for backward-compatible runbooks, but expected counts
and KRX memberships are derived from the local operational snapshot so the
validator remains valid as companies are added incrementally.
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_source_documents_to_mysql import document_id, source_manifest  # noqa: E402
from sync_sqlite_to_mysql import MySQL, build_tables, lit, sha256  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FINANCIAL_DB = PROJECT_ROOT / "db" / "financial" / "financial.db"
BGE_DB = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
BGE_FAISS = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.faiss"
def canonical(value: object) -> str:
    if value is None:
        return "\x1e"
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def python_crc(rows: list[tuple], indices: list[int]) -> int:
    return sum(zlib.crc32("\x1f".join(canonical(row[i]) for i in indices).encode("utf-8"))
               for row in rows)


def mysql_crc_expression(columns: list[str]) -> str:
    values = ",".join(f"COALESCE(CAST(`{column}` AS CHAR),CHAR(30))" for column in columns)
    return f"SUM(CRC32(CONCAT_WS(CHAR(31),{values})))"


def compare_companies(
    db: MySQL,
    tables: dict[str, dict],
    corp_codes: set[str],
    problems: list[str],
) -> None:
    exclusions = {
        "financial_facts": {"data_version", "is_active"},
        "capex_facts": {"data_version", "is_active"},
        "filings": {"data_version", "is_active"},
        "chunk_metadata": {"search_priority", "embedded_at", "index_version", "data_version", "is_active"},
    }
    for table, excluded in exclusions.items():
        spec = tables[table]
        columns = [column for column in spec["columns"] if column not in excluded]
        indices = [spec["columns"].index(column) for column in columns]
        corp_index = spec["columns"].index("corp_code")
        for corp_code in sorted(corp_codes):
            rows = [row for row in spec["rows"] if row[corp_index] == corp_code]
            expected = (len(rows), python_crc(rows, indices))
            result = db.query(
                f"SELECT COUNT(*),COALESCE({mysql_crc_expression(columns)},0) FROM {table} "
                f"WHERE is_active=1 AND corp_code={lit(corp_code)}"
            )[0]
            actual = (int(result[0]), int(result[1]))
            if actual != expected:
                problems.append(f"로컬 원천 불일치 {table}/{corp_code}: {actual} != {expected}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()
    index_version = f"bge-m3-{args.data_version}"
    problems: list[str] = []

    with sqlite3.connect(FINANCIAL_DB) as fin, sqlite3.connect(BGE_DB) as bge:
        tables = build_tables(fin, bge, args.data_version, index_version)
        expected_krx = {
            row[0]: int(row[1])
            for row in fin.execute(
                "SELECT i.index_code,COUNT(c.corp_code) FROM krx_indices i "
                "LEFT JOIN krx_index_constituents c "
                "ON c.index_code=i.index_code AND c.is_current=1 "
                "WHERE i.is_active=1 GROUP BY i.index_code"
            )
        }
        local_embeddings = bge.execute(
            "SELECT vector_id,embedding FROM filing_chunks ORDER BY vector_id"
        ).fetchall()
        content_crc = sum(zlib.crc32(row[0].encode("utf-8"))
                          for row in bge.execute("SELECT content_hash FROM filing_chunks"))
    documents, mappings = source_manifest()
    expected_counts = {
        table: len(tables[table]["rows"])
        for table in (
            "companies", "financial_facts", "capex_facts", "filings",
            "chunk_metadata", "account_mapping", "coverage",
            "company_financial_profiles", "financial_collection_status",
            "financial_metric_definitions", "financial_metric_mappings",
            "financial_metric_values",
        )
    }
    expected_counts.update(
        chunk_embeddings=len(local_embeddings),
        filing_source_documents=len(documents),
        chunk_source_documents=len(mappings),
    )
    company_columns = tables["companies"]["columns"]
    corp_index = company_columns.index("corp_code")
    expected_corps = {row[corp_index] for row in tables["companies"]["rows"]}

    db = MySQL()
    try:
        for table, expected in expected_counts.items():
            active = " WHERE is_active=1" if table in {
                "financial_facts", "capex_facts", "filings", "chunk_metadata",
                "chunk_embeddings", "filing_source_documents",
            } else ""
            actual = int(db.scalar(f"SELECT COUNT(*) FROM {table}{active}") or 0)
            if actual != expected:
                problems.append(f"{table}: {actual} != {expected}")

        version = db.query(
            "SELECT financial_sqlite_sha256,vector_sqlite_sha256,faiss_sha256,index_version "
            f"FROM data_versions WHERE data_version={lit(args.data_version)}"
        )
        expected_hashes = (sha256(FINANCIAL_DB), sha256(BGE_DB), sha256(BGE_FAISS), index_version)
        if len(version) != 1 or tuple(version[0]) != expected_hashes:
            problems.append("data_versions 원천 해시/index_version 불일치")

        compare_companies(db, tables, expected_corps, problems)

        for table in (
            "krx_indices", "krx_index_constituents", "coverage",
            "company_financial_profiles", "financial_collection_status",
            "financial_metric_definitions", "financial_metric_mappings",
            "financial_metric_values",
        ):
            spec = tables[table]
            columns = spec["columns"]
            expected = (len(spec["rows"]), python_crc(spec["rows"], list(range(len(columns)))))
            actual_row = db.query(
                f"SELECT COUNT(*),COALESCE({mysql_crc_expression(columns)},0) FROM {table}"
            )[0]
            actual = (int(actual_row[0]), int(actual_row[1]))
            if actual != expected:
                problems.append(f"금융 계층 원천 불일치 {table}: {actual} != {expected}")

        expected_embedding = (
            len(local_embeddings),
            sum(zlib.crc32(row[1]) for row in local_embeddings),
            sum(row[0] for row in local_embeddings),
        )
        actual_embedding = tuple(map(int, db.query(
            "SELECT COUNT(*),SUM(CRC32(embedding)),SUM(vector_id) "
            "FROM chunk_embeddings WHERE is_active=1"
        )[0]))
        if actual_embedding != expected_embedding:
            problems.append(f"embedding BLOB/vector_id 불일치: {actual_embedding} != {expected_embedding}")

        mysql_content_crc = int(db.scalar(
            "SELECT SUM(CRC32(content_hash)) FROM chunk_metadata WHERE is_active=1"
        ) or 0)
        if mysql_content_crc != content_crc:
            problems.append("chunk content_hash 체크섬 불일치")

        doc_rows = db.query(
            "SELECT document_id,SHA2(raw_xml,256),OCTET_LENGTH(raw_xml),rcept_no,source_path "
            "FROM filing_source_documents WHERE is_active=1"
        )
        actual_docs = {row[0]: (row[1].lower(), int(row[2]), row[3], row[4]) for row in doc_rows}
        expected_docs = {}
        for doc in documents:
            expected_docs[doc.document_id] = (doc.digest, doc.byte_size, doc.rcept_no, doc.source_path)
        if actual_docs != expected_docs:
            problems.append("원본 XML BLOB SHA-256/크기/경로 불일치")

        joined, exact = db.query(
            "SELECT COUNT(*),SUM(c.rcept_no=d.rcept_no AND c.source_file=d.source_path) "
            "FROM chunk_source_documents m "
            "JOIN chunk_metadata c ON c.chunk_id=m.chunk_id AND c.is_active=1 "
            "JOIN filing_source_documents d ON d.document_id=m.document_id AND d.is_active=1"
        )[0]
        if int(joined) != len(mappings) or int(exact or 0) != len(mappings):
            problems.append(f"chunk→XML 연결 불일치: joined={joined}, exact={exact}")

        duplicates = db.query(
            "SELECT "
            "(SELECT COUNT(*)-COUNT(DISTINCT chunk_id) FROM chunk_metadata WHERE is_active=1),"
            "(SELECT COUNT(*)-COUNT(DISTINCT vector_id) FROM chunk_metadata WHERE is_active=1),"
            "(SELECT COUNT(*) FROM chunk_embeddings e LEFT JOIN chunk_metadata m "
            " ON m.chunk_id=e.chunk_id AND m.vector_id=e.vector_id AND m.is_active=1 "
            " WHERE e.is_active=1 AND m.chunk_id IS NULL)"
        )[0]
        if tuple(map(int, duplicates)) != (0, 0, 0):
            problems.append(f"chunk/vector 중복 또는 embedding 고아행: {duplicates}")

        krx = {row[0]: int(row[1]) for row in db.query(
            "SELECT i.index_code,COUNT(c.corp_code) FROM krx_indices i "
            "LEFT JOIN krx_index_constituents c ON c.index_code=i.index_code AND c.is_current=1 "
            "GROUP BY i.index_code"
        )}
        if krx != expected_krx:
            problems.append(f"KRX 구성 불일치: {krx} != {expected_krx}")
        if krx.get("KRX_BANK") != 5:
            problems.append(f"KRX_BANK 구성 종목 수가 5가 아닙니다: {krx.get('KRX_BANK')}")

        fk_problems = db.query("SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
                               "WHERE CONSTRAINT_SCHEMA=DATABASE()")
        if not fk_problems:
            problems.append("외래키 메타데이터 조회 실패")

        if problems:
            print("FAIL: 최종 검증 실패 — current 승격 안 함")
            for problem in problems:
                print("  -", problem)
            return 1

        print(
            f"PASS: {len(expected_corps)}개사·XML·청크·임베딩·KRX "
            "로컬 원천 일치 검증 완료"
        )
        if not args.promote:
            print("검증 전용 실행: current 승격은 하지 않았습니다.")
            return 0

        db.query(
            "START TRANSACTION;"
            "UPDATE data_versions SET validated=1,is_current=(data_version=" + lit(args.data_version) + ");"
            "UPDATE embedding_metadata SET is_current=(index_version=" + lit(index_version) + ");"
            "COMMIT;"
        )
        current = db.query(
            "SELECT data_version,validated,is_current FROM data_versions WHERE is_current=1"
        )
        current_index = db.query(
            "SELECT index_version,is_current FROM embedding_metadata WHERE is_current=1"
        )
        if current != [[args.data_version, "1", "1"]] or current_index != [[index_version, "1"]]:
            raise RuntimeError(f"승격 후 current 상태가 잘못됐습니다: {current} / {current_index}")
        print(f"PROMOTED: {args.data_version}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
