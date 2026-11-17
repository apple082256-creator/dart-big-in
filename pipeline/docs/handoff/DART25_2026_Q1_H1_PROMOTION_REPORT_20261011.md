# DART 25개사 2026 Q1·H1 운영 승격 보고서

- 작업일: 2026-10-11 (KST). 로컬 교체 20:11, MySQL current 전환 20:53
- 대상: 2026 Q1(`11013`)·H1(`11012`), 25개사 × 2 = 50 슬롯. Q3/FY 제외
- 이전 단계: [`DART25_2026_Q1_H1_INCREMENT_REPORT.md`](DART25_2026_Q1_H1_INCREMENT_REPORT.md) (staging 검증)
- 최종 판정: **PROMOTED_AND_READY_FOR_BACKEND** (10절 조건 참고)

## 1. 승격 전 결정 사항

- POSCO홀딩스·HL만도 2026 H1 영업활동현금흐름(분기) 2건: 요청서는 `review_required` 유지(986/2)였으나 staging은
  `approved`(988/0)였다. 사용자 확인 결과 **`approved` 유지**로 결정하고 게이트를 988/0으로 적용했다.

## 2. 사전 상태와 백업

| 자산 | 경로 | 크기(bytes) | SHA-256 | 행 수 | 버전 |
|---|---|---:|---|---|---|
| Financial DB | `db/financial/financial.db` | 39,948,288 | `ea656cfd…0aa0bdd` | facts 63,931 / capex 469 / KPI 1,677 | - |
| Vector DB | `db/vector/filings_bge_m3.db` | 1,331,277,824 | `a1b6f68e…cd98c9ce` | chunks 147,720 | - |
| FAISS | `db/vector/filings_bge_m3.faiss` | 606,242,970 | `d8d17f64…c88b0292` | ntotal 147,720 | - |
| MySQL `dart_big_in` | `KHP-SD1-03:3306` | - | - | facts 63,931(활성) / KPI 1,677 / chunks 147,720 / docs 399 | 재무 current `2026-10-10-healthcare-rnd-v12`, 인덱스 current `bge-m3-2026-10-09-dart25-h1h3-v11` |

- 로컬 백업: `backups/local/20261011_pre_2026q1h1_v14/` (바이트 복사, 세 해시 모두 원본과 동일, `SHA256SUMS`)
- MySQL 전체 덤프: `backups/mysql/20261011_pre_2026q1h1_v14/dart_big_in_pre_2026q1h1_v14.sql`
  (`mysqldump --single-transaction --routines --triggers --hex-blob`, 4,903,423,790 bytes, 테이블 23 + VIEW,
  SHA-256 `e0a9dd7aad1c601e365eb962b8e2e72c61abe2c8245a553bae624647727cfab1`, 정상 종료 확인)
- 참고: MySQL current는 v12였다. v13은 `validated=1, is_current=0`(이전 문서가 v13을 current로 잘못 적음).

## 3. 스테이징 재확인

| 조건 | 기대 | 실제 |
|---|---|---|
| 기업 / 슬롯 / Q3·FY | 25 / 50 / 0 | 25 / 50 / 0 |
| 신규 facts / CAPEX / KPI | 11,373 / 80 / 988 | 11,373 / 80 / 988 |
| KPI approved / review_required | 988 / 0 (1절 결정) | 988 / 0 |
| 신규 청크 / 총 임베딩 / FAISS | 22,814 / 170,534 / 170,534 | 22,814 / 170,534 / 170,534 |
| SQLite ID 집합 = FAISS ID 집합 | 불일치 0 | 0 / 0 |
| SQLite 벡터 BLOB = FAISS 벡터 (전수) | 0 | 0 |
| 기존 147,720행 (모든 컬럼·임베딩) | 변경 0 | 0 |
| 기존 2023~2025 재무 값 | 변경 0 | 0 (`capex_id` 재번호 457건·`krx_indices.updated_at`·CAPEX 빌드 메타만 다름. `capex_id`는 MySQL로 가지 않는 내부 번호) |

## 4. 승격 중 발견해 고친 문제

| # | 문제 | 분류 | 조치 |
|---|---|---|---|
| 1 | 2026 청크 22,814건의 `source_file`과 R&D KPI 20건의 원문 경로가 staging 디렉터리를 가리킴 | 데이터(경로) | 원문 XML 50 + ZIP 50을 `data/raw/filings/{xml,zip}/…/2026/`로 복사(덮어쓰기 없음, 해시 대조). `promote/` 후보에서 경로를 운영 경로로 바꿈 |
| 2 | 2026 청크·R&D KPI의 `source_document_id`가 정식 ID가 아니라 XML 파일 해시 | 데이터(계약 위반) | 정식 ID `SHA-256(rcept_no+'\0'+source_path)`로 재계산. KPI→청크→문서 316/316 일치. 생성 코드(`src/staging_filing_increment.py`)도 수정 |
| 3 | 프로필 행이 없는 14개사 2026 KPI 506행이 서비스 API `get_industry_kpis`에서 0행 반환 | 서비스 결함 | `src/retrieval/industry_kpi.py`가 KRX 그룹으로 산업을 정하도록 수정(데이터 변경 없음). 988/988 조회 확인, 회귀 테스트 추가 |
| 4 | 감사(`financial_db_audit`)가 암묵 프로필·CAPEX 음수 정책·공시 비율을 몰라 hard check 실패 | 검사 규칙 지연 | 규칙을 현재 정책에 맞춤. 300건 DIRECT 값은 근거 fact와 전부 일치 확인 후 반영. 승격 전·후 DB 모두 hard check 0 |

`final/`은 그대로 두고 수정본은 `db/staging/20261011_2026q1h1/promote/`에 만들었다. `final/` 대비 차이는 위 1·2번 컬럼뿐이다
(Vector `source_file`·`source_document_id` 22,814행, KPI `source_document_id`·`source_values` 20행).
재배치 기록: `db/staging/20261011_2026q1h1/promote/relocation_report.json`.

## 5. 로컬 승격

- 교체 직전 운영 해시를 2절 값과 다시 대조했다. 운영 파일을 여는 프로세스와 WAL/journal 파일이 없었다.
- 같은 파일시스템에 임시 복사 → 해시 확인 → `os.replace`로 원자 교체.

| 파일 | 승격 후 SHA-256 | 크기(bytes) |
|---|---|---:|
| `financial.db` | `0ed3ac22953aec1cf25bee9890325e3f122d1c9249a0adffdc8007fe84966a92` | 47,337,472 |
| `filings_bge_m3.db` | `608bcdad6865710f810b1bb125b4e8e7090674a62a7e0182c39737832f53d6d8` | 1,536,118,784 |
| `filings_bge_m3.faiss` | `63ebe30e3168a5e2000e39980c453d1077e68e9d4bbbea82e2de5429c100c922` | 699,871,626 |

사후 확인: `PRAGMA integrity_check` ok(Financial·Vector), FK 위반 0, companies 25 / facts 75,304 / CAPEX 549 / KPI 2,665,
벡터 147,720 + 22,814 = 170,534 = FAISS ntotal, ID 불일치 0, 기존 벡터 변경 0. (SQLite에는 `data_versions` 테이블이 없다.
버전은 MySQL에서만 관리한다.)

## 6. MySQL 동기화와 버전 승격

기존 운영 경로만 사용했다(새 적재 로직 없음).

1. `sync_sqlite_to_mysql.py --data-version 2026-10-11-dart25-2026q1h1-v14 --defer-promotion`: dry-run 후 적재, 기본 대조 PASS
2. `sync_source_documents_to_mysql.py --data-version …`: 원본 XML 449개, 청크→문서 170,534개. 바이트·SHA-256 PASS
3. `sync_embeddings_to_mysql.py --data-version …`: 벡터 170,534개, 체크섬·vector_id·청크 연결 PASS
4. 전체 대조(7절) 후 한 트랜잭션으로 `data_versions`(v14 `validated=1`, 유일 current)와
   `embedding_metadata`(`bge-m3-2026-10-11-dart25-2026q1h1-v14` 유일 current) 전환

| 버전 | validated | is_current |
|---|---|---|
| `2026-10-11-dart25-2026q1h1-v14` | 1 | 1 |
| `2026-10-10-healthcare-rnd-docid-v13` | 1 | 0 |
| `2026-10-10-healthcare-rnd-v12` (직전 current) | 1 | 0 |

current 개수: 재무 1, 인덱스 1. 실패 `sync_runs` 0. 다른 버전의 활성 행 0.

## 7. SQLite ↔ MySQL 정합성 (current 전환 전·후 동일)

| 대상 | 행(SQLite/MySQL) | 자연키 누락 | 값 불일치 |
|---|---|---:|---:|
| companies | 25 / 25 | 0 | 0 |
| periods | 14 / 14 | 0 | 0 |
| company_financial_profiles | 11 / 11 | 0 | 0 |
| financial_collection_status | 110 / 110 | 0 | 0 |
| financial_facts (19컬럼 + source_values JSON) | 75,304 / 75,304 | 0 | 0 |
| capex_facts (25컬럼) | 549 / 549 | 0 | 0 |
| financial_metric_values (11컬럼 + source_values JSON) | 2,665 / 2,665 | 0 | 0 |
| chunk_metadata (rcept_no, source_file, content_hash 등) | 170,534 / 170,534 | 0 | 0 |
| chunk_embeddings (벡터별 SHA-256) | 170,534 / 170,534 | 0 | 0 |
| chunk_source_documents (chunk_id→document_id) | 170,534 / 170,534 | 0 | 0 |
| filing_source_documents (rcept_no, path, size, sha256, raw_xml 해시) | 449 / 449 | 0 | 0 |
| filings (슬롯→rcept_no) | 100 / 100 | 0 | 1 |

filings 1건은 KB금융 2025 FY다(재무 API 정정공시 vs 원문 원 공시). 승격 전부터 있던 차이이고 2026 범위 밖이다.
상세: `backups/mysql/20261011_pre_2026q1h1_v14/sqlite_mysql_compare_{before,after}_current.json`.

## 8. 2026 커버리지와 서비스 smoke

- MySQL `coverage` 2026: 50/50 PASS (재무·원문·청크·임베딩 모두 존재). 품질 CSV
  `data/processed/quality/dart25_2026_q1_h1_coverage.csv` 50행, 검토·누락 0, 문서 ID 50/50 정식 ID.
- 서비스 smoke(`financial_retriever`, `industry_kpi`, `operational_retriever` BGE-M3 dense, MySQL 원문 조회):
  반도체 삼성전자, 자동차 현대자동차, 철강 POSCO홀딩스, 헬스케어 셀트리온·삼성바이오로직스, 금융 KB금융·카카오뱅크 ×
  Q1/H1 = **14/14 통과**. 재무 행·KPI 행·검색 결과의 rcept_no가 슬롯 접수번호와 일치하고, 검색 1위 청크의
  원문 문서가 MySQL에서 열리며 파일이 존재한다. fallback 0.
- 헬스케어 R&D(셀트리온·삼성바이오로직스 × Q1/H1 × 2지표): 8/8 조회, 모두 `approved`, 문서 MySQL 연결.
  예: 셀트리온 H1 `RND_EXPENSE` 284,259,659,000원(누적), `RND_TO_REVENUE` 1,119 BPS.
- 금융: 구조화 KPI(KB금융 27행, 카카오뱅크 25행/기간) 조회 정상. NIM·BIS·총자본비율·CET1·NPL·연체율 구조화 행 0 확인.
  KB금융 2026 H1 "BIS 자기자본비율" 질의는 `5. 재무건전성 등 기타 참고사항`의 BIS 표 청크를 반환(fallback 동작).
- 2026 Q3/FY 요청: 재무 0행, 검색 0행.
- 결과: `backups/local/20261011_pre_2026q1h1_v14/service_smoke.json`

## 9. 테스트

### 기존 실패 3건 재검토

| 테스트 | 분류 | 근거와 조치 |
|---|---|---|
| `test_backend_fixes.test_core_accounts_are_matched_by_official_tag` | C | VIEW가 migration 007에서 재정의됨(운영 VIEW·init 스키마 모두 007). 테스트가 006만 봄 → 최신 정의 migration을 보도록 수정 |
| `test_bank_xml_backfill.test_backfill_is_additive_idempotent_and_survives_rebuild` | B | 재생성 시 기존 행 삭제·값·상태 변경 0, 미적재 헬스케어·HVM 2023~2025 KPI 1,181행만 추가 → 기존 행 불변 + 은행 행 추가 0으로 검사 |
| `test_backend_fixes.test_profile_selects_ofs_without_changing_others_or_metrics` | B/C | 위와 같은 부분 적재 + 2026 HVM KPI 생성 정책 → 기존 행 불변(값·상태), HVM은 OFS 행만 검사 |

A(실제 코드/데이터 오류)는 없었다.

### 승격 후 새로 드러난 실패와 조치

| 테스트 | 분류 | 조치 |
|---|---|---|
| `test_expense_sign` (360 ≠ 420) | B | 2026 은행 비용 60행 추가. 60행도 표준화·양수·크기 보존 조건 충족 확인 → 연도별 기대값 |
| `test_healthcare_rnd` (60 ≠ 70) | B | 기간 수에서 기대값 계산 |
| `test_industry_kpis` (12 ≠ 14) | B | 누락 기간 0으로 검사 |
| `test_financial_cleanup` | B | 정리 파이프라인은 승격 전·후 모두 기존 행 변경 0, 미적재 867행 추가 → 기존 행 불변으로 검사 |
| `test_sign_convention` | B | 재생성 시 은행 외 미적재 행 허용, 은행은 Q4 순액만 |
| `test_financial_db_audit` (4 hard check) | C(2건은 승격 전부터) | 4절 4번 |

### 최종 회귀 결과

운영 승격 후 전체 테스트 모듈 23개를 모듈별로 실행했다(`backups/local/20261011_pre_2026q1h1_v14/tests_final/`).

- **23/23 모듈, 106/106 테스트 통과**, 실패·오류 0
- 대상: financial, CAPEX, KPI, healthcare R&D, bank(XML 백필·비용 부호), Vector/FAISS, 운영 검색, source document 연결,
  increment pipeline, 감사, 분기 백필·잔여 보정, 부호 규칙
- `pytest`는 환경에 없어 `python -m unittest`로 실행했다. 한 번에 `discover`로 돌리면 오래 걸리는 모듈 때문에 시간 초과가 나므로 모듈별로 실행했다.

- Python compile(`src`, `infra/mysql`, `tests`): 통과
- `git diff --check`: 통과
- SQLite `integrity_check`: Financial ok, Vector ok
- 테스트 후 운영 파일 해시: 변경 없음(테스트가 운영 파일을 쓰지 않음)

## 10. 최종 판정

| 조건 | 결과 |
|---|---|
| 2026 Q1/H1 50/50 | PASS |
| Q3/FY 0 | PASS |
| SQLite integrity | PASS |
| MySQL sync | PASS (3단계 모두) |
| SQLite/MySQL 정합성 | PASS (2026·운영 데이터 불일치 0, 기존 KB 2025 FY 1건은 범위 밖) |
| Vector count = 170,534 | PASS |
| FAISS ntotal = 170,534 | PASS |
| Vector/FAISS ID 정합성 | PASS |
| 기존 데이터 회귀 | PASS (값 변경 0) |
| review_required | 2026: 0 (1절 결정으로 2 → 0). 전체: KPI 10·CAPEX 2(2023~2025, 변경 없음) |
| source document 연결 | PASS (316/316, 청크→문서 170,534/170,534) |
| 서비스 smoke | PASS (14/14) |
| 백엔드 인계 문서 | 완료 |
| rollback 문서 | 완료 |

판정: **PROMOTED_AND_READY_FOR_BACKEND**

## 11. 산출물

- 인계 문서: [`BACKEND_HANDOFF_20261011.md`](BACKEND_HANDOFF_20261011.md) 외 링크된 문서 7개
- 롤백: [`ROLLBACK_20261011.md`](ROLLBACK_20261011.md)
- 검증 기록·스크립트: `backups/local/20261011_pre_2026q1h1_v14/` (`*_check.json`, `service_smoke.json`, `tests_final/`, `scripts/`)
