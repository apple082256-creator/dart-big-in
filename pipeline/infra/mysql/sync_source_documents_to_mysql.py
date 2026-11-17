"""Tag-complete DART XML and exact chunk-to-document mapping -> MySQL.

The source XML is stored as the original bytes.  No decoding, newline conversion,
or markup cleanup is performed.  Re-running the command is idempotent.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_sqlite_to_mysql import MySQL, lit, sha256  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BGE_DB = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
MAPPING_BATCH = 1000
ENCODING_RE = re.compile(br"<\?xml[^>]*encoding=[\"']([^\"']+)[\"']", re.I)


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    rcept_no: str
    sequence: int
    source_path: str
    path: Path
    encoding: str | None
    byte_size: int
    digest: str


def document_id(rcept_no: str, source_path: str) -> str:
    return hashlib.sha256(f"{rcept_no}\0{source_path}".encode("utf-8")).hexdigest()


def source_manifest() -> tuple[list[SourceDocument], list[tuple[str, str]]]:
    with sqlite3.connect(f"file:{BGE_DB}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT chunk_id, rcept_no, source_file FROM filing_chunks ORDER BY vector_id"
        ).fetchall()

    by_receipt: dict[str, set[str]] = defaultdict(set)
    for _, rcept_no, source_path in rows:
        if not rcept_no or not source_path:
            raise RuntimeError("chunk에 rcept_no 또는 source_file이 없습니다.")
        by_receipt[str(rcept_no)].add(str(source_path))

    documents: list[SourceDocument] = []
    ids: dict[tuple[str, str], str] = {}
    root = PROJECT_ROOT.resolve()
    for rcept_no in sorted(by_receipt):
        for sequence, source_path in enumerate(sorted(by_receipt[rcept_no]), 1):
            path = (PROJECT_ROOT / source_path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"프로젝트 밖 source_file 경로: {source_path}") from exc
            if not path.is_file() or path.suffix.lower() != ".xml":
                raise RuntimeError(f"원본 XML이 없거나 확장자가 잘못됐습니다: {source_path}")
            if rcept_no not in path.parts:
                raise RuntimeError(f"rcept_no와 source_file 경로가 다릅니다: {rcept_no} / {source_path}")
            raw = path.read_bytes()
            match = ENCODING_RE.search(raw[:1024])
            encoding = match.group(1).decode("ascii", "replace")[:40] if match else None
            doc_id = document_id(rcept_no, source_path)
            ids[(rcept_no, source_path)] = doc_id
            documents.append(SourceDocument(
                document_id=doc_id,
                rcept_no=rcept_no,
                sequence=sequence,
                source_path=source_path,
                path=path,
                encoding=encoding,
                byte_size=len(raw),
                digest=hashlib.sha256(raw).hexdigest(),
            ))

    mappings = [(chunk_id, ids[(str(rcept_no), str(source_path))])
                for chunk_id, rcept_no, source_path in rows]
    if len({d.document_id for d in documents}) != len(documents):
        raise RuntimeError("document_id 충돌이 있습니다.")
    if len({chunk_id for chunk_id, _ in mappings}) != len(mappings):
        raise RuntimeError("chunk_id 중복이 있습니다.")
    return documents, mappings


def require_schema(db: MySQL) -> None:
    for table in ("filing_source_documents", "chunk_source_documents"):
        if db.scalar(f"SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                     f"AND TABLE_NAME={lit(table)}") != "1":
            raise RuntimeError(f"{table} 테이블이 없습니다. migration 004를 먼저 적용하세요.")


def write_document(db: MySQL, work: Path, doc: SourceDocument, version: str) -> None:
    raw = doc.path.read_bytes()
    if len(raw) != doc.byte_size or hashlib.sha256(raw).hexdigest() != doc.digest:
        raise RuntimeError(f"적재 중 원본 파일이 변경됐습니다: {doc.source_path}")
    sql = f"""SET NAMES utf8mb4;
START TRANSACTION;
INSERT INTO filing_source_documents (
  document_id, rcept_no, document_sequence, source_path, file_name,
  mime_type, detected_encoding, byte_size, sha256, raw_xml, data_version, is_active
) VALUES (
  {lit(doc.document_id)}, {lit(doc.rcept_no)}, {doc.sequence}, {lit(doc.source_path)},
  {lit(doc.path.name)}, 'application/xml', {lit(doc.encoding)}, {doc.byte_size},
  {lit(doc.digest)}, X'{raw.hex()}', {lit(version)}, 1
) AS new ON DUPLICATE KEY UPDATE
  rcept_no=new.rcept_no,
  document_sequence=new.document_sequence,
  source_path=new.source_path,
  file_name=new.file_name,
  mime_type=new.mime_type,
  detected_encoding=new.detected_encoding,
  byte_size=new.byte_size,
  sha256=new.sha256,
  raw_xml=new.raw_xml,
  data_version=new.data_version,
  is_active=1;
COMMIT;
"""
    path = work / "source_document.sql"
    path.write_text(sql, encoding="ascii")
    db.run_file(path)


def write_mappings(db: MySQL, work: Path, mappings: list[tuple[str, str]], version: str) -> None:
    statements = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
    for offset in range(0, len(mappings), MAPPING_BATCH):
        values = ",\n".join(
            f"({lit(chunk_id)}, {lit(doc_id)}, 'source_path_exact', {lit(version)})"
            for chunk_id, doc_id in mappings[offset:offset + MAPPING_BATCH]
        )
        statements.append(
            "INSERT INTO chunk_source_documents "
            "(chunk_id, document_id, mapping_method, data_version) VALUES\n" + values + "\n"
            "AS new ON DUPLICATE KEY UPDATE document_id=new.document_id, "
            "mapping_method=new.mapping_method, data_version=new.data_version;"
        )
    statements.append("COMMIT;")
    path = work / "chunk_source_documents.sql"
    path.write_text("\n".join(statements) + "\n", encoding="utf-8")
    db.run_file(path)


def validate(db: MySQL, documents: list[SourceDocument], mappings: list[tuple[str, str]]) -> None:
    actual_documents = {
        row[0]: (row[1].lower(), int(row[2]), row[3], row[4])
        for row in db.query(
            "SELECT document_id,SHA2(raw_xml,256),OCTET_LENGTH(raw_xml),rcept_no,source_path "
            "FROM filing_source_documents WHERE is_active=1"
        )
    }
    expected_documents = {
        doc.document_id: (doc.digest, doc.byte_size, doc.rcept_no, doc.source_path)
        for doc in documents
    }
    if actual_documents != expected_documents:
        missing = expected_documents.keys() - actual_documents.keys()
        extra = actual_documents.keys() - expected_documents.keys()
        raise RuntimeError(f"XML 원본 검증 실패: missing={len(missing)}, extra={len(extra)}")

    count, exact = db.query(
        "SELECT COUNT(*),SUM(c.rcept_no=d.rcept_no AND c.source_file=d.source_path) "
        "FROM chunk_source_documents m "
        "JOIN chunk_metadata c ON c.chunk_id=m.chunk_id AND c.is_active=1 "
        "JOIN filing_source_documents d ON d.document_id=m.document_id AND d.is_active=1"
    )[0]
    if int(count) != len(mappings) or int(exact or 0) != len(mappings):
        raise RuntimeError(f"chunk→XML 매핑 검증 실패: joined={count}, exact={exact}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_hash = sha256(BGE_DB)
    documents, mappings = source_manifest()
    print(f"원본 XML {len(documents):,}개 / 매핑 {len(mappings):,}개 / "
          f"{sum(d.byte_size for d in documents):,} bytes")
    print(f"최대 XML {max(d.byte_size for d in documents):,} bytes")
    if args.dry_run:
        print("dry-run 완료: MySQL을 변경하지 않았습니다.")
        return 0

    db = MySQL()
    work = Path(tempfile.mkdtemp(prefix="dart_xml_", dir=db.temp_dir))
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        require_schema(db)
        if db.scalar(f"SELECT COUNT(*) FROM data_versions WHERE data_version={lit(args.data_version)}") != "1":
            raise RuntimeError("선택한 data_version이 MySQL에 없습니다.")
        packet = int(db.scalar("SELECT @@max_allowed_packet") or 0)
        largest_sql_payload = max(d.byte_size for d in documents) * 2 + 1_000_000
        if packet <= largest_sql_payload:
            raise RuntimeError(f"max_allowed_packet={packet:,}가 최대 XML hex SQL에 부족합니다.")

        for index, doc in enumerate(documents, 1):
            write_document(db, work, doc, args.data_version)
            if index % 25 == 0 or index == len(documents):
                print(f"  XML {index:,}/{len(documents):,}")
        write_mappings(db, work, mappings, args.data_version)
        if sha256(BGE_DB) != source_hash:
            raise RuntimeError("작업 중 BGE SQLite가 변경됐습니다.")
        validate(db, documents, mappings)
        db.query(
            "INSERT INTO sync_runs (data_version,table_name,started_at,finished_at,status,rows_source,rows_inserted) "
            f"VALUES ({lit(args.data_version)},'filing_source_documents',{lit(started)},NOW(),'success',"
            f"{len(documents)},{len(documents)})"
        )
        db.query(
            "INSERT INTO sync_runs (data_version,table_name,started_at,finished_at,status,rows_source,rows_inserted) "
            f"VALUES ({lit(args.data_version)},'chunk_source_documents',{lit(started)},NOW(),'success',"
            f"{len(mappings)},{len(mappings)})"
        )
        print("PASS: 원본 XML 바이트/SHA-256 및 chunk→문서 연결 일치")
        return 0
    finally:
        db.close()
        for path in work.glob("*"):
            path.unlink(missing_ok=True)
        work.rmdir()


if __name__ == "__main__":
    raise SystemExit(main())
