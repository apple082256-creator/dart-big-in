# MySQL 중앙 서버 전환 설계

작성일: 2026-10-06 (Phase 1 구현 결과 반영 개정)
상태: **Phase 1 구현·초기 적재 완료**(데이터 버전 `2026-10-06-dart5-v1`, 검증 PASS). Phase 2는 설계안.
실제 스키마는 `infra/mysql/init/01_schema.sql`이 기준이며, 4장의 DDL은 설계 당시 예시다.

## 현재 구현 상태 요약 (2026-10-06)

| 항목 | 설계안 | 실제 구현 |
|---|---|---|
| 서버 | 중앙 서버 | 데이터 담당자 PC의 **Windows MySQL Community Server 8.0**(서비스 `MySQL80`, 3306). Docker 구성(`infra/mysql/docker-compose.yml`)은 이전용 대안으로 보관 |
| DB / 문자셋 | `dart_big_in`, utf8mb4 | 동일 |
| 생성 스크립트 | — | `infra/mysql/setup_windows.sh`(DB·스키마·시드·계정), `init/01_schema.sql`, `init/02_seed_reference.sql` |
| 동기화 | sync script | `infra/mysql/sync_sqlite_to_mysql.py` — 추가 패키지 없이 Windows `mysql.exe` 사용 |
| 데이터 범위 | 21개사 예정 | **5개사**(16개사 확장 취소). 기업 5 / 기간 12 / 공시 60 / financial_facts 10,849 / capex_facts 120 / account_mapping 668 / standard_account_mapping 78 / chunk_metadata 21,797 / coverage 60(PASS 60) |
| KRX 연결 계층 | — | SQLite와 신규 설치 DDL에 `krx_indices`, `krx_index_constituents` 구현. 반도체·은행·자동차·헬스케어 내부 코드 등록. 기존 Windows MySQL은 root로 `apply_krx_migration_windows.sh` 실행 필요 |
| 검증 | 행 수·합계·ID | 테이블별 행 수, 기업×값유형별 금액 합계, `content_hash` 체크섬, `vector_id` 수·범위 = FAISS — 모두 일치. 같은 스냅샷 재실행 시 중복 없음(멱등) 확인 |
| 계정 | etl / backend / readonly / admin | `dart_etl`(읽기·쓰기), `dart_backend`(SELECT 전용, 삭제 거부 확인). readonly·admin은 미생성 |
| staging 테이블 | 사용 | **미사용**: 테이블마다 한 트랜잭션으로 직접 upsert(실패 시 해당 테이블 롤백) |
| 원격 접속 | VPN/사설망 | **미개방**. 계정 host는 `%`이므로 방화벽 설정 전에 범위를 정해야 함 |
| 백업 | 매일 + sync 직전 | **미구성** |

## 0. 전제와 금지 사항

- 적재 기준 데이터는 검증 완료된 기존 5개사 SQLite/FAISS 자산이다(Financial 10,849행, BGE 21,797벡터 = FAISS).
- 검증된 SQLite/FAISS 파이프라인은 그대로 유지한다. SQLite를 바로 폐기하지 않는다.
- 초기(Phase 1)에는 **SQLite가 원본(source of truth)**, MySQL은 읽기용 사본이다.
- KRX 지수는 별도 DB가 아니라 `corp_code` 기업 데이터 위의 다대다 연결 계층이다. 공식 구성종목은 `stock_code`로만 매핑하며 회사명 fallback을 사용하지 않는다.
- **BGE-M3 모델과 FAISS 인덱스는 MySQL에 넣지 않는다.** 중앙 서버 파일 시스템(또는 별도 검색 서비스)에 둔다. MySQL에는 "어떤 FAISS 파일이 어떤 데이터 버전인지"를 적는 메타데이터만 둔다.
- 스키마 근거는 정적 코드: `src/db/financial_db.py`(`SCHEMA`), `src/db/capex_db.py`(`CAPEX_SCHEMA`), `src/retrieval/bge_m3_indexer.py`(`SCHEMA`), `src/quality/account_mapping_quality.py`(`CORE_ACCOUNTS`). 컬럼 길이·키 중복은 적재 전 운영 DB를 읽기 전용으로 점검했다.

## 1. 현재 구조 요약

```text
DART OpenAPI
  ├─ 재무 API ─→ data/raw/financial ─→ 정제·매핑·기간정규화 ─→ db/financial/financial.db
  │                                                             (companies, periods, account_mapping,
  │                                                              financial_facts, capex_facts, ...)
  └─ 공시 원문 ─→ data/raw/filings ─→ 파싱 ─→ chunks_v2 JSONL ─→ BGE-M3 임베딩
                                                              ─→ db/vector/filings_bge_m3.db (filing_chunks + embedding BLOB)
                                                              ─→ db/vector/filings_bge_m3.faiss (vector_id 기준)
```

현재 팀원 간에는 이 SQLite/FAISS 파일을 직접 주고받아야 한다. 목표는 **중앙 MySQL에서 재무·메타데이터를 공동 조회**하고, 벡터 검색은 중앙 서버의 검색 기능을 호출하게 하는 것이다.

### 1.1 현재 스키마에서 MySQL 전환 시 주의할 점 (코드 기준 사실)

| 항목 | 현재 구조 | 전환 시 영향 |
|---|---|---|
| `periods.period_id` | 자동번호. DB를 새로 만들 때마다 번호가 달라질 수 있다 | MySQL 동기화 키로 쓰면 안 됨. **`(year, report_type)`** 를 자연키로 사용 |
| `financial_facts.fact_id` | 자동번호 | 동기화 키로 쓰지 않음. UNIQUE 복합키 사용 |
| `financial_facts` UNIQUE의 `ord` | NULL 가능. SQLite·MySQL 모두 UNIQUE에서 NULL끼리는 "다른 값"으로 취급 → 중복이 막히지 않을 수 있음 | MySQL에서는 `ord_key = COALESCE(ord, -1)` 같은 NOT NULL 컬럼 또는 해시키 사용 |
| `capex_facts` | 빌드할 때마다 **DROP 후 재생성**, `capex_id` 바뀜 | UNIQUE(`corp_code`,`year`,`report_type`,`fs_div`,`value_type`)로 upsert, 사라진 행은 별도 처리 |
| 메인 파이프라인 `src/pipeline.py` | `build_final_database`로 `financial.db`를 **통째로 새로 만들어 교체** | 요청 기업·fs_div 기준으로만 재생성된다. 동기화 스크립트는 활성 행이 1% 넘게 줄면 중단한다(`--allow-shrink`로만 허용) |
| `filing_chunks.vector_id` | 기존 번호 유지, 신규는 MAX+1 | MySQL에서도 `vector_id`를 그대로 사용(FAISS ID와 일치해야 함) |
| 금액 | SQLite INTEGER(64bit) | MySQL **BIGINT** 필수(대기업 매출은 수백조 원) |
| 문자열 | UTF-8 | MySQL `utf8mb4` 필수 |
| JSON 문자열(`source_values` 등) | TEXT에 JSON 문자열 | MySQL `JSON` 타입 또는 `TEXT` 유지(초기에는 TEXT 권장: 값 그대로 보존) |

## 2. 전환 단계

### Phase 1 — SQLite → MySQL 증분 sync

```text
DART → 기존 전처리 → SQLite(원본, 검증 완료본) → [스냅샷 복사] → sync script → MySQL(읽기용)
                                     └→ FAISS/BGE(서버 파일 시스템 유지)
```

| 항목 | 내용 |
|---|---|
| 목적 | 검증된 파이프라인을 건드리지 않고 중앙 DB를 제공 |
| 장점 | 기존 코드·테스트·검증 그대로. 실패해도 SQLite가 남아 있음. 롤백 쉬움 |
| 단점 | 데이터가 두 곳에 존재(동기화 지연). sync 스크립트 유지 비용 |
| 위험 | ① 진행 중인 SQLite를 읽어 중간 상태가 MySQL에 들어감 ② `period_id`·`capex_id` 같은 자동번호를 키로 써서 중복 발생 ③ 파이프라인 재실행으로 대량 삭제가 그대로 전파 |
| 위험 대응 | ① SQLite를 **읽기 전용 + backup API로 스냅샷 파일 복사** 후 스냅샷에서 sync, sha256 기록 ② 자연키만 사용 ③ 삭제 비율 임계값(예: 1% 초과 시 중단, 사람 승인) |
| 전환 조건(시작) | 기존 5개사 SQLite/FAISS 무결성 검사 통과, 데이터 버전 이름 확정 |
| 롤백 | MySQL 스키마/데이터를 이전 `data_version`으로 되돌리거나(백업 복원) 백엔드 연결을 SQLite 직접 조회로 되돌림. SQLite는 변경되지 않으므로 손실 없음 |

### Phase 2 — DART → 전처리 → MySQL 직접 적재

```text
DART → 전처리 → MySQL(원본) ─→ (필요 시) SQLite 내보내기
             └→ chunk → BGE-M3 → FAISS(서버 파일) + MySQL chunk_metadata
```

| 항목 | 내용 |
|---|---|
| 목적 | SQLite 중간 단계 제거, 중앙 DB가 원본 |
| 장점 | 데이터 한 곳, 동시 작업 가능, 권한·감사 관리 쉬움 |
| 단점 | `save_financial_data`, `materialize_capex_tables`, `build_bge_m3_index` 등 SQLite 전용 코드(UPSERT 문법, `sqlite_master`, `PRAGMA`, backup API) 재작성 필요. 테스트도 MySQL용으로 보강 필요 |
| 위험 | 재작성 과정의 값 변동(회귀), 네트워크 장애 시 적재 중단, 트랜잭션 크기 |
| 전환 조건 | Phase 1을 일정 기간 운영하며 **SQLite와 MySQL 값이 계속 일치**함을 확인(대조 리포트), MySQL 기반 회귀 테스트 통과, 백업·복구 리허설 완료 |
| 롤백 | Phase 1 방식(SQLite 원본 + sync)으로 복귀. 이를 위해 Phase 2 초기에도 SQLite 생성 경로를 일정 기간 유지 |

## 3. 무엇을 어디에 둘 것인가

| 데이터 | 위치 | 이유 |
|---|---|---|
| 기업, 공시, 재무 facts, CAPEX, 매핑, coverage, 버전 | **MySQL** | 표 형태, 여러 사람이 조회 |
| 청크 메타데이터·본문(`chunk_text`) | **MySQL** | 근거 조회·필터용 |
| 청크 embedding BLOB | **서버 파일(SQLite) 유지 권장** | 행당 4KB, 검색은 FAISS/SQLite가 담당. MySQL 크기·백업 부담만 커짐 |
| FAISS 인덱스 | **서버 파일 시스템** | MySQL에 넣는 개념이 아님 |
| BGE-M3 모델 캐시 | **서버 파일 시스템** | 대용량 모델 파일 |
| DART 원본 XML/ZIP | 서버 파일 시스템(또는 객체 저장소) | 원본 보존 |

## 4. 테이블 후보

> 아래 DDL은 **예시**이며 실행하지 않는다. 문자셋은 `utf8mb4`, 엔진 InnoDB를 가정한다.

### 4.1 `companies`

| 항목 | 내용 |
|---|---|
| 역할 | 기업 마스터 |
| PK | `corp_code` |
| UNIQUE | `stock_code`(NULL 허용) |
| INDEX | `corp_name` |
| SQLite source | `financial.db: companies(corp_code, company_name, stock_code)` |
| sync 기준 / upsert | `corp_code` 기준 upsert. 이름 변경 시 갱신 |

```sql
CREATE TABLE companies (
  corp_code   CHAR(8)      NOT NULL,
  corp_name   VARCHAR(100) NOT NULL,
  stock_code  CHAR(6)      NULL,
  is_operational TINYINT(1) NOT NULL DEFAULT 1,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (corp_code),
  UNIQUE KEY uk_companies_stock (stock_code),
  KEY ix_companies_name (corp_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 4.2 `periods`

| 항목 | 내용 |
|---|---|
| 역할 | 연도×보고서 유형 |
| PK | `(year, report_type)` (자연키) |
| SQLite source | `periods(year, report_type, report_code, report_name)` |
| upsert | `(year, report_type)`. SQLite `period_id`는 **가져오지 않음** |

```sql
CREATE TABLE periods (
  year         SMALLINT    NOT NULL,
  report_type  ENUM('Q1','H1','Q3','FY') NOT NULL,
  report_code  CHAR(5)     NOT NULL,
  report_name  VARCHAR(30) NOT NULL,
  PRIMARY KEY (year, report_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 4.3 `filings`

| 항목 | 내용 |
|---|---|
| 역할 | 정기공시 1건(접수번호 단위) |
| PK | `rcept_no` |
| UNIQUE | `(corp_code, year, report_type)` — 슬롯마다 선택된 공시 1건(정정공시면 최신) |
| INDEX | `corp_code, year` |
| SQLite source | 전용 테이블 없음. `filing_chunks`의 DISTINCT(`rcept_no`, corp_code, year, report_type) + `financial_facts.rcept_no`. `rcept_dt`는 접수번호 앞 8자리, `dart_report_name`·`is_amended`는 현재 NULL |
| upsert | `rcept_no` |
| 주의 | 같은 슬롯에서 Financial과 Vector의 rcept_no가 다르면 동기화 스크립트가 **중단**한다. 현재 60슬롯 모두 일치 |

```sql
CREATE TABLE filings (
  rcept_no        CHAR(14)     NOT NULL,
  corp_code       CHAR(8)      NOT NULL,
  year            SMALLINT     NOT NULL,
  report_type     ENUM('Q1','H1','Q3','FY') NOT NULL,
  rcept_dt        DATE         NULL,
  dart_report_name VARCHAR(200) NULL,
  is_amended      TINYINT(1)   NULL,
  source_file_root VARCHAR(500) NULL,
  data_version    VARCHAR(50)  NOT NULL,
  PRIMARY KEY (rcept_no),
  UNIQUE KEY uk_filings_slot (corp_code, year, report_type),
  CONSTRAINT fk_filings_company FOREIGN KEY (corp_code) REFERENCES companies(corp_code),
  CONSTRAINT fk_filings_period FOREIGN KEY (year, report_type) REFERENCES periods(year, report_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 4.4 `financial_facts`

| 항목 | 내용 |
|---|---|
| 역할 | 재무 값 원장(원본·계산값 모두) |
| PK | `fact_id`(MySQL 자체 번호, 외부 노출 안 함) |
| UNIQUE | `(corp_code, year, report_type, fs_div, sj_div, account_key, ord_key, value_type, is_calculated)` — SQLite UNIQUE와 같은 의미, `period_id` → `(year, report_type)`, `ord` → `ord_key` |
| INDEX | `(corp_code, year, report_type, fs_div)`, `(standard_account_nm)`, `(account_key, sj_div)` |
| SQLite source | `financial_facts` JOIN `periods` |
| upsert | 위 UNIQUE 기준 `INSERT ... ON DUPLICATE KEY UPDATE` (SQLite와 동일하게 값 관련 컬럼만 갱신) |

```sql
CREATE TABLE financial_facts (
  fact_id            BIGINT       NOT NULL AUTO_INCREMENT,
  corp_code          CHAR(8)      NOT NULL,
  year               SMALLINT     NOT NULL,
  report_type        ENUM('Q1','H1','Q3','FY') NOT NULL,
  rcept_no           CHAR(14)     NULL,
  fs_div             ENUM('CFS','OFS') NOT NULL,
  sj_div             VARCHAR(5)   NOT NULL,
  account_key        VARCHAR(255) NOT NULL,
  account_id         VARCHAR(255) NULL,
  original_account_nm VARCHAR(255) NOT NULL,
  standard_account_nm VARCHAR(100) NULL,
  ord                INT          NULL,
  ord_key            INT          NOT NULL,          -- COALESCE(ord, -1)
  period_type        VARCHAR(20)  NOT NULL,
  value_type         VARCHAR(20)  NOT NULL,
  period_start       DATE         NULL,
  period_end         DATE         NOT NULL,
  currency           VARCHAR(10)  NULL,
  source_value_field VARCHAR(50)  NOT NULL,
  raw_value          VARCHAR(100) NULL,
  normalized_value   BIGINT       NULL,
  calculated_value   BIGINT       NULL,
  calculation_method TEXT         NULL,
  source_values      TEXT         NULL,              -- JSON 문자열 그대로
  is_calculated      TINYINT(1)   NOT NULL,
  data_version       VARCHAR(50)  NOT NULL,
  PRIMARY KEY (fact_id),
  UNIQUE KEY uk_fact (corp_code, year, report_type, fs_div, sj_div, account_key, ord_key, value_type, is_calculated),
  KEY ix_fact_lookup (corp_code, year, report_type, fs_div),
  KEY ix_fact_std (standard_account_nm),
  KEY ix_fact_account (account_key, sj_div)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

> `account_key` 최대 길이는 실제 데이터로 확인해야 한다(추후 확인). 너무 길면 UNIQUE 인덱스 길이 제한(3072 bytes)에 걸릴 수 있으므로 `fact_natural_key CHAR(64)`(자연키 sha256) UNIQUE로 대체하는 방안도 있다.

### 4.5 `standardized_financials` (VIEW 또는 테이블)

| 항목 | 내용 |
|---|---|
| 역할 | 24개 표준계정만, 백엔드가 바로 쓰기 쉬운 형태 |
| 형태 | 초기에는 **VIEW** 권장(원장 하나만 관리) |
| 주요 컬럼 | corp_code, corp_name, year, report_type, rcept_no, fs_div, standard_account, value(= calculated_value 또는 normalized_value), value_type, is_calculated, value_source, calculation_method, source_values |
| source | `financial_facts` + `standard_accounts` + `capex_facts` (SQLite VIEW `financial_facts_final`과 같은 발상) |
| 주의 | 운영 5개사의 검증된 `standard_account_nm` 기준을 사용하고 `standard_account_mapping`(4.8)으로 거른다. |

### 4.6 `capex_facts`

| 항목 | 내용 |
|---|---|
| 역할 | CAPEX direct/derived 및 분기 계산값 |
| UNIQUE | `(corp_code, year, report_type, fs_div, value_type)` |
| SQLite source | `capex_facts` JOIN `periods` |
| upsert | UNIQUE 기준. SQLite에서 매번 재생성되므로, sync 시 이번 스냅샷에 없는 MySQL 행은 `is_active=0`(soft delete) 처리 후 검토 |
| 주요 컬럼 | SQLite `CAPEX_SCHEMA` 컬럼 전부(`cash_outflow_amount`, `value_source`, `capex_method`, `source_account_ids`, `source_account_names`, `source_values`, `review_required` 등) + `data_version`, `is_active` |

### 4.7 `standard_accounts`

| 항목 | 내용 |
|---|---|
| 역할 | 24개 핵심 표준계정 정의 |
| PK | `standard_account_nm` |
| source | 코드 상수 `CORE_ACCOUNTS` |
| 주요 컬럼 | standard_account_nm, sj_divs(JSON), account_ids(JSON), aliases(JSON), period_nature, display_order |

### 4.8 `standard_account_mapping` / `account_mapping`

| 테이블 | 역할 | 키 | source |
|---|---|---|---|
| `account_mapping` | 원본 계정 → 표준계정(전체 후보) | PK(`account_key`, `sj_div`) | SQLite `account_mapping` |
| `standard_account_mapping` | 24개 핵심계정 승인/검토 매핑 | UNIQUE(`standard_account_nm`, `account_id`, `original_account_nm`, `sj_div`, `fs_div`) | `data/mapping/account_mapping_core.csv` |

`mapping_status` / `review_required` / `review_status`로 이름이 나뉘어 있으므로 MySQL에서는 `mapping_method`(direct/alias/existing_mapping/derived/none)와 `review_status`(approved/review_required/unmapped)로 **통일 제안**.

### 4.9 `chunk_metadata`

| 항목 | 내용 |
|---|---|
| 역할 | 공시 청크 메타데이터·본문(근거 조회, 필터) |
| PK | `chunk_id` |
| UNIQUE | `vector_id` |
| INDEX | `(corp_code, year, report_type, rcept_no, is_searchable)` — SQLite `idx_bge_filter`와 같은 목적 |
| SQLite source | `filings_bge_m3.db: filing_chunks` (embedding BLOB **제외**) |
| upsert | `chunk_id` 기준. `vector_id`가 바뀌면 **오류로 중단**(FAISS와 불일치 위험) |
| 주요 컬럼 | vector_id, chunk_id, corp_code, corp_name, year, report_type, rcept_no, section_name, subsection_name, source_file, chunk_text(MEDIUMTEXT), original_text(MEDIUMTEXT, 선택), content_hash, chunk_version, token_count, content_type, search_priority, selection_reason, is_searchable, embedding_model, model_revision, embedding_dimension, embedded_at, index_version |

### 4.10 `embedding_metadata` (= 벡터 인덱스 버전)

| 항목 | 내용 |
|---|---|
| 역할 | 어떤 FAISS 파일이 어떤 데이터 버전·모델로 만들어졌는지 |
| PK | `index_version` |
| 주요 컬럼 | index_version, model_name(`BAAI/bge-m3`), model_revision, embedding_dimension(1024), normalize_embeddings, faiss_index_type(`IndexIDMap2(IndexFlatIP)`), chunk_version, faiss_path, faiss_sha256, sqlite_path, sqlite_sha256, searchable_vector_count, faiss_ntotal, max_vector_id, built_at, is_current |
| source | SQLite `vector_metadata` + 파일 sha256 |

### 4.11 `coverage`

| 항목 | 내용 |
|---|---|
| 역할 | 기업×연도×보고서 슬롯별 데이터 존재 여부 |
| PK | `(corp_code, year, report_type)` |
| 주요 컬럼 | rcept_no, financial_rcept_no, financial_exists, fs_div, filing_exists, chunk_exists, embedding_exists, capex_status(direct/derived/unresolved), status(PASS/SOURCE_ABSENT/MISSING_OR_REVIEW), reason, data_version |
| source | 동기화 시 Financial DB(`financial_facts`, `capex_facts`)와 BGE DB(`filing_chunks`)에서 계산 |

### 4.12 `data_versions` / `sync_runs`

```sql
CREATE TABLE data_versions (
  data_version        VARCHAR(50)  NOT NULL,       -- 예: 2026-10-06-dart5-v1
  description         VARCHAR(500) NULL,
  financial_sqlite_sha256 CHAR(64) NOT NULL,
  vector_sqlite_sha256    CHAR(64) NOT NULL,
  faiss_sha256            CHAR(64) NOT NULL,
  index_version       VARCHAR(50)  NOT NULL,
  manifest_path       VARCHAR(500) NULL,
  manifest_sha256     CHAR(64)     NULL,
  validation_report_path VARCHAR(500) NULL,
  validated           TINYINT(1)   NOT NULL DEFAULT 0,
  is_current          TINYINT(1)   NOT NULL DEFAULT 0,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (data_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sync_runs (
  sync_run_id   BIGINT      NOT NULL AUTO_INCREMENT,
  data_version  VARCHAR(50) NOT NULL,
  table_name    VARCHAR(64) NOT NULL,
  started_at    DATETIME    NOT NULL,
  finished_at   DATETIME    NULL,
  status        ENUM('running','success','failed','aborted') NOT NULL,
  rows_source   BIGINT      NULL,
  rows_inserted BIGINT      NULL,
  rows_updated  BIGINT      NULL,
  rows_deactivated BIGINT   NULL,
  error_message TEXT        NULL,
  PRIMARY KEY (sync_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## 5. 관계 구조

```text
companies (corp_code) ─┬─< filings (rcept_no; corp_code, year, report_type)
                       ├─< financial_facts (corp_code, year, report_type, ...)
                       ├─< capex_facts (corp_code, year, report_type, fs_div, value_type)
                       ├─< chunk_metadata (chunk_id; vector_id; rcept_no)
                       └─< coverage (corp_code, year, report_type)
periods (year, report_type) ─< 위 모든 기간 컬럼
filings.rcept_no ─< financial_facts.rcept_no, capex_facts.rcept_no, chunk_metadata.rcept_no  (논리 연결, FK는 선택)
standard_accounts ─< standard_account_mapping
embedding_metadata (index_version) ─< chunk_metadata.index_version
data_versions ─ 모든 테이블의 data_version
```

> `financial_facts.rcept_no` → `filings` FK는 **처음에는 걸지 않기를 권장**: 원천 데이터에서 rcept_no가 비거나 Vector와 다를 수 있다. 정합성은 검증 쿼리로 확인한다.

## 6. SQLite → MySQL 필드 매핑

| MySQL | SQLite 원천 | 변환 |
|---|---|---|
| `companies.corp_name` | `companies.company_name` | 이름 변경 |
| `financial_facts.year`, `report_type` | `periods.year`, `periods.report_type` (via `period_id`) | JOIN 후 자연키로 |
| `financial_facts.ord_key` | `financial_facts.ord` | `COALESCE(ord, -1)` |
| `financial_facts.is_calculated` | 0/1 | 그대로 |
| `financial_facts.normalized_value` 등 | INTEGER | BIGINT |
| `capex_facts.*` | `capex_facts` + `periods` | `capex_id` 버림 |
| `chunk_metadata.*` | `filing_chunks`(BGE) | `embedding` 제외 |
| `embedding_metadata.*` | `vector_metadata` key/value | 행으로 펼침 + 파일 sha256 |
| `account_mapping.*` | `account_mapping` | 그대로 |
| `standard_accounts.*` | `CORE_ACCOUNTS` 코드 상수 | 리스트 → JSON |

## 7. 초기 full migration 절차 (Phase 1 시작 시 1회)

> 2026-10-06 실제 수행: 아래 절차를 `sync_sqlite_to_mysql.py`로 실행. 4번은 별도 staging 스키마 없이 `dart_big_in`에 바로 적재, 6번의 무작위 샘플 비교 대신 금액 합계·체크섬으로 대조, 7번의 스키마 전환은 생략.

1. 데이터 작업 완료·검증 확인(`DATA_PIPELINE_VALIDATION_CHECKLIST.md`).
2. `data_version` 이름 확정.
3. 운영 SQLite 2개를 **읽기 전용으로 열어 SQLite backup API로 스냅샷 파일 생성**, FAISS 파일 복사. 세 파일 sha256 기록.
4. MySQL 스키마 생성(빈 DB, 별도 스키마명 예: `dart_big_in_staging`).
5. 스냅샷에서 테이블별 적재(배치 1,000~5,000행), 순서: periods → companies → filings → account_mapping/standard_* → financial_facts → capex_facts → chunk_metadata → embedding_metadata → coverage → data_versions.
6. 대조 검증: 테이블별 행 수, 기업·기간별 행 수, 숫자 합계(예: 기업·연도별 `SUM(normalized_value)`), 무작위 샘플 100건 값 비교, `vector_id` 집합 = FAISS ID 집합.
7. 통과하면 `data_versions.validated=1`, `is_current=1`로 지정하고 백엔드용 스키마로 전환(스키마 rename 또는 백엔드 설정의 DB명 변경).

## 8. 이후 incremental sync

| 항목 | 전략 |
|---|---|
| 트리거 | 파이프라인 작업이 끝나고 검증 리포트가 PASS일 때만 수동/자동 실행. **진행 중 SQLite는 절대 읽지 않음** |
| 변경 감지 | 스냅샷 sha256이 직전 `data_versions`와 같으면 skip. 다르면 테이블별 자연키로 비교 |
| upsert | `INSERT ... ON DUPLICATE KEY UPDATE` (자연키 기준) |
| 삭제 | 이번 스냅샷에 없는 MySQL 행은 즉시 DELETE 하지 않고 `is_active=0` + `deactivated_version` 기록. 삭제 비율이 임계값(예: 1%) 넘으면 **중단 후 사람 확인** |
| 중복 방지 키 | companies=`corp_code`, filings=`rcept_no`, financial=복합 UNIQUE(4.4), capex=`(corp_code,year,report_type,fs_div,value_type)`, chunks=`chunk_id` + `vector_id` UNIQUE, mapping=`(account_key, sj_div)` |
| 불변 조건 | 기존 `chunk_id`의 `vector_id`가 바뀌면 오류. 기존 5개사 facts 값이 바뀌면 경고(회귀 의심) |

## 9. 트랜잭션·실패 재실행

- 설계: 테이블 단위로 staging 테이블에 먼저 적재 → 검증 → 본 테이블 upsert.
- **현재 구현**: staging 없이 테이블마다 `START TRANSACTION … COMMIT` 한 번으로 upsert(`chunk_metadata`도 한 트랜잭션, 200행 단위 INSERT). 오류가 나면 그 테이블은 커밋되지 않고, 스크립트가 `sync_runs`에 `failed`를 남기고 멈춘다.
- 실패 시 `sync_runs.status='failed'`와 오류를 남기고 중단. 본 테이블은 직전 버전 그대로.
- 재실행은 **멱등**(같은 스냅샷을 몇 번 넣어도 결과 동일)해야 한다 → 자연키 upsert로 보장.
- `data_versions.is_current` 전환은 모든 테이블 성공 후 **마지막에 한 번**.

## 10. manifest / version 관리

- 현재 운영 파이프라인에는 manifest 파일이 없다. 버전·이력은 MySQL `data_versions`와 `sync_runs`로 관리한다.
- **구현됨**: `data_version` 하나가 세 파일을 묶는다 — `financial.db`, `filings_bge_m3.db`, `filings_bge_m3.faiss`의 sha256. 검증을 통과한 버전만 `is_current=1`이 된다. 현재: `2026-10-06-dart5-v1`.
- 백엔드 응답 `meta.data_version`에 현재 버전을 넣어 화면에서도 확인 가능하게 한다.
- 검색 서버는 시작 시 자신이 읽은 FAISS sha256이 `data_versions.is_current` 행과 같은지 확인한다(다르면 경고 또는 fail-closed).

## 11. 권한 분리

| 계정(예시) | 권한 | 사용처 |
|---|---|---|
| `dart_etl` | 대상 스키마 SELECT/INSERT/UPDATE/DELETE, staging CREATE | sync 스크립트(데이터 담당자 서버에서만) |
| `dart_backend` | **SELECT만** | 백엔드 API |
| `dart_readonly` | SELECT (일부 테이블) | 분석·노트북 |
| `dart_admin` | DDL, 백업 | 관리자 1~2명, 원격 접속 제한 |

- root 원격 접속 금지. 계정별 접속 허용 IP(`'user'@'10.0.0.%'`) 제한.

## 12. 백엔드 원격 접속 방식

- MySQL 포트(3306)를 인터넷에 직접 열지 않는다. **사설망/VPN** 또는 **SSH 터널**을 사용한다.
- 가능하면 TLS 연결 강제(`REQUIRE SSL`).
- 벡터 검색은 MySQL이 아니라 **중앙 서버의 검색 기능**을 써야 한다. 선택지:
  1. 백엔드를 FAISS/모델이 있는 같은 서버에서 실행하고 `retrieve_filing_context`를 import
  2. 검색 전용 내부 HTTP 서비스(예: `POST /search`)를 두고 백엔드가 호출 (제안, 미구현)
- 검색 결과의 `chunk_id`로 MySQL `chunk_metadata`에서 근거를 조회하는 구조가 가능하다.

환경변수 예시(값은 자리표시, **실제 비밀번호를 문서·코드·Git에 넣지 않는다**):

```bash
MYSQL_HOST=<server-ip>
MYSQL_PORT=3306
MYSQL_DATABASE=dart_big_in
MYSQL_USER=<backend-user>
MYSQL_PASSWORD=<secret>
MYSQL_SSL_CA=<path-to-ca.pem>
DATA_VERSION=<expected-data-version>        # 선택: 기대 버전 고정
FILING_RETRIEVER=bge_m3_dense
FILING_RETRIEVER_AUTO_FALLBACK=false
```

## 13. 보안 주의사항

- `.env`, 비밀번호, DART/Gemini API 키는 Git·문서·채팅에 올리지 않는다. 서버에는 권한 600 파일 또는 비밀 관리 도구로 둔다.
- 백엔드 계정은 SELECT만. SQL은 항상 파라미터 바인딩(현재 코드도 `?` 바인딩 사용).
- `source_file`(서버 내부 경로)은 외부 응답에서 제외하거나 가린다.
- 공시 원문은 공개 데이터지만, 프롬프트에 원문을 넣어 외부 LLM을 호출할 때는 팀 정책을 따른다.
- MySQL 감사 로그·접속 로그 보관.

## 14. 백업 전략

| 대상 | 방법 | 주기(제안) |
|---|---|---|
| MySQL | `mysqldump --single-transaction` 또는 물리 백업 + binlog | 매일 + sync 직전 |
| SQLite 2개 + FAISS | `data_version`별 스냅샷 묶음(sha256 포함) 보관 | 버전 생성 시 |
| 모델 캐시 | revision 고정, 필요 시 재다운로드 가능 → 선택 | — |
| 보관 | 최소 최근 3개 `data_version` | — |

- 복구 리허설을 Phase 2 전환 조건에 포함한다.

## 15. 롤백 전략

| 상황 | 롤백 |
|---|---|
| sync 중 실패 | 본 테이블 미변경(staging만 실패) → 원인 수정 후 재실행 |
| sync 후 값 이상 발견 | `data_versions`에서 직전 버전을 `is_current`로 되돌리고 sync 직전 MySQL 백업 복원 |
| 검색 이상 | FAISS/SQLite 스냅샷 묶음을 직전 버전으로 교체(코드의 fail-closed 검증이 불일치를 잡음). 필요 시 `FILING_RETRIEVER=feature_hashing_hybrid` 로 legacy 전환 |
| Phase 2 문제 | Phase 1(SQLite 원본 + sync)로 복귀 |

## 16. 추후 확인 필요

확인 완료(2026-10-06 적재 시):
- 컬럼 길이: `account_key` 최대 180자, `original_account_nm` 63자 등 모두 스키마 이내. strict 모드 적재 성공
- `financial_facts.ord` NULL 0건, `ord_key` 기준 중복 0건
- 슬롯별 Financial·Vector `rcept_no` 불일치 0건(60/60)

남은 항목:
1. **원격 접속 개방 범위**(방화벽 허용 대역, VPN 여부)와 계정 host 제한 방식.
2. **백업 구성**: `mysqldump --single-transaction` 주기와 보관 위치.
3. 서버 위치: 데이터 담당자 PC를 계속 쓸지, 상시 켜진 서버로 옮길지(PC가 꺼지면 DB도 꺼짐).
4. 중앙 서버 사양(메모리: BGE-M3 모델 + 필터 후보 임시 인덱스), GPU 유무.
5. 백엔드 프레임워크·배포 위치(같은 서버 import 방식 vs 검색 HTTP 서비스).
6. `src/pipeline.py` 재실행 시 운영 5개사 CFS 데이터가 동일하게 재생성되는지.
7. `dart_readonly`, `dart_admin` 계정 필요 여부.
