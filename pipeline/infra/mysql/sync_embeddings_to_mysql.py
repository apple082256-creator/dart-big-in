"""BGE-M3 청크 임베딩 → MySQL chunk_embeddings 동기화.

- 선행: migrations/002_add_chunk_embeddings.sql 적용, sync_sqlite_to_mysql.py로 chunk_metadata 적재 완료.
- MySQL의 현재 데이터 버전(data_versions.is_current=1)이 기록한 BGE SQLite sha256과 지금 파일이 같을 때만 실행한다
  (chunk_metadata와 벡터가 서로 다른 버전이 되는 것을 막기 위함).
- 운영 SQLite를 스냅샷으로 복사해 읽고, 일부 벡터를 FAISS에서 직접 꺼내 바이트 단위로 대조한다.
- vector_id 기준 upsert(재실행해도 중복 없음). 적재 후 행 수와 CRC32 체크섬으로 원본과 대조한다.

실행 (프로젝트 루트):
    .venv/bin/python infra/mysql/sync_embeddings_to_mysql.py --dry-run
    .venv/bin/python infra/mysql/sync_embeddings_to_mysql.py
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import tempfile
import zlib
from contextlib import closing
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_sqlite_to_mysql import MySQL, lit, sha256, snapshot  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BGE_DB = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
BGE_FAISS = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.faiss"
BATCH = 100
SHRINK_LIMIT = 0.01
FAISS_SAMPLE = 200


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--data-version",
        help="current 대신 지정한 대기 버전의 SQLite 해시/index_version을 사용한다.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-shrink", action="store_true")
    args = parser.parse_args()

    db = MySQL()
    work = Path(tempfile.mkdtemp(prefix="dart_emb_", dir=db.temp_dir))
    try:
        where = (f"data_version = {lit(args.data_version)}" if args.data_version
                 else "is_current = 1")
        current = db.query("SELECT data_version, index_version, vector_sqlite_sha256 FROM data_versions "
                           f"WHERE {where}")
        if len(current) != 1:
            raise RuntimeError("MySQL에 선택한 데이터 버전이 정확히 1개 있어야 합니다.")
        version, index_version, expected_sha = current[0]
        print(f"[1/5] 동기화 대상 데이터 버전: {version}")
        actual_sha = sha256(BGE_DB)
        if actual_sha != expected_sha:
            raise RuntimeError("BGE SQLite가 MySQL 현재 버전과 다릅니다. 먼저 sync_sqlite_to_mysql.py로 "
                               "chunk_metadata를 같은 데이터로 동기화하세요.")

        bge_snapshot = snapshot(BGE_DB, work / "bge.snapshot.db")
        if sha256(BGE_DB) != actual_sha:
            raise RuntimeError("스냅샷 중 BGE SQLite가 바뀌었습니다. 다른 작업이 끝난 뒤 다시 실행하세요.")
        with closing(sqlite3.connect(bge_snapshot)) as bge:
            rows = bge.execute(
                "SELECT vector_id, chunk_id, embedding, embedding_model, model_revision, embedding_dimension "
                "FROM filing_chunks ORDER BY vector_id"
            ).fetchall()
            searchable = {r[0] for r in bge.execute("SELECT vector_id FROM filing_chunks WHERE is_searchable = 1")}

        print(f"[2/5] 원천 검증 ({len(rows):,}개)")
        for vector_id, _, blob, _, _, dimension in rows:
            if len(blob) != dimension * 4:
                raise RuntimeError(f"vector_id={vector_id}: 벡터 길이 {len(blob)} != {dimension * 4}")
        matrix = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(matrix).all() or not np.allclose(norms, 1.0, atol=2e-3):
            raise RuntimeError("벡터에 NaN이 있거나 정규화되지 않은 벡터가 있습니다.")

        import faiss
        index = faiss.read_index(str(BGE_FAISS))
        if int(index.ntotal) != len(searchable):
            raise RuntimeError(f"FAISS ntotal({index.ntotal}) != SQLite 검색 가능 행({len(searchable)})")
        blob_by_id = {r[0]: r[2] for r in rows}
        sample = random.Random(0).sample(sorted(searchable), min(FAISS_SAMPLE, len(searchable)))
        for vector_id in sample:
            if index.reconstruct(int(vector_id)).astype(np.float32).tobytes() != blob_by_id[vector_id]:
                raise RuntimeError(f"vector_id={vector_id}: SQLite 벡터와 FAISS 벡터가 다릅니다.")
        print(f"  벡터 길이·정규화 OK, FAISS 표본 {len(sample)}개 바이트 일치")

        before = int(db.scalar("SELECT COUNT(*) FROM chunk_embeddings WHERE is_active = 1") or 0)
        print(f"[3/5] 축소 감지: 원천 {len(rows):,} / MySQL 활성 {before:,}")
        if before and len(rows) < before * (1 - SHRINK_LIMIT) and not args.allow_shrink:
            raise RuntimeError("활성 벡터가 1% 넘게 줄어듭니다. 의도한 변경이면 --allow-shrink로 실행하세요.")
        missing_meta = int(db.scalar(
            "SELECT COUNT(*) FROM chunk_metadata WHERE is_active = 1") or 0)
        if missing_meta != len(rows):
            raise RuntimeError(f"chunk_metadata 활성 {missing_meta}행과 벡터 {len(rows)}개 수가 다릅니다.")

        print("[4/5] SQL 생성" + (" (dry-run: 적재하지 않음)" if args.dry_run else " 및 적재"))
        columns = "vector_id, chunk_id, embedding, embedding_model, model_revision, embedding_dimension, " \
                  "index_version, data_version, is_active"
        update = ", ".join(f"{c}=new.{c}" for c in (
            "chunk_id", "embedding", "embedding_model", "model_revision", "embedding_dimension",
            "index_version", "data_version", "is_active"))
        body = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
        for offset in range(0, len(rows), BATCH):
            values = ",\n".join(
                f"({vid}, {lit(cid)}, X'{blob.hex()}', {lit(model)}, {lit(rev)}, {dim}, "
                f"{lit(index_version)}, {lit(version)}, 1)"
                for vid, cid, blob, model, rev, dim in rows[offset:offset + BATCH]
            )
            body.append(f"INSERT INTO chunk_embeddings ({columns}) VALUES\n{values}\n"
                        f"AS new ON DUPLICATE KEY UPDATE {update};")
        body.append(f"UPDATE chunk_embeddings SET is_active = 0 WHERE data_version <> {lit(version)} AND is_active = 1;")
        body.append("COMMIT;")
        sql_path = work / "chunk_embeddings.sql"
        sql_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"  SQL {sql_path.stat().st_size / 1e6:,.1f} MB")
        if args.dry_run:
            print("dry-run 완료: 적재하지 않았습니다.")
            return 0

        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            db.run_file(sql_path)
            status, error = "success", None
        except RuntimeError as exc:
            status, error = "failed", str(exc)[:2000]
        after = int(db.scalar("SELECT COUNT(*) FROM chunk_embeddings") or 0)
        db.query(
            "INSERT INTO sync_runs (data_version, table_name, started_at, finished_at, status, rows_source, "
            f"rows_inserted, error_message) VALUES ({lit(version)}, 'chunk_embeddings', {lit(started)}, NOW(), "
            f"{lit(status)}, {len(rows)}, {max(after - before, 0) if status == 'success' else 0}, {lit(error)})"
        )
        if status != "success":
            raise RuntimeError(f"chunk_embeddings 적재 실패: {error}")

        print("[5/5] 대조 검증")
        count, crc_sum, id_sum = db.query(
            "SELECT COUNT(*), SUM(CRC32(embedding)), SUM(vector_id) FROM chunk_embeddings WHERE is_active = 1")[0]
        expected = (len(rows), sum(zlib.crc32(r[2]) for r in rows), sum(r[0] for r in rows))
        actual = (int(count), int(crc_sum), int(id_sum))
        joined = int(db.scalar("SELECT COUNT(*) FROM chunk_embeddings e JOIN chunk_metadata m USING (chunk_id) "
                               "WHERE e.is_active = 1 AND m.is_active = 1 AND e.vector_id = m.vector_id") or 0)
        if actual != expected or joined != len(rows):
            print(f"FAIL: MySQL {actual} / 원천 {expected} / chunk_metadata 일치 {joined}")
            return 1
        print(f"PASS: 벡터 {len(rows):,}개 적재, 바이트 체크섬·vector_id·chunk_metadata 연결 일치 (data_version={version})")
        return 0
    finally:
        db.close()
        for path in sorted(work.glob("*"), reverse=True):
            path.unlink(missing_ok=True)
        work.rmdir()


if __name__ == "__main__":
    sys.exit(main())
