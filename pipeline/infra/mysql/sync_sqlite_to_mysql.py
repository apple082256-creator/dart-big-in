"""SQLite(Financial DB, BGE 메타데이터) → 중앙 MySQL 동기화 (Phase 1).

설계: docs/handoff/MYSQL_CENTRALIZATION_PLAN.md
- 운영 SQLite를 읽기 전용으로 열어 backup API로 스냅샷을 만든 뒤 스냅샷에서만 읽는다.
- 자연키 기준 INSERT ... ON DUPLICATE KEY UPDATE 이므로 같은 스냅샷을 다시 넣어도 결과가 같다.
- 테이블마다 하나의 트랜잭션. 실패하면 해당 테이블은 이전 상태 그대로다.
- 이번 버전에 없는 기존 행은 삭제하지 않고 is_active=0 으로 표시한다.
  활성 행이 1% 넘게 줄어들면 --allow-shrink 없이는 중단한다.
- embedding BLOB과 FAISS는 MySQL에 넣지 않는다.
- Windows MySQL 클라이언트(mysql.exe)를 사용하므로 추가 패키지가 필요 없다.
  접속 비밀번호는 infra/mysql/.env 에서 읽어 임시 설정 파일(권한 600, 실행 후 삭제)로만 전달한다.

실행 (프로젝트 루트에서):
    .venv/bin/python infra/mysql/sync_sqlite_to_mysql.py --data-version 2026-10-06-dart5-v1
    .venv/bin/python infra/mysql/sync_sqlite_to_mysql.py --dry-run   # SQL 생성·건수 확인만
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import zlib
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FINANCIAL_DB = PROJECT_ROOT / "db" / "financial" / "financial.db"
BGE_DB = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
BGE_FAISS = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.faiss"
CORE_MAPPING_CSV = PROJECT_ROOT / "data" / "mapping" / "account_mapping_core.csv"
MYSQL_EXE = Path("/mnt/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe")
BATCH = {"chunk_metadata": 200}
DEFAULT_BATCH = 1000
SHRINK_LIMIT = 0.01


# ---------------------------------------------------------------------------
# MySQL 클라이언트 실행
# ---------------------------------------------------------------------------
def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def _windows_temp() -> Path:
    raw = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], cwd="/mnt/c", stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, errors="replace").stdout.strip()
    return Path(subprocess.run(["wslpath", raw], capture_output=True, text=True, check=True).stdout.strip())


def _winpath(path: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


class MySQL:
    def __init__(self) -> None:
        env = _read_env()
        self.database = env["MYSQL_DATABASE"]
        self.temp_dir = _windows_temp()
        fd, name = tempfile.mkstemp(prefix="dart_etl_", suffix=".cnf", dir=self.temp_dir)
        self.cnf = Path(name)
        os.chmod(self.cnf, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                "[client]\nuser=dart_etl\n"
                f"password={env['MYSQL_ETL_PASSWORD']}\n"
                f"host=127.0.0.1\nport={env.get('MYSQL_PORT', '3306')}\n"
                "default-character-set=utf8mb4\n"
            )

    def close(self) -> None:
        self.cnf.unlink(missing_ok=True)

    def _base(self) -> list[str]:
        return [str(MYSQL_EXE), f"--defaults-extra-file={_winpath(self.cnf)}", self.database]

    def query(self, sql: str) -> list[list[str]]:
        result = subprocess.run(self._base() + ["-N", "-B", "-e", sql], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return [line.split("\t") for line in result.stdout.splitlines() if line]

    def scalar(self, sql: str) -> str | None:
        rows = self.query(sql)
        return rows[0][0] if rows else None

    def run_file(self, path: Path) -> None:
        source = _winpath(path).replace("\\", "/")
        result = subprocess.run(self._base() + ["-e", f"source {source}"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())


# ---------------------------------------------------------------------------
# SQL 생성
# ---------------------------------------------------------------------------
def lit(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace("'", "''").replace("\0", "\\0")
    return f"'{text}'"


def upsert_sql(table: str, columns: list[str], rows: list[tuple], update: list[str]) -> list[str]:
    statements: list[str] = []
    size = BATCH.get(table, DEFAULT_BATCH)
    assignments = ", ".join(f"{column}=new.{column}" for column in update)
    for offset in range(0, len(rows), size):
        values = ",\n".join("(" + ", ".join(lit(v) for v in row) + ")" for row in rows[offset:offset + size])
        statements.append(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values}\nAS new ON DUPLICATE KEY UPDATE {assignments};"
        )
    return statements


# ---------------------------------------------------------------------------
# 스냅샷과 원천 읽기
# ---------------------------------------------------------------------------
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source: Path, target: Path) -> Path:
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(target)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"스냅샷 무결성 실패: {source}")
    return target


def rows_of(connection: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    return connection.execute(sql).fetchall()


def build_tables(fin: sqlite3.Connection, bge: sqlite3.Connection, version: str, index_version: str) -> dict[str, dict]:
    tables: dict[str, dict] = {}

    tables["periods"] = {
        "columns": ["year", "report_type", "report_code", "report_name"],
        "rows": [tuple(r) for r in rows_of(fin, "SELECT year, report_type, report_code, report_name FROM periods")],
        "update": ["report_code", "report_name"], "versioned": False,
    }
    tables["companies"] = {
        "columns": ["corp_code", "corp_name", "stock_code", "is_operational"],
        "rows": [(r["corp_code"], r["company_name"], r["stock_code"] or None, 1)
                 for r in rows_of(fin, "SELECT * FROM companies")],
        "update": ["corp_name", "stock_code", "is_operational"], "versioned": False,
    }
    index_columns = ["index_code", "official_index_code", "index_name", "display_name",
                     "source_as_of", "source_url", "is_active"]
    tables["krx_indices"] = {
        "columns": index_columns,
        "rows": [tuple(r[c] for c in index_columns) for r in rows_of(fin, "SELECT * FROM krx_indices")],
        "update": index_columns[1:], "versioned": False,
    }
    constituent_columns = ["index_code", "corp_code", "effective_from", "effective_to",
                           "source_as_of", "weight", "is_current"]
    tables["krx_index_constituents"] = {
        "columns": constituent_columns,
        "rows": [tuple(r[c] for c in constituent_columns)
                 for r in rows_of(fin, "SELECT * FROM krx_index_constituents")],
        "update": ["effective_to", "source_as_of", "weight", "is_current"], "versioned": False,
    }

    # filings는 실제로 저장한 XML/BGE 문서의 접수번호를 우선한다. DART 재무
    # API가 가리키는 최신 정정공시의 원문이 아직 제공되지 않는 경우에는 직전
    # 다운로드 가능 정정공시를 사용할 수 있으며, 두 번호의 차이는 coverage에
    # 별도로 남긴다. 슬롯당 filings 행은 하나라는 기존 스키마는 유지한다.
    chunk_slots: dict[tuple, set[str]] = defaultdict(set)
    for r in rows_of(bge, "SELECT DISTINCT corp_code, year, report_type, rcept_no FROM filing_chunks"):
        chunk_slots[(r["corp_code"], r["year"], r["report_type"])].add(r["rcept_no"])
    chunk_conflicts = {key: value for key, value in chunk_slots.items() if len(value) > 1}
    if chunk_conflicts:
        raise RuntimeError(f"한 슬롯에 XML/BGE 접수번호가 여러 개입니다: {list(chunk_conflicts.items())[:5]}")

    financial_slots: dict[tuple, set[str]] = defaultdict(set)
    for r in rows_of(fin, """SELECT DISTINCT f.corp_code, p.year, p.report_type, f.rcept_no
                              FROM financial_facts f JOIN periods p USING(period_id) WHERE f.rcept_no IS NOT NULL"""):
        financial_slots[(r["corp_code"], r["year"], r["report_type"])].add(r["rcept_no"])
    financial_conflicts = {key: value for key, value in financial_slots.items() if len(value) > 1}
    if financial_conflicts:
        raise RuntimeError(f"한 슬롯에 Financial 접수번호가 여러 개입니다: {list(financial_conflicts.items())[:5]}")

    filing_rows = []
    all_slots = set(chunk_slots) | set(financial_slots)
    for corp_code, year, report_type in sorted(all_slots):
        key = (corp_code, year, report_type)
        receipts = chunk_slots.get(key) or financial_slots[key]
        rcept_no = next(iter(receipts))
        filing_rows.append((rcept_no, corp_code, year, report_type,
                            date(int(rcept_no[:4]), int(rcept_no[4:6]), int(rcept_no[6:8])).isoformat(),
                            version, 1))
    tables["filings"] = {
        "columns": ["rcept_no", "corp_code", "year", "report_type", "rcept_dt", "data_version", "is_active"],
        "rows": filing_rows, "update": ["corp_code", "year", "report_type", "rcept_dt", "data_version", "is_active"],
        "versioned": True,
    }

    with CORE_MAPPING_CSV.open(encoding="utf-8-sig", newline="") as handle:
        core = list(csv.DictReader(handle))
    tables["standard_account_mapping"] = {
        "columns": ["standard_account_nm", "original_account_nm", "account_id", "sj_div", "fs_div",
                    "mapping_status", "mapping_confidence", "review_required", "review_note",
                    "corp_names", "report_types", "data_version"],
        "rows": [(r["standard_account_nm"], r["original_account_nm"], r["account_id"] or "", r["sj_div"], r["fs_div"],
                  r["mapping_status"], r["mapping_confidence"] or None, r["review_required"].strip().lower() == "true",
                  r["review_note"] or None, r["corp_names"] or None, r["report_types"] or None, version)
                 for r in core],
        "update": ["mapping_status", "mapping_confidence", "review_required", "review_note",
                   "corp_names", "report_types", "data_version"], "versioned": False,
    }

    mapping_columns = ["account_key", "sj_div", "account_id", "standard_account_name", "source_account_names",
                       "company_count", "observation_count", "mapping_status"]
    tables["account_mapping"] = {
        "columns": mapping_columns + ["data_version"],
        "rows": [tuple(r[c] for c in mapping_columns) + (version,) for r in rows_of(fin, "SELECT * FROM account_mapping")],
        "update": mapping_columns[2:] + ["data_version"], "versioned": False,
    }

    profile_columns = ["corp_code", "industry_profile", "preferred_fs_div", "fallback_fs_div",
                       "comparison_scope", "capex_applicable", "valid_from", "valid_to"]
    tables["company_financial_profiles"] = {
        "columns": profile_columns,
        "rows": [tuple(r[c] for c in profile_columns) for r in rows_of(fin, "SELECT * FROM company_financial_profiles")],
        "update": profile_columns[1:], "versioned": False,
    }
    collection_columns = ["corp_code", "year", "report_type", "requested_fs_div", "selected_fs_div",
                          "cfs_status", "ofs_status", "missing_reason", "rcept_no"]
    tables["financial_collection_status"] = {
        "columns": collection_columns,
        "rows": [tuple(r[c] for c in collection_columns) for r in rows_of(fin, "SELECT * FROM financial_collection_status")],
        "update": collection_columns[3:], "versioned": False,
    }
    definition_columns = ["metric_code", "metric_name", "industry_profile", "statement_type", "metric_type",
                          "period_nature", "unit_type", "description", "is_core", "is_active"]
    tables["financial_metric_definitions"] = {
        "columns": definition_columns,
        "rows": [tuple(r[c] for c in definition_columns) for r in rows_of(fin, "SELECT * FROM financial_metric_definitions")],
        "update": definition_columns[1:], "versioned": False,
    }
    metric_mapping_columns = ["metric_code", "industry_profile", "account_id", "normalized_account_name",
                              "sj_div", "fs_div", "applies_to_corp_code", "mapping_status",
                              "mapping_confidence", "tag_class", "priority", "valid_from", "valid_to", "review_note"]
    tables["financial_metric_mappings"] = {
        "columns": metric_mapping_columns,
        "rows": [tuple(r[c] for c in metric_mapping_columns) for r in rows_of(fin, "SELECT * FROM financial_metric_mappings")],
        "update": metric_mapping_columns[7:], "versioned": False,
    }
    metric_value_columns = ["corp_code", "year", "report_type", "metric_code", "value", "unit", "value_type",
                            "fs_div", "scope", "source_kind", "source_type", "source_rcept_no",
                            "source_document_id", "source_chunk_id", "source_account_id", "source_account_name",
                            "source_fact_key", "calculation_method", "source_values", "review_status"]
    tables["financial_metric_values"] = {
        "columns": metric_value_columns,
        "rows": [tuple(r[c] for c in metric_value_columns) for r in rows_of(fin, "SELECT * FROM financial_metric_values")],
        "update": [c for c in metric_value_columns if c not in
                   ("corp_code", "year", "report_type", "metric_code", "value_type", "fs_div", "scope", "source_kind")],
        "versioned": False,
    }

    fact_columns = ["corp_code", "year", "report_type", "rcept_no", "fs_div", "sj_div", "account_key", "account_id",
                    "original_account_nm", "standard_account_nm", "ord", "ord_key", "period_type", "value_type",
                    "period_start", "period_end", "currency", "source_value_field", "raw_value", "normalized_value",
                    "calculated_value", "calculation_method", "source_values", "is_calculated", "data_version", "is_active"]
    facts = []
    for r in rows_of(fin, "SELECT f.*, p.year, p.report_type FROM financial_facts f JOIN periods p USING(period_id)"):
        record = dict(r)
        record.update(ord_key=-1 if r["ord"] is None else r["ord"], data_version=version, is_active=1)
        facts.append(tuple(record[c] for c in fact_columns))
    tables["financial_facts"] = {
        "columns": fact_columns, "rows": facts, "versioned": True,
        "update": [c for c in fact_columns if c not in
                   ("corp_code", "year", "report_type", "fs_div", "sj_div", "account_key", "ord_key", "value_type", "is_calculated")],
    }

    capex_columns = ["corp_code", "year", "report_type", "fs_div", "sj_div", "rcept_no", "account_id",
                     "original_account_nm", "standard_account_nm", "period_type", "value_type", "period_start",
                     "period_end", "currency", "raw_value", "normalized_value", "calculated_value",
                     "cash_outflow_amount", "sign_normalized", "is_calculated", "is_derived", "value_source",
                     "capex_method", "calculation_method", "source_account_ids", "source_account_names",
                     "source_values", "source_account_count", "direct_account_found", "review_required", "note",
                     "data_version", "is_active"]
    capex = []
    for r in rows_of(fin, "SELECT cf.*, p.year, p.report_type FROM capex_facts cf JOIN periods p USING(period_id)"):
        record = dict(r)
        record.update(data_version=version, is_active=1)
        capex.append(tuple(record[c] for c in capex_columns))
    tables["capex_facts"] = {
        "columns": capex_columns, "rows": capex, "versioned": True,
        "update": [c for c in capex_columns if c not in ("corp_code", "year", "report_type", "fs_div", "value_type")],
    }

    chunk_columns = ["chunk_id", "vector_id", "corp_code", "corp_name", "year", "report_type", "rcept_no",
                     "section_name", "subsection_name", "source_file", "chunk_text", "original_text", "content_hash",
                     "chunk_version", "token_count", "content_type", "search_priority", "selection_reason",
                     "is_searchable", "embedding_model", "model_revision", "embedding_dimension", "embedded_at"]
    chunks = [tuple(r[c] for c in chunk_columns) + (index_version, version, 1)
              for r in rows_of(bge, f"SELECT {', '.join(chunk_columns)} FROM filing_chunks ORDER BY vector_id")]
    all_chunk_columns = chunk_columns + ["index_version", "data_version", "is_active"]
    tables["chunk_metadata"] = {
        "columns": all_chunk_columns, "rows": chunks, "versioned": True,
        "update": [c for c in all_chunk_columns if c not in ("chunk_id", "vector_id")],
    }

    # coverage: 기업 × 기간 슬롯
    fin_slots = defaultdict(lambda: {"fs": set(), "rcept": set()})
    for r in rows_of(fin, """SELECT f.corp_code, p.year, p.report_type, f.fs_div, f.rcept_no
                              FROM financial_facts f JOIN periods p USING(period_id)
                              GROUP BY 1, 2, 3, 4, 5"""):
        slot = fin_slots[(r["corp_code"], r["year"], r["report_type"])]
        slot["fs"].add(r["fs_div"])
        if r["rcept_no"]:
            slot["rcept"].add(r["rcept_no"])
    chunk_slots = {(r["corp_code"], r["year"], r["report_type"]): (r["rcept_no"], r["n"], r["searchable"])
                   for r in rows_of(bge, """SELECT corp_code, year, report_type, MIN(rcept_no) rcept_no,
                                                    COUNT(*) n, SUM(is_searchable) searchable
                                             FROM filing_chunks GROUP BY 1, 2, 3""")}
    capex_slots = defaultdict(set)
    for r in rows_of(fin, """SELECT cf.corp_code, p.year, p.report_type, cf.value_source
                              FROM capex_facts cf JOIN periods p USING(period_id) WHERE cf.is_calculated = 0"""):
        capex_slots[(r["corp_code"], r["year"], r["report_type"])].add(r["value_source"])
    coverage = []
    period_keys = [(r[0], r[1]) for r in tables["periods"]["rows"]]
    for corp_code, *_ in tables["companies"]["rows"]:
        for year, report_type in period_keys:
            key = (corp_code, year, report_type)
            fin_slot = fin_slots.get(key)
            chunk = chunk_slots.get(key)
            financial_exists = fin_slot is not None
            financial_rcept = next(iter(fin_slot["rcept"]), None) if fin_slot else None
            sources = capex_slots.get(key, set())
            capex_status = ("direct" if "direct" in sources else "derived" if "derived" in sources
                            else "unresolved" if financial_exists else None)
            reasons = []
            if not financial_exists:
                reasons.append("financial missing")
            if not chunk:
                reasons.append("filing chunks missing")
            elif not chunk[2]:
                reasons.append("no searchable embedding")
            if chunk and financial_rcept and chunk[0] != financial_rcept:
                reasons.append("rcept_no mismatch")
            coverage.append((
                corp_code, year, report_type, chunk[0] if chunk else None, financial_rcept, financial_exists,
                ",".join(sorted(fin_slot["fs"])) if fin_slot else None, bool(chunk), bool(chunk),
                bool(chunk and chunk[2]), capex_status, "PASS" if not reasons else "MISSING_OR_REVIEW",
                "; ".join(reasons) or None, version,
            ))
    coverage_columns = ["corp_code", "year", "report_type", "rcept_no", "financial_rcept_no", "financial_exists",
                        "fs_div", "filing_exists", "chunk_exists", "embedding_exists", "capex_status", "status",
                        "reason", "data_version"]
    tables["coverage"] = {"columns": coverage_columns, "rows": coverage, "versioned": False,
                          "update": coverage_columns[3:]}
    return tables


TABLE_ORDER = ["periods", "companies", "krx_indices", "krx_index_constituents", "filings", "standard_account_mapping", "account_mapping",
               "company_financial_profiles", "financial_collection_status", "financial_metric_definitions",
               "financial_metric_mappings", "financial_facts", "financial_metric_values", "capex_facts",
               "embedding_metadata", "chunk_metadata", "coverage"]
def active_count(db: MySQL, table: str, versioned: bool) -> int:
    where = " WHERE is_active = 1" if versioned else " WHERE is_current = 1" if table == "embedding_metadata" else ""
    return int(db.scalar(f"SELECT COUNT(*) FROM {table}{where}") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-version", default=f"{date.today().isoformat()}-dart5-v1")
    parser.add_argument("--description", default="기존 5개사 검증 스냅샷(Financial + BGE-M3 메타데이터)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--financial-db", type=Path, default=FINANCIAL_DB)
    parser.add_argument("--bge-db", type=Path, default=BGE_DB)
    parser.add_argument("--faiss", type=Path, default=BGE_FAISS)
    parser.add_argument("--allow-shrink", action="store_true")
    parser.add_argument(
        "--defer-promotion",
        action="store_true",
        help="적재·기본 검증만 수행하고 data_versions/embedding_metadata current 승격을 미룬다.",
    )
    args = parser.parse_args()
    version = args.data_version
    index_version = f"bge-m3-{version}"
    financial_db = args.financial_db.resolve()
    bge_db = args.bge_db.resolve()
    bge_faiss = args.faiss.resolve()

    db = MySQL()
    work = Path(tempfile.mkdtemp(prefix="dart_sync_", dir=db.temp_dir))
    try:
        print(f"[1/5] 스냅샷 생성 ({work})")
        hashes = {name: sha256(path) for name, path in
                  (("financial", financial_db), ("vector", bge_db), ("faiss", bge_faiss))}
        fin_snapshot = snapshot(financial_db, work / "financial.snapshot.db")
        bge_snapshot = snapshot(bge_db, work / "bge.snapshot.db")
        if sha256(financial_db) != hashes["financial"] or sha256(bge_db) != hashes["vector"]:
            raise RuntimeError("스냅샷 중 원본 SQLite가 바뀌었습니다. 다른 작업이 끝난 뒤 다시 실행하세요.")

        import faiss
        index = faiss.read_index(str(bge_faiss))
        with closing(sqlite3.connect(fin_snapshot)) as fin, closing(sqlite3.connect(bge_snapshot)) as bge:
            vector_meta = dict(bge.execute("SELECT key, value FROM vector_metadata").fetchall())
            searchable, max_vector_id, built_at = bge.execute(
                "SELECT SUM(is_searchable), MAX(vector_id), MAX(embedded_at) FROM filing_chunks").fetchone()
            if int(index.ntotal) != searchable:
                raise RuntimeError(f"FAISS({index.ntotal})와 SQLite 검색 가능 행({searchable}) 수가 다릅니다.")
            print("[2/5] 원천 읽기")
            tables = build_tables(fin, bge, version, index_version)
            chunk_crc = sum(zlib.crc32(r[0].encode()) for r in bge.execute("SELECT content_hash FROM filing_chunks"))
            fin_check = dict(((r[0], r[1]), (r[2], r[3], r[4])) for r in fin.execute(
                """SELECT corp_code, value_type, COUNT(*), COALESCE(SUM(normalized_value), 0),
                          COALESCE(SUM(calculated_value), 0) FROM financial_facts GROUP BY 1, 2"""))

        embed_columns = ["index_version", "model_name", "model_revision", "embedding_dimension",
                         "normalize_embeddings", "faiss_index_type", "chunk_version", "faiss_path", "faiss_sha256",
                         "sqlite_path", "sqlite_sha256", "searchable_vector_count", "faiss_ntotal", "max_vector_id",
                         "built_at", "is_current"]
        tables["embedding_metadata"] = {
            "columns": embed_columns, "versioned": False, "update": embed_columns[1:],
            "rows": [(index_version, vector_meta["embedding_model"], vector_meta["model_revision"],
                      int(vector_meta["embedding_dimension"]), vector_meta.get("normalize_embeddings") == "true",
                      vector_meta["faiss_index_type"], vector_meta["chunk_version"],
                      str(bge_faiss.relative_to(PROJECT_ROOT)), hashes["faiss"],
                      str(bge_db.relative_to(PROJECT_ROOT)), hashes["vector"], searchable, int(index.ntotal),
                      max_vector_id, built_at, 1)],
        }
        if args.defer_promotion:
            row = list(tables["embedding_metadata"]["rows"][0])
            row[-1] = 0
            tables["embedding_metadata"]["rows"] = [tuple(row)]

        print("[3/5] 축소 감지")
        for name in TABLE_ORDER:
            table = tables[name]
            before = active_count(db, name, table["versioned"])
            table["before"] = before
            if before and len(table["rows"]) < before * (1 - SHRINK_LIMIT) and not args.allow_shrink:
                raise RuntimeError(f"{name}: 활성 {before}행 → 원천 {len(table['rows'])}행. "
                                   "의도한 축소라면 --allow-shrink로 다시 실행하세요.")
            print(f"  {name:26s} 원천 {len(table['rows']):>7,}행 / MySQL 활성 {before:>7,}행")

        print("[4/5] SQL 생성" + (" (dry-run: 적재하지 않음)" if args.dry_run else " 및 적재"))
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for name in TABLE_ORDER:
            table = tables[name]
            body = ["SET NAMES utf8mb4;", "START TRANSACTION;"]
            if name == "embedding_metadata" and not args.defer_promotion:
                body.append("UPDATE embedding_metadata SET is_current = 0;")
            body += upsert_sql(name, table["columns"], table["rows"], table["update"])
            if table["versioned"]:
                body.append(f"UPDATE {name} SET is_active = 0 WHERE data_version <> {lit(version)} AND is_active = 1;")
            body.append("COMMIT;")
            path = work / f"{name}.sql"
            path.write_text("\n".join(body) + "\n", encoding="utf-8")
            if args.dry_run:
                continue
            try:
                db.run_file(path)
                status, error = "success", None
            except RuntimeError as exc:
                status, error = "failed", str(exc)[:2000]
            after = int(db.scalar(f"SELECT COUNT(*) FROM {name}") or 0)
            deactivated = (int(db.scalar(f"SELECT COUNT(*) FROM {name} WHERE is_active = 0 "
                                         f"AND data_version <> {lit(version)}") or 0) if table["versioned"] else None)
            db.query(
                "INSERT INTO sync_runs (data_version, table_name, started_at, finished_at, status, rows_source, "
                "rows_inserted, rows_deactivated, error_message) VALUES ("
                f"{lit(version)}, {lit(name)}, {lit(started)}, NOW(), {lit(status)}, {len(table['rows'])}, "
                f"{max(after - table['before'], 0) if status == 'success' else 0}, {lit(deactivated)}, {lit(error)})"
            )
            print(f"  {name:26s} {status}")
            if status != "success":
                raise RuntimeError(f"{name} 적재 실패: {error}")
        if args.dry_run:
            print("dry-run 완료: 생성한 SQL과 스냅샷은 정리합니다(적재 없음).")
            return 0

        print("[5/5] 대조 검증")
        problems = []
        for name in TABLE_ORDER:
            table = tables[name]
            actual = active_count(db, name, table["versioned"])
            if actual != len(table["rows"]):
                problems.append(f"{name}: MySQL {actual} != 원천 {len(table['rows'])}")
        for corp_code, value_type, count, normalized, calculated in db.query(
                "SELECT corp_code, value_type, COUNT(*), COALESCE(SUM(normalized_value), 0), "
                "COALESCE(SUM(calculated_value), 0) FROM financial_facts WHERE is_active = 1 GROUP BY 1, 2"):
            expected = fin_check.get((corp_code, value_type))
            if expected != (int(count), int(normalized), int(calculated)):
                problems.append(f"financial_facts 합계 불일치 {corp_code}/{value_type}: {expected} vs {(count, normalized, calculated)}")
        if int(db.scalar("SELECT SUM(CRC32(content_hash)) FROM chunk_metadata WHERE is_active = 1") or 0) != chunk_crc:
            problems.append("chunk_metadata content_hash 체크섬 불일치")
        vector_check = db.query("SELECT COUNT(*), MIN(vector_id), MAX(vector_id) FROM chunk_metadata "
                                "WHERE is_active = 1 AND is_searchable = 1")[0]
        if int(vector_check[0]) != int(index.ntotal) or int(vector_check[2]) != max_vector_id:
            problems.append(f"vector_id 범위/수 불일치: {vector_check} vs ntotal={index.ntotal}")

        validated = not problems
        recorded_validated = validated and not args.defer_promotion
        db.query(
            "INSERT INTO data_versions (data_version, description, financial_sqlite_sha256, vector_sqlite_sha256, "
            "faiss_sha256, index_version, validated, is_current) VALUES ("
            f"{lit(version)}, {lit(args.description)}, {lit(hashes['financial'])}, {lit(hashes['vector'])}, "
            f"{lit(hashes['faiss'])}, {lit(index_version)}, {int(recorded_validated)}, 0) AS new "
            "ON DUPLICATE KEY UPDATE description=new.description, financial_sqlite_sha256=new.financial_sqlite_sha256, "
            "vector_sqlite_sha256=new.vector_sqlite_sha256, faiss_sha256=new.faiss_sha256, "
            "index_version=new.index_version, validated=new.validated"
        )
        if validated and args.defer_promotion:
            print(f"PASS: data_version={version} 적재·기본 검증 완료, 최종 검증 전이라 current 승격을 보류")
            return 0
        if validated:
            db.query(f"UPDATE data_versions SET is_current = (data_version = {lit(version)})")
            print(f"PASS: data_version={version} 적재·검증 완료, 현재 버전으로 지정")
            return 0
        print("FAIL: 검증 불일치 — 현재 버전으로 지정하지 않았습니다.")
        for problem in problems:
            print("  -", problem)
        return 1
    finally:
        db.close()
        for path in sorted(work.glob("*"), reverse=True):
            path.unlink(missing_ok=True)
        work.rmdir()


if __name__ == "__main__":
    sys.exit(main())
