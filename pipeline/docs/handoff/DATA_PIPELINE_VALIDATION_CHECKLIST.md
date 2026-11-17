# 데이터 파이프라인 검증 체크리스트 (5개사 운영 기준)

작성일: 2026-10-06 (16개사 확장 취소 후 5개사·MySQL 기준으로 재작성)
용도: 데이터를 **다시 만들거나 갱신한 뒤, MySQL에 동기화하기 전에** 사람이 확인하는 체크리스트

## 0. 사용 방법

- 데이터 작업(수집·임베딩 등)이 **완전히 끝난 뒤**에만 사용한다. 진행 중인 DB·FAISS는 열지 않는다.
- 모든 확인은 **읽기 전용**이다. SQLite는 `file:<경로>?mode=ro` URI로 연다.
- 경로: Financial `db/financial/financial.db`, Vector `db/vector/filings_bge_m3.db`, FAISS `db/vector/filings_bge_m3.faiss`, MySQL `dart_big_in`
- 각 항목의 "기준값"은 2026-10-06 운영 데이터(`2026-10-06-dart5-v1`)의 값이다. 데이터를 의도적으로 바꾼 경우가 아니라면 같아야 한다.
- `✔ 10-06`: 2026-10-06에 이미 확인된 항목. 데이터를 다시 만들면 다시 확인한다.

### 기준값 (2026-10-06)

| 항목 | 값 |
|---|---|
| 기업 | 5개: 삼성전자(00126380), SK하이닉스(00164779), 한미반도체(00161383), 주성엔지니어링(00252135), 원익IPS(01135941) |
| 기간 | 2023~2025 × Q1/H1/Q3/FY = 12, 슬롯 60 |
| 정기공시 | 60건 |
| `financial_facts` | 10,849행 (삼성전자 2,225 / SK하이닉스 1,975 / 한미반도체 2,213 / 주성엔지니어링 2,202 / 원익IPS 2,234) |
| `capex_facts` | 120행, 기본 60슬롯: direct 48 / derived 12 / unresolved 0, review_required 0 |
| `account_mapping` | 668행 |
| BGE 청크·벡터 | 21,797개 = FAISS `ntotal`, 1024차원, `vector_id` 0~21,796 |
| 모델 | `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181` |

---

## A. 기업 범위

- [ ] 기업 수가 **5**개다 — Financial `SELECT COUNT(*) FROM companies;`, Vector `SELECT COUNT(DISTINCT corp_code) FROM filing_chunks WHERE is_searchable=1;` ✔ 10-06
- [ ] `corp_code`·`stock_code`가 위 기준값과 같고 중복이 없다. ✔ 10-06
- [ ] Financial DB와 Vector DB의 `(기업명, corp_code)` 쌍이 같다.
- [ ] 취소된 16개사 데이터가 운영 DB·FAISS·MySQL에 **없다**. ✔ 10-06
- [ ] 코드 기대값과 일치: `src/config.py` `DEFAULT_COMPANIES`(5개) = `operational_config.EXPECTED_BGE_COMPANIES`. ✔ 10-06

## B. 공시

- [ ] 슬롯 60개 모두 정기공시가 있다(MySQL `coverage`에서 `filing_exists=1` 60건). ✔ 10-06
- [ ] 슬롯마다 공시가 1건이다.
  `SELECT corp_code, year, report_type, COUNT(DISTINCT rcept_no) n FROM filing_chunks GROUP BY 1,2,3 HAVING n>1;` → 0행 ✔ 10-06
- [ ] 정정공시가 있는 슬롯은 최신 접수본이 선택됐다.
- [ ] `rcept_no`가 모두 14자리이고 앞 8자리 날짜가 보고기간 이후다(예: 2024 FY → 2025년 접수).
- [ ] 같은 슬롯의 Financial `rcept_no` = Vector `rcept_no`(동기화 스크립트가 다르면 중단함). ✔ 10-06 (60/60)

## C. Financial

- [ ] `PRAGMA integrity_check` = `ok` ✔ 10-06
- [ ] 행 수가 기준값과 같다(기업별 포함). ✔ 10-06
- [ ] UNIQUE 복합키 중복 0건, `ord` NULL 0건. ✔ 10-06
- [ ] `fs_div`가 모두 `CFS`다(현재 5개사). OFS가 나오면 원인을 확인한다. ✔ 10-06
- [ ] 기간 의미가 섞이지 않았다: `BS`=`point_in_time`, `IS/CIS` 분기=`quarterly`+`cumulative`, FY=`annual`, `CF` 분기=`cumulative`(+계산 `quarterly`).
  `SELECT sj_div, p.report_type, value_type, is_calculated, COUNT(*) FROM financial_facts f JOIN periods p USING(period_id) GROUP BY 1,2,3,4;`
- [ ] 계산값(`is_calculated=1`)은 모두 `calculation_method`와 `source_values`가 있다. 샘플 3건 손계산(H1 분기 = H1 누적 − Q1 누적).
- [ ] 24개 표준계정이 5개사 모두에서 조회된다(MySQL `standardized_financials`에서 기업×표준계정 표 확인).

## D. CAPEX

- [ ] 기본 행 `value_source` 분포가 direct 48 / derived 12 / unresolved 0이다. ✔ 10-06
- [ ] 모든 행에 `value_source`, `source_values`, `calculation_method`, `source_account_names`가 있다.
- [ ] derived 구성에 무형자산·소프트웨어·회원권·투자부동산·영업권이 없다.
- [ ] direct 계정이 있는 슬롯에서 구성계정 합산을 같이 쓰지 않았다(이중 계산 없음).
- [ ] 분기 CAPEX가 음수인 행은 `review_required=1`이다(현재 0건).
- [ ] 회귀 기준 사례 ✔ 10-06 (MySQL `dart_backend`로 조회)
  - [ ] 원익IPS 2024 FY: direct, 21,432,773,671원, `rcept_no=20250325000491`
  - [ ] 주성엔지니어링 2024 H1: derived 누적 11,066,442,727원, 2분기 단독 5,023,179,778원

## E. Vector

- [ ] SQLite 검색 가능 행 = FAISS `ntotal` = 21,797, `vector_id` 0~21,796. ✔ 10-06
- [ ] `chunk_id` 중복 0건, `vector_id` 중복 0건. ✔ 10-06
- [ ] FAISS ID와 SQLite ID가 양방향으로 일치(orphan·누락 0건). (Codex 보고 10-06)
- [ ] FAISS 차원 1024, embedding BLOB 4096 bytes.
- [ ] 모든 행 모델 `BAAI/bge-m3`, revision `5617a9f6…`, 차원 1024. ✔ 10-06
- [ ] `vector_metadata`: `normalize_embeddings=true`, `faiss_index_type=IndexIDMap2(IndexFlatIP)`, `chunk_version=bge-m3-token-v2`. ✔ 10-06
- [ ] `validate_bge_operational_assets()`가 `status: PASS`, 기업 5개를 반환한다.

## F. 검색

대표 질의 3개 이상(예: `retrieve_filing_context("주요 위험", corp_name="SK하이닉스", year=2024, report_type="FY")`)으로 확인한다.

- [ ] 기업·`corp_code`·연도·`report_type`·`rcept_no` 필터 결과가 모두 조건과 같다(다른 기업·연도 혼입 0건). (Codex 보고 10-06)
- [ ] 결과의 `chunk_id`, `vector_id`, `rcept_no`, `section_name`, `chunk_text`가 비어 있지 않다.
- [ ] `score`가 유한하고 내림차순이며, `score == dense_score`(pure dense).
- [ ] `effective_retriever = bge_m3_dense`, `fallback_reason = None`. (Codex 보고 10-06)
- [ ] `FILING_RETRIEVER=feature_hashing_hybrid`로 legacy 경로가 동작한다(rollback 경로).
- [ ] `analyze_company(..., llm_client=None)`에서 `financial_sources`, `filing_sources`가 채워지고 공시 `rcept_no`가 재무 `rcept_no`와 같다.

## G. 회귀

- [ ] Financial·CAPEX 값이 직전 데이터 버전과 같다(의도한 변경 제외). (Codex 보고 10-06)
- [ ] 기존 chunk(`chunk_id`, `content_hash`)와 vector(`vector_id`, embedding)가 바뀌지 않았다.
- [ ] 단위 테스트 통과:
  ```bash
  .venv/bin/python -m unittest tests.test_operational_retriever tests.test_bge_m3_pipeline
  ```
  ```bash
  .venv/bin/python -m unittest tests.test_bge_m3_faiss tests.test_capex_derivation tests.test_final_database tests.test_quarterly_trend
  ```
- [ ] 과거 benchmark 수치를 현재 운영 성능으로 표현하지 않는다.

## H. MySQL 동기화

- [ ] 위 A~G 통과 후 dry-run:
  ```bash
  .venv/bin/python infra/mysql/sync_sqlite_to_mysql.py --data-version <YYYY-MM-DD-이름-vN> --dry-run
  ```
  축소 감지 단계에서 "원천 행 수 / MySQL 활성 행 수"가 예상과 같은지 본다.
- [ ] 실제 동기화 후 마지막 줄이 `PASS: … 현재 버전으로 지정`이다. ✔ 10-06 (`2026-10-06-dart5-v1`)
- [ ] `SELECT data_version, validated, is_current FROM data_versions;` → 새 버전만 `is_current=1`, `validated=1`. ✔ 10-06
- [ ] `SELECT table_name, status FROM sync_runs WHERE data_version = '<버전>';` → 모두 `success`. ✔ 10-06
- [ ] `SELECT status, COUNT(*) FROM coverage GROUP BY status;` → 전부 `PASS`(현재 60). ✔ 10-06
- [ ] 같은 버전으로 한 번 더 실행해도 행 수가 바뀌지 않는다(멱등). ✔ 10-06
- [ ] 활성 행이 줄었다면(`is_active=0` 증가) 의도한 변경인지 확인했다.

## I. 백엔드 연결 전

- [ ] `docs/handoff/BACKEND_INTERFACE_SPEC.md`, `DATA_DICTIONARY.md`가 현재 스키마·함수와 일치한다(10-06 개정).
- [ ] 백엔드 계정 `dart_backend`가 SELECT만 가능하다(쓰기·삭제 거부). ✔ 10-06
- [ ] 동기화 계정 `dart_etl` 비밀번호를 백엔드 담당자에게 주지 않았다.
- [ ] MySQL 원격 접속을 열 경우 방화벽 허용 범위를 팀 네트워크로 제한했다(현재 **미개방**).
- [ ] MySQL 백업 방법을 정했다(현재 **미구성**).
- [ ] `.env`, 비밀번호, API 키가 Git·문서·채팅·로그에 없다(`git status`에 `.env`가 없는지 확인).
- [ ] 백엔드가 읽는 경로가 운영 경로(`db/financial/financial.db`, `db/vector/filings_bge_m3.*`)이고 `db/archive/*`, `financial_final.db`, `financial_capex_v2.db`가 아니다.
- [ ] `python -m src.pipeline`을 다시 실행하면 `financial.db`가 통째로 다시 만들어진다는 점을 팀에 공유했다.

---

## 검증 결과 기록란

| 섹션 | 결과(PASS/FAIL/보류) | 확인자 | 날짜 | 메모 |
|---|---|---|---|---|
| A. 기업 범위 | | | | |
| B. 공시 | | | | |
| C. Financial | | | | |
| D. CAPEX | | | | |
| E. Vector | | | | |
| F. 검색 | | | | |
| G. 회귀 | | | | |
| H. MySQL 동기화 | PASS | Claude Code | 2026-10-06 | `2026-10-06-dart5-v1` 초기 적재·재실행 확인 |
| I. 백엔드 연결 전 | 보류 | | | 원격 접속·백업 미구성 |
