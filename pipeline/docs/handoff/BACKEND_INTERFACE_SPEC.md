# 백엔드 연동 인터페이스 명세

작성일: 2026-10-06 (5개사 확정·MySQL 적재 반영 개정)
대상 독자: 백엔드·프론트엔드 담당자, 데이터 담당자

## 0. 이 문서를 읽기 전에

- **운영 범위는 기존 5개 기업으로 확정**됐다: 삼성전자, SK하이닉스, 한미반도체, 주성엔지니어링, 원익IPS.
  2026-10-06에 시도한 16개사 추가 확장은 **취소**되었고, 데이터·코드는 확장 전 검증본으로 복원됐다.
- 현재 데이터 기준(데이터 버전 `2026-10-06-dart5-v1`, 검증 PASS):

  | 항목 | 값 |
  |---|---|
  | 기업 | 5개 (모두 연결재무제표 CFS) |
  | 기간 | 2023~2025 × Q1/H1/Q3/FY = 12개, 기업×기간 슬롯 60개 |
  | 정기공시 | 60건 (슬롯마다 1건, 재무와 공시의 `rcept_no` 일치) |
  | `financial_facts` | 10,849행 |
  | `capex_facts` | 120행 (기본 60슬롯: direct 48, derived 12, unresolved 0) |
  | 공시 청크 / 검색 벡터 | 21,797개 (SQLite = FAISS, 1024차원) |

- 데이터는 **두 곳**에서 읽을 수 있다.
  1. **중앙 MySQL `dart_big_in`** (신규): 재무·CAPEX·공시·청크 메타데이터·coverage. 백엔드는 읽기 전용 계정 `dart_backend` 사용. → 2.3절
  2. **기존 Python 함수 + SQLite/FAISS**: 프로젝트 앱의 공시 의미 검색 경로.
  3. **MySQL 벡터 + 팀원 PC의 BGE-M3** (2026-10-06 추가): MySQL `chunk_embeddings`에 벡터 21,797개가 있어, `infra/mysql/team_client/dart_semantic_search.py`로 팀원 PC에서도 같은 의미 검색을 할 수 있다(프로젝트 검색과 상위 10개 순서 일치 확인).
- 문서에서 **[현재 구현]** 은 지금 실제로 쓸 수 있는 함수·테이블, **[권장 인터페이스]** 는 앞으로 FastAPI 등에 붙일 때 쓰자고 제안하는 계약이다. 권장 인터페이스는 아직 **구현되지 않았다**.
- HTTP API endpoint는 현재 프로젝트에 **없다**. 현재 유일한 화면은 Streamlit(`app/app.py`)이다.
- 위 수치는 데이터 갱신 시 바뀐다. 최신 값은 MySQL `data_versions`(`is_current=1`)와 `sync_runs`에서 확인한다.

## 1. 현재 호출 구조

```text
app/app.py (Streamlit)
  └─ src.llm.analyzer.analyze_company(...)
       ├─ src.retrieval.financial_retriever.get_financial_context(...)   → db/financial/financial.db
       │     └─ (CAPEX 질의일 때) _get_capex_context(...)                → capex_facts 테이블
       └─ src.retrieval.operational_retriever.retrieve_filing_context(...)
             ├─ bge_m3_dense (기본)  → src.retrieval.bge_m3_indexer.search_bge_m3(...)
             │                          → db/vector/filings_bge_m3.db + .faiss + BGE-M3 모델
             └─ feature_hashing_hybrid (legacy/rollback)
                                     → src.retrieval.vector_retriever.search_filing_context(...)
                                        → db/vector/filings.db
```

- Financial(숫자)과 Vector(공시 문장)는 **서로 다른 SQLite 파일**이다.
- 둘은 `corp_code`, `year`, `report_type`, `rcept_no`로 분석 단계에서 연결된다. SQLite 안에서 JOIN으로 연결되는 구조는 아니다.
- SQLite 데이터는 동기화 스크립트(`infra/mysql/sync_sqlite_to_mysql.py`)로 중앙 MySQL에 복사된다. MySQL 안에서는 두 데이터가 같은 DB에 있어 JOIN할 수 있다.

```text
SQLite(원본) ──sync──▶ MySQL dart_big_in (읽기용 사본)
  financial.db            companies, periods, filings, financial_facts, capex_facts,
  filings_bge_m3.db       account_mapping, standard_accounts, standard_account_mapping,
                          chunk_metadata, chunk_embeddings(벡터), embedding_metadata, coverage,
                          data_versions, sync_runs, VIEW standardized_financials
filings_bge_m3.faiss + BGE-M3 모델 ── MySQL에 넣지 않음(벡터 값만 chunk_embeddings로 복사, 모델은 각자 PC)
```

**권장 연결 방식**
- 목록·재무·CAPEX·coverage·공시 근거 조회 → **MySQL** (SELECT 전용 계정)
- 공시 의미 검색(질문 → 관련 문단) → 데이터 담당자 PC: **Python `retrieve_filing_context`** / 팀원 PC: **`team_client/dart_semantic_search.py`**(MySQL 벡터 + 로컬 BGE-M3, 동일 결과)
- 검색 결과의 `chunk_id`로 MySQL `chunk_metadata`를 조회해 근거 원문을 보여 줄 수 있다.

## 2. 공통 규칙

### 2.1 식별자

| 항목 | 타입 | 규칙 |
|---|---|---|
| `corp_code` | string(8) | DART 기업코드. 앞자리 0을 지키기 위해 **항상 문자열**로 다룬다. |
| `corp_name` | string | 현재 함수들은 기업명을 **정확히 일치**하는 문자열로 필터한다(`삼성전자`, `SK하이닉스` 등). 띄어쓰기·영문 대소문자가 다르면 결과가 비어 있다. |
| `stock_code` | string(6) | 상장 종목코드. Financial DB `companies` 테이블에만 있다. |
| `year` | int | 사업연도(공시 제출연도 아님). 현재 범위 2023~2025. |
| `report_type` | enum | `Q1`, `H1`, `Q3`, `FY`. Financial 조회 함수는 `HY`, `HALF`, `1H`를 `H1`로 바꿔 준다. 검색 함수는 바꿔 주지 않는다. |
| `rcept_no` | string(14) | DART 접수번호. 재무 숫자와 공시 원문을 묶는 핵심 키. |
| `fs_div` | enum | `CFS`(연결) 또는 `OFS`(별도). |

### 2.2 권장 응답 공통 형식 (제안)

현재 함수는 Python `list[dict]` 또는 dataclass를 반환한다. HTTP로 노출할 때는 아래 형태로 감싸기를 권장한다.

```json
{
  "data": [],
  "meta": {
    "data_version": "추후 정의(MYSQL_CENTRALIZATION_PLAN.md 참고)",
    "source": "financial_db | vector_db",
    "retriever": "bge_m3_dense",
    "count": 0
  },
  "error": null
}
```

권장 오류 코드(제안):

| 코드 | 의미 | 현재 코드에서 발생하는 예외 |
|---|---|---|
| `INVALID_ARGUMENT` | 잘못된 입력 | `ValueError` (top_k ≤ 0, 지원하지 않는 retriever, 잘못된 analysis_period 등) |
| `NOT_FOUND` | 조건에 맞는 데이터 없음 | 예외 없음. 현재는 **빈 리스트** 반환 |
| `ASSET_UNAVAILABLE` | DB/FAISS/모델 파일 없음 또는 검증 실패 | `FileNotFoundError`, `RuntimeError` (BGE 운영 자산 검증) |
| `INTERNAL` | 기타 | `sqlite3.OperationalError` 등 |

> 주의: 현재 코드는 "데이터 없음"과 "필터 값 오타"를 구분하지 않는다. 둘 다 빈 리스트다. 백엔드에서 먼저 `기업 목록`·`존재 여부` 조회로 입력을 검증하는 것을 권장한다.

### 2.3 중앙 MySQL 접속 [현재 구현]

| 항목 | 값 |
|---|---|
| 서버 | 데이터 담당자 PC의 Windows MySQL Community Server 8.0, 포트 3306 |
| DB | `dart_big_in` (utf8mb4) |
| 백엔드 계정 | `dart_backend` — **SELECT 전용**(쓰기·삭제 거부 확인) |
| 동기화 계정 | `dart_etl` — 데이터 담당자 전용, 백엔드에 주지 않음 |
| 현재 데이터 버전 | `SELECT * FROM data_versions WHERE is_current = 1;` |
| 원격 접속 | **아직 열지 않음**(방화벽). 팀원 접속은 데이터 담당자가 연 뒤 접속 정보를 별도 전달 |

환경변수 예시(실제 값은 문서·Git에 넣지 않는다):

```bash
MYSQL_HOST=<server-ip>
MYSQL_PORT=3306
MYSQL_DATABASE=dart_big_in
MYSQL_USER=dart_backend
MYSQL_PASSWORD=<secret>
```

- Python에서 접속하려면 MySQL 드라이버(예: `pymysql`, `mysql-connector-python`)가 필요하다. 현재 프로젝트 `requirements.txt`에는 없다.
- SQL은 항상 파라미터 바인딩(`%s`)으로 실행한다. 문자열 이어 붙이기 금지.
- 모든 사실 테이블에 `is_active`가 있다. 조회 시 `is_active = 1` 조건을 붙인다(동기화 때 사라진 행은 지우지 않고 0으로 표시됨).
- MySQL에서는 기간이 `period_id`가 아니라 **`year` + `report_type` 컬럼**으로 바로 들어 있다(SQLite와 다름).

---

## 3. 기능별 인터페이스

### 3.1 기업 목록 조회

| 항목 | 내용 |
|---|---|
| 목적 | 화면의 기업 선택 목록, 입력값 검증 |
| 현재 연결 가능한 실제 함수 | ① **MySQL `companies` 테이블** (권장) ② `src.config.DEFAULT_COMPANIES` (기업명 tuple, 코드 상수) ③ `src.retrieval.bge_m3_indexer.validate_bge_operational_assets()["companies"]` (BGE 인덱스 검증 후 기업명 목록) |
| 향후 권장 함수명 | `list_companies()` |
| 데이터 출처 | MySQL `companies` (원천: Financial DB `companies`) |

[현재 구현] MySQL:

```sql
SELECT corp_code, corp_name, stock_code FROM companies WHERE is_operational = 1 ORDER BY corp_name;
```

현재 운영 5개사(Financial DB 기준):

| corp_name | corp_code | stock_code |
|---|---|---|
| 삼성전자 | 00126380 | 005930 |
| SK하이닉스 | 00164779 | 000660 |
| 한미반도체 | 00161383 | 042700 |
| 주성엔지니어링 | 00252135 | 036930 |
| 원익IPS | 01135941 | 240810 |

[권장 인터페이스]

```python
list_companies(include_coverage: bool = False) -> list[dict]
```

| 입력 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `include_coverage` | 선택 | bool | True면 Financial/Vector 존재 여부를 함께 반환 |

| 출력 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| `corp_name` | string | N | 기업명 |
| `corp_code` | string | N | DART 기업코드 |
| `stock_code` | string | Y | 상장 종목코드 |
| `in_financial_db` | bool | N | Financial DB `companies`에 있는지 |
| `in_vector_index` | bool | N | BGE `filing_chunks`(is_searchable=1)에 있는지 |

예시 요청 / 응답 (값은 형식 예시):

```python
list_companies()
```

```json
[
  {"corp_name": "삼성전자", "corp_code": "00126380", "stock_code": "005930",
   "in_financial_db": true, "in_vector_index": true}
]
```

오류 조건: DB 파일 없음 → `ASSET_UNAVAILABLE`.
주의사항:
- 기업명은 DART 기업코드 원본에서 **상장 레코드가 하나인 것**을 선택하는 방식(`DartClient.resolve_companies`)으로 확정된다. 동명 법인이 여럿이면 오류를 낸다.
- 기업 식별은 기업명이 아니라 `corp_code`로 한다(사명 변경 대비).

---

### 3.2 기업 coverage 조회

| 항목 | 내용 |
|---|---|
| 목적 | 기업별로 어떤 연도·보고서의 재무/공시/벡터가 있는지 보여 주기 |
| 현재 연결 가능한 실제 함수 | **MySQL `coverage` 테이블** (동기화 시 Financial DB·BGE DB에서 계산해 적재). Python 함수는 없음 |
| 향후 권장 함수명 | `get_company_coverage(corp_code=None, corp_name=None, years=None)` |
| 데이터 출처 | MySQL `coverage` (원천: Financial DB `financial_facts`·`capex_facts`, BGE DB `filing_chunks`) |

[현재 구현] MySQL:

```sql
SELECT c.corp_name, v.year, v.report_type, v.rcept_no, v.financial_exists, v.fs_div,
       v.filing_exists, v.embedding_exists, v.capex_status, v.status, v.reason
FROM coverage v JOIN companies c USING (corp_code)
WHERE v.corp_code = %s
ORDER BY v.year, FIELD(v.report_type, 'Q1', 'H1', 'Q3', 'FY');
```

현재 상태: 60슬롯 모두 `PASS`, `capex_status` direct 48 / derived 12.

[권장 인터페이스]

| 입력 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `corp_code` 또는 `corp_name` | 둘 중 하나 필수 | string | 기업 |
| `years` | 선택 | list[int] | 기본: 2023~2025 |

| 출력 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| `corp_code`, `corp_name` | string | N | |
| `year` | int | N | |
| `report_type` | string | N | Q1/H1/Q3/FY |
| `rcept_no` | string | Y | 해당 슬롯의 정기공시 접수번호. 원천 부재면 null |
| `financial_exists` | bool | N | Financial facts 존재 여부 |
| `fs_div` | string | Y | 해당 슬롯에서 사용한 CFS/OFS |
| `filing_exists` | bool | N | 공시 원문/청크 존재 여부 |
| `embedding_exists` | bool | N | 검색 가능 벡터 존재 여부 |
| `capex_status` | string | Y | `direct` / `derived` / `unresolved`(재무는 있으나 CAPEX 산출 불가) |
| `status` | string | N | `PASS` / `MISSING_OR_REVIEW` / `SOURCE_ABSENT`(DART 원천 부재) |
| `reason` | string | Y | 누락 사유 |

예시 응답:

```json
[
  {"corp_name": "원익IPS", "year": 2024, "report_type": "FY", "rcept_no": "20250325000491",
   "financial_exists": true, "fs_div": "CFS", "filing_exists": true, "embedding_exists": true,
   "capex_status": "direct", "status": "PASS", "reason": null}
]
```

주의사항:
- 운영 범위는 5개사 × 3년 × 4개 보고서 = 60슬롯이다.
- `SOURCE_ABSENT`는 MySQL 스키마에 값으로 정의돼 있지만, 현재 동기화 스크립트는 `PASS` / `MISSING_OR_REVIEW`만 기록한다(원천 부재 슬롯이 현재 없음).

---

### 3.3 Financial 조회

| 항목 | 내용 |
|---|---|
| 목적 | 특정 기업·연도·보고서의 재무 숫자 조회 |
| 현재 연결 가능한 실제 함수 | ① **MySQL VIEW `standardized_financials`** (24개 표준계정 + CAPEX, `value` 단일 컬럼) / MySQL `financial_facts`(전체 계정) ② Python `src.retrieval.financial_retriever.get_financial_context` |
| 향후 권장 함수명 | `get_financial_metrics` |
| 데이터 출처 | MySQL `financial_facts`·`capex_facts` / `db/financial/financial.db` |

[현재 구현 ①] MySQL — 백엔드 권장 경로:

```sql
SELECT corp_name, year, report_type, rcept_no, fs_div, sj_div, standard_account,
       value_type, value, is_calculated, value_source, calculation_method, source_values
FROM standardized_financials
WHERE corp_code = %s AND year = %s AND report_type = %s
  AND standard_account IN ('매출액', '영업이익')
ORDER BY standard_account, value_type;
```

- `value`는 이미 `calculated_value`(계산값) 또는 `normalized_value`(직접값) 중 맞는 것을 고른 값이다. CAPEX 행은 양수 지출 규모(`cash_outflow_amount`)다.
- VIEW는 `standard_accounts`(24개)에 있는 계정만 보여 준다. 그 밖의 원본 계정은 `financial_facts`를 직접 조회한다.
- 같은 계정이 IS와 CIS에 둘 다 있으면 행이 2개 나올 수 있다(`sj_div`로 구분).

[현재 구현 ②] Python 함수:

```python
get_financial_context(
    corp_name: str,
    year: int,
    report_type: str,
    account_name: str | None = None,
    fs_div: str = "CFS",
    path: Path | None = None,       # 기본 db/financial/financial.db
) -> list[dict]
```

| 입력 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `corp_name` | 필수 | str | `companies.company_name`과 정확히 일치 |
| `year` | 필수 | int | |
| `report_type` | 필수 | str | Q1/H1/Q3/FY (HY/HALF/1H 허용) |
| `account_name` | 선택 | str | `standard_account_nm` 또는 `original_account_nm`에 **부분 일치(LIKE '%값%')**. CAPEX 별칭이면 CAPEX 경로로 분기 |
| `fs_div` | 선택 | str | 기본 `CFS` |
| `path` | 선택 | Path | 테스트용 DB 경로 |

출력 필드(일반 계정, 한 행 = 한 재무 값):

| 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| `corp_name`, `corp_code`, `year`, `report_type`, `rcept_no` | | rcept_no는 Y | 식별 |
| `sj_div` | str | N | BS/IS/CIS/CF/SCE |
| `account_id` | str | Y | DART/XBRL 계정 ID |
| `original_account_nm` | str | N | 원본 계정명 |
| `standard_account_nm` | str | Y | 표준 계정명(주의사항 참고) |
| `period_type`, `value_type` | str | N | point_in_time / quarterly / cumulative / annual |
| `period_start` | str(date) | Y | BS는 null |
| `period_end` | str(date) | N | |
| `currency` | str | Y | 보통 KRW |
| `raw_value` | str | Y | 원본 문자열 |
| `normalized_value` | int | Y | 직접 제공값(원 단위) |
| `calculated_value` | int | Y | 계산값(누적 차감 등) |
| `calculation_method` | str | Y | 계산 산식 |
| `source_values` | str(JSON) | Y | 계산에 쓴 값 |
| `is_calculated` | int(0/1) | N | 1이면 `calculated_value` 사용 |
| `fs_div` | str | N | CFS/OFS |

**실제 값 선택 규칙**: `value = calculated_value if is_calculated == 1 else normalized_value` (analyzer와 동일).

[권장 인터페이스]

```python
get_financial_metrics(
    corp_name: str | None = None,
    corp_code: str | None = None,        # 둘 중 하나 필수, corp_code 우선
    year: int = ...,
    report_type: str = ...,               # Q1/H1/Q3/FY
    value_type: str | None = None,        # quarterly/cumulative/annual/point_in_time
    standard_accounts: list[str] | None = None,  # 24개 표준계정명, 정확 일치
    fs_div: str | None = None,            # None이면 해당 슬롯에 존재하는 fs_div 자동 선택(CFS 우선)
) -> list[dict]
```

권장 출력 필드: `corp_name, corp_code, year, report_type, rcept_no, standard_account, account_id, account_name, sj_div, fs_div, period_type, value_type, period_start, period_end, value, raw_value, is_calculated, value_source, source_values, calculation_method, currency`.

예시 요청 / 응답:

```python
get_financial_metrics(corp_name="원익IPS", year=2024, report_type="FY",
                      standard_accounts=["매출액"])
```

```json
[
  {"corp_name": "원익IPS", "year": 2024, "report_type": "FY",
   "rcept_no": "20250325000491", "standard_account": "매출액",
   "sj_div": "CIS", "fs_div": "CFS", "value_type": "annual",
   "value": 0, "is_calculated": false, "value_source": "reported",
   "calculation_method": null}
]
```

(`value: 0`은 자리표시. 실제 값은 DB에서 확인해야 한다. `rcept_no`는 기존 `CSV_GUIDE.md`에 기재된 원익IPS 2024 FY 접수번호를 예시로 사용.)

오류 조건: 입력 누락 → `INVALID_ARGUMENT`; DB 없음 → `ASSET_UNAVAILABLE`; 결과 없음 → 빈 리스트.

**주의사항 (중요)**
1. **기본 `fs_div="CFS"`**: 현재 5개사는 모두 CFS만 있어 문제가 없다. 다만 함수와 `analyze_company`가 `fs_div`를 넘기지 않으므로, 앞으로 **별도재무제표(OFS)만 있는 기업을 추가하면 재무 결과가 비어 나온다.** 기업 추가 시 함께 고쳐야 한다.
2. **부분 일치 검색**: `account_name="매출"`은 `매출액`, `매출원가`, `매출채권`, `매출총이익`을 모두 가져온다. 권장 인터페이스는 표준계정명 **정확 일치**를 제안한다.
3. **`standard_account_nm`의 범위**: 운영 5개사는 메인 파이프라인의 검증된 매핑을 사용한다. "24개 표준계정만" 보여 주려면 3.4의 표준계정 목록으로 거른다.
4. 기업 필터가 `company_name`(기업명)이다. 권장 인터페이스는 `corp_code` 필터를 우선한다.
5. 현재 함수는 SQLite를 **쓰기 가능 모드**로 연다(`sqlite3.connect(path)`). 파일이 없으면 빈 DB 파일이 만들어질 수 있다. 백엔드는 `file:...?mode=ro` 읽기 전용 연결을 권장한다.
6. 일반 계정 행에는 `value_source` 키가 **없다**. analyzer는 없으면 `reported`로 표시한다. `financial_facts_final` VIEW는 `'reported'`를 명시적으로 넣는다.

---

### 3.4 표준계정 조회

| 항목 | 내용 |
|---|---|
| 목적 | 24개 핵심 표준계정 목록과 정의 제공(화면 드롭다운, 입력 검증) |
| 현재 연결 가능한 실제 함수 | ① **MySQL `standard_accounts`**(24행), `standard_account_mapping`(78행, 원본 계정 ↔ 표준계정) ② 상수 `src.quality.account_mapping_quality.CORE_ACCOUNTS` |
| 향후 권장 함수명 | `list_standard_accounts()` |
| 데이터 출처 | 코드 상수 `CORE_ACCOUNTS` → MySQL 시드 |

[현재 구현] MySQL:

```sql
SELECT standard_account_nm, category, sj_divs, account_ids, aliases, period_nature
FROM standard_accounts ORDER BY display_order;
```

24개 표준계정(코드 기준):

| 분류 | 표준계정 | sj_div |
|---|---|---|
| 손익 | 매출액, 매출원가, 매출총이익, 영업이익, 당기순이익, 법인세비용차감전순이익, 연구개발비 | IS/CIS |
| 재무상태 | 자산총계, 부채총계, 자본총계, 유동자산, 유동부채, 이익잉여금, 현금및현금성자산, 유형자산, 무형자산, 재고자산, 매출채권 | BS |
| 현금흐름 | 영업활동현금흐름, 투자활동현금흐름, 재무활동현금흐름, 유형자산취득(CAPEX), 무형자산취득, 감가상각비 | CF |

권장 출력 필드: `standard_account`, `sj_divs`(list), `account_ids`(list), `aliases`(list), `period_nature`(`point_in_time` 또는 `flow`).

예시 응답:

```json
[{"standard_account": "유형자산취득(CAPEX)", "sj_divs": ["CF"],
  "account_ids": ["ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
  "aliases": ["유형자산의취득", "유형자산취득"], "period_nature": "flow"}]
```

주의사항:
- `연구개발비`는 account_id가 정의되어 있지 않고 계정명 alias로만 매핑된다.
- `유형자산`(BS, 잔액)과 `유형자산취득(CAPEX)`(CF, 기간 지출)은 다른 계정이다.
- `CORE_ACCOUNTS`는 `src/quality/` 아래 품질 스크립트에 있다. 백엔드가 import할 때 이 모듈이 pandas와 경로 상수를 함께 불러온다는 점에 유의(부작용은 없음).

---

### 3.5 CAPEX 조회

| 항목 | 내용 |
|---|---|
| 목적 | 설비투자(유형자산 취득 현금유출) 값과 산출 근거 제공 |
| 현재 연결 가능한 실제 함수 | ① **MySQL `capex_facts`** 또는 `standardized_financials`(`standard_account='유형자산취득(CAPEX)'`) ② `get_financial_context(corp_name, year, report_type, account_name="CAPEX")` → 내부 `_get_capex_context` |
| 향후 권장 함수명 | `get_capex(corp_code|corp_name, year, report_type, value_type=None, fs_div=None)` |
| 데이터 출처 | Financial DB `capex_facts` (빌드 시 `materialize_capex_tables`가 `financial_facts`에서 다시 만듦) → MySQL |

[현재 구현 ①] MySQL:

```sql
SELECT year, report_type, value_type, value_source, cash_outflow_amount, calculated_value,
       is_calculated, calculation_method, source_account_names, review_required, rcept_no
FROM capex_facts
WHERE corp_code = %s AND year = %s AND is_active = 1
ORDER BY FIELD(report_type, 'Q1', 'H1', 'Q3', 'FY'), value_type;
```

[현재 구현 ②] Python 함수:

CAPEX 경로로 들어가는 `account_name`: `CAPEX`, `설비투자`, `유형자산취득(CAPEX)`, `유형자산의 취득` 중 하나를 **포함**(대소문자 무시)하면 된다.

[현재 구현] 출력 필드(한 행 = 한 보고서의 한 value_type):

| 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| 공통 식별 필드 | | | corp_name, corp_code, year, report_type, rcept_no, sj_div(`CF`), fs_div |
| `normalized_value` | int | N | **양수 지출 규모**(= `cash_outflow_amount`). 원래 부호값이 아님에 주의 |
| `signed_normalized_value` | int | Y | 원본 부호값(보통 음수). 분기 계산 행은 null |
| `calculated_value` | int | Y | 분기 단독값(계산 행) |
| `value_type` | str | N | `quarterly` → `cumulative` → `annual` 순 정렬 |
| `value_source` | str | N | `direct` 또는 `derived` |
| `is_derived` | int | N | derived면 1 |
| `capex_method` | str | N | `direct_account` / `component_fallback` |
| `calculation_method` | str | N | 예: `direct: 유형자산의 취득`, 또는 구성계정 합 산식, 분기 차감 산식 |
| `source_account_ids`, `source_account_names` | str(JSON 배열) | N | 사용한 원천 계정 |
| `source_values` | str(JSON) | N | 원천 값 |
| `source_account_count` | int | N | |
| `direct_account_found` | int | N | |
| `review_required` | int | N | 1이면 사람 검토 필요(직접 계정 중복, 분기 차감이 음수 등) |
| `note` | str | Y | 설명 |

예시(기존 `CSV_GUIDE.md`에 기재된 사례):

```json
[
  {"corp_name": "원익IPS", "year": 2024, "report_type": "FY", "value_type": "annual",
   "value_source": "direct", "normalized_value": 21432773671, "rcept_no": "20250325000491"},
  {"corp_name": "주성엔지니어링", "year": 2024, "report_type": "H1", "value_type": "cumulative",
   "value_source": "derived", "normalized_value": 11066442727},
  {"corp_name": "주성엔지니어링", "year": 2024, "report_type": "H1", "value_type": "quarterly",
   "value_source": "derived", "is_calculated": 1, "calculated_value": 5023179778,
   "calculation_method": "H1 cumulative CAPEX - Q1 cumulative CAPEX [...]"}
]
```

(위 수치는 MySQL `standardized_financials`에서 백엔드 계정으로 조회해 확인한 값이다.)

오류 조건: `capex_facts` 테이블이 없으면 **빈 리스트**(예외 아님).

주의사항:
- **CAPEX unresolved**(직접 계정도 구성 계정도 없음)인 경우 `capex_facts`에 **행 자체가 없다.** "0원"이 아니라 "산출 불가"로 표시해야 한다.
- 분기 질문에는 `value_type="quarterly"`만 사용한다. H1·Q3·FY의 `cumulative/annual`을 분기값으로 쓰면 안 된다.
- `capex_facts`는 빌드할 때마다 DROP 후 재생성된다. `capex_id`는 안정적인 키가 아니다.

---

### 3.6 공시 검색

| 항목 | 내용 |
|---|---|
| 목적 | 질문과 의미가 가까운 공시 문단(청크) Top-K 검색 |
| 현재 연결 가능한 실제 함수 | `src.retrieval.operational_retriever.retrieve_filing_context` (운영 라우터, **권장 진입점**) / 하위: `bge_m3_indexer.search_bge_m3`, `vector_retriever.search_filing_context` |
| 향후 권장 함수명 | `search_filings` |
| 데이터 출처 | 기본: `db/vector/filings_bge_m3.db` + `db/vector/filings_bge_m3.faiss` + BGE-M3 모델 / legacy: `db/vector/filings.db` |

[현재 구현]

```python
retrieve_filing_context(
    query: str,
    *,
    corp_name: str | None = None,
    corp_code: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    rcept_no: str | None = None,
    top_k: int = 5,
    retriever: str | None = None,      # "bge_m3_dense" | "feature_hashing_hybrid"
    settings: FilingRetrieverSettings | None = None,
) -> FilingRetrievalResult(rows, requested_retriever, effective_retriever, fallback_reason)
```

| 입력 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `query` | 필수 | str | 질문. 내부에서 기업명·연도 문자열을 제거한 뒤 임베딩(`effective_query`) |
| `corp_name`/`corp_code`/`year`/`report_type`/`rcept_no` | 선택 | | **정확 일치** 필터. SQLite에서 먼저 후보를 거른 뒤 그 안에서만 점수 계산 |
| `top_k` | 선택 | int | 기본 5, 0 이하면 ValueError |
| `retriever` | 선택 | str | 없으면 환경변수 `FILING_RETRIEVER`, 그것도 없으면 `bge_m3_dense` |

`FilingRetrievalResult`:

| 필드 | 설명 |
|---|---|
| `rows` | 결과 리스트(아래) |
| `requested_retriever` | 요청한 방식 |
| `effective_retriever` | 실제 사용한 방식 |
| `fallback_reason` | 자동 fallback 시 사유, 아니면 null |

`rows` 한 행(BGE-M3 dense 기준):

| 필드 | 타입 | nullable | 설명 |
|---|---|---|---|
| `vector_id` | int | N | FAISS ID와 동일 |
| `chunk_id` | str | N | `{rcept_no}:v2:{section_index}:{chunk_index}` |
| `corp_name`, `corp_code`, `year`, `report_type`, `rcept_no` | | N | |
| `section_name` | str | N | 상위 목차 |
| `subsection_name` | str | Y | 하위 목차 |
| `source_file` | str | N | 원본 XML 프로젝트 상대경로 |
| `chunk_text` | str | N | 정제된 본문 |
| `content_hash`, `chunk_version`, `token_count`, `content_type`, `search_priority`, `selection_reason` | | N | 청크 메타 |
| `embedding_model`, `model_revision`, `embedding_dimension` | | N | `BAAI/bge-m3`, `5617a9f6…`, 1024 |
| `dense_score` | float | N | 정규화 내적(코사인 유사도) |
| `lexical_score` | float | N | 키워드 점수. **pure dense에서는 계산만 하고 최종 점수에 쓰지 않음** |
| `final_score` = `score` | float | N | 정렬 기준 점수 |
| `rank` | int | N | 1부터 |
| `retrieval_method` | str | N | `bge_m3_dense` 등 |
| `search_priority_applied`, `priority_multiplier` | | N | 운영 기본은 false / 1.0 |

legacy(`feature_hashing_hybrid`) 행에는 `vector_id`, `embedding_model`, `model_revision`, `content_type` 등이 **없다**. 백엔드는 이 키들을 nullable로 처리해야 한다. fallback 시 각 행에 `fallback_reason`이 추가된다.

[권장 인터페이스]

```python
search_filings(
    query: str,
    corp_code: str | None = None,
    corp_name: str | None = None,
    year: int | None = None,
    report_type: str | None = None,
    rcept_no: str | None = None,
    top_k: int = 5,
) -> {"rows": [...], "retriever": "...", "model": "...", "model_revision": "...", "fallback_reason": null}
```

권장 출력 행 필드: `corp_name, corp_code, year, report_type, rcept_no, section_name, subsection_name, source_file, chunk_id, vector_id, chunk_text, score, rank, retriever, model`.

예시 요청 / 응답:

```python
search_filings("2024년 주요 위험 요인", corp_name="SK하이닉스", year=2024, report_type="FY", top_k=3)
```

```json
{"rows": [{"corp_name": "SK하이닉스", "year": 2024, "report_type": "FY",
           "rcept_no": "<14자리>", "section_name": "<목차>", "chunk_id": "<rcept_no>:v2:<n>:<m>",
           "chunk_text": "...", "score": 0.0, "rank": 1, "retriever": "bge_m3_dense",
           "model": "BAAI/bge-m3"}],
 "retriever": "bge_m3_dense", "fallback_reason": null}
```

오류 조건:
- 지원하지 않는 retriever / top_k ≤ 0 → `INVALID_ARGUMENT`
- BGE 운영 자산 검증 실패(파일 없음, 모델/revision/차원 불일치, SQLite·FAISS 개수/ID 불일치, **기업 목록이 기대값 5개와 다름**) → `ASSET_UNAVAILABLE`. 자동 fallback은 기본 **꺼짐**(`FILING_RETRIEVER_AUTO_FALLBACK=false`)
- 필터 결과 없음 → 빈 리스트

주의사항:
- 점수는 retriever마다 척도가 다르다. **서로 다른 방식의 점수를 비교하거나 고정 임계값으로 쓰지 않는다.**
- 첫 호출 때 BGE-M3 모델을 로딩하므로 느리고 메모리를 많이 쓴다. 모델·FAISS는 프로세스 안에서 캐시된다. 웹 서버는 **워커 수만큼 모델이 메모리에 올라간다**는 점을 고려한다.
- 필터가 넓으면(예: 필터 없음) SQLite에서 후보 embedding을 모두 읽어 임시 인덱스를 만들므로 느려질 수 있다. 백엔드는 최소 `corp_code`(또는 `corp_name`) + `year` 필터를 권장한다.
- `validate_bge_operational_assets()`는 실제 기업 목록이 코드의 기대 목록(`EXPECTED_BGE_COMPANIES` = 5개)과 정확히 같아야 통과한다.
- `report_type` 별칭(HY 등)은 검색 함수에서 바꿔 주지 않는다.

---

### 3.7 공시 근거 조회

| 항목 | 내용 |
|---|---|
| 목적 | 답변에 인용한 청크의 원문·출처를 다시 보여 주기(근거 링크) |
| 현재 연결 가능한 실제 함수 | ① **MySQL `chunk_metadata`** (chunk_id·rcept_no로 바로 조회) ② `search_*` 결과 행에 원문 포함 ③ `analyze_company(...)["filing_sources"]`, `["filing_evidence"]` |
| 향후 권장 함수명 | `get_filing_evidence(chunk_id)`, `list_filing_chunks(rcept_no, section_name=None)` |
| 데이터 출처 | BGE DB `filing_chunks`(embedding 제외) → MySQL `chunk_metadata`, 공시 단위는 MySQL `filings` |

[현재 구현] MySQL:

```sql
-- 검색 결과 chunk_id로 근거 원문 보기
SELECT chunk_id, vector_id, corp_name, year, report_type, rcept_no,
       section_name, subsection_name, chunk_text, original_text
FROM chunk_metadata WHERE chunk_id = %s AND is_active = 1;

-- 특정 공시의 목차(섹션) 목록
SELECT section_name, subsection_name, COUNT(*) AS chunks
FROM chunk_metadata WHERE rcept_no = %s AND is_active = 1
GROUP BY section_name, subsection_name ORDER BY MIN(vector_id);
```

[권장 인터페이스]

| 입력 | 필수 | 타입 |
|---|---|---|
| `chunk_id` | 필수(get) | str |
| `rcept_no` | 필수(list) | str |
| `section_name` | 선택 | str |

| 출력 필드 | 타입 | nullable |
|---|---|---|
| `chunk_id`, `vector_id`, `rcept_no`, `corp_code`, `corp_name`, `year`, `report_type` | | N |
| `section_name` / `subsection_name` | str | N / Y |
| `chunk_text` | str | N |
| `original_text` | str | N |
| `source_file` | str | N |
| `dart_url` | str | N (제안: `https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}`) |

주의사항:
- `chunk_text`는 정제본, `original_text`는 파싱 원문이다. 화면 인용은 `chunk_text`, 감사·검증은 `original_text`를 권장.
- 청크끼리 약 100 토큰 겹친다(overlap). 여러 청크를 이어 붙이면 문장이 중복될 수 있다.
- `source_file`은 서버 내부 경로이므로 외부 사용자에게 그대로 노출하지 않는다.

---

### 3.8 기업/연도/보고서별 데이터 존재 여부 조회

| 항목 | 내용 |
|---|---|
| 목적 | 질문 전에 "이 조합의 데이터가 있는가"를 빠르게 확인 |
| 현재 연결 가능한 실제 함수 | **MySQL `coverage`** 한 행 조회(3.2와 같은 테이블). Python 함수는 없음 |
| 향후 권장 함수명 | `check_data_availability(corp_code|corp_name, year, report_type)` |
| 데이터 출처 | MySQL `coverage` |

[현재 구현] MySQL:

```sql
SELECT financial_exists, fs_div, capex_status, filing_exists, embedding_exists,
       rcept_no, financial_rcept_no, status, reason
FROM coverage WHERE corp_code = %s AND year = %s AND report_type = %s;
```

행이 없으면 운영 범위 밖(기업·연도·보고서 조합이 없음)이다.

권장 출력:

```json
{"corp_code": "<8자리>", "year": 2023, "report_type": "Q1",
 "financial": {"exists": true, "fs_div": ["CFS"], "rcept_no": "<14자리>"},
 "capex": {"exists": true, "value_source": "direct"},
 "filing": {"exists": true, "rcept_no": "<14자리>", "searchable_chunks": 0},
 "rcept_no_consistent": true}
```

- `rcept_no_consistent`: Financial의 `rcept_no`와 Vector의 `rcept_no`가 같은지. analyzer는 두 값이 같은 청크만 남기므로(아래 3.9), 다르면 공시 근거가 사라진다.
- `fs_div`는 리스트로 돌려준다(한 슬롯에 CFS·OFS가 함께 있을 수 있음).

---

### 3.9 (부록) 통합 분석 — 현재 화면이 쓰는 진입점

```python
src.llm.analyzer.analyze_company(
    query: str, corp_name: str, year: int | None = None, report_type: str = "FY",
    retrieval_method: str | None = None,
    llm_client: Callable[[str], str] | None = None,   # None이면 LLM 호출 없이 근거 미리보기
    analysis_period: str = "single_report",           # 또는 "quarterly_trend"
) -> dict
```

반환 주요 키: `route`(financial/filing/integrated), `years`, `financial_db_used`, `vector_db_used`, `requested_retrieval_method`, `effective_retrieval_method`, `retrieval_fallback_reason`, `answer`, `raw_answer`, `financial_evidence`, `filing_evidence`, `financial_sources`, `filing_sources`, `prompt`, `llm_called`, `postprocessing`.

동작 요약(코드 기준):
- 질문의 키워드로 경로를 정한다(숫자 키워드 → financial, 원인·전략 키워드 → filing, 둘 다 → integrated).
- 질문에 "변화/대비/전년"이 있으면 전년도도 조회한다.
- 재무와 공시를 모두 썼으면 **재무 `rcept_no`와 같은 공시 청크만 남긴다.**
- `fs_div`를 지정하지 않는다(항상 CFS). 현재 5개사는 문제없지만 OFS 기업 추가 시 3.3 주의사항 1을 참고한다.
- 이 함수는 MySQL이 아니라 **SQLite를 직접 읽는다.** MySQL 전환 후에도 당분간 그대로다.
- `src/llm/analyzer.py`는 아직 커밋되지 않은 변경이 있으므로, 연결 전에 최신 시그니처를 다시 확인한다.

백엔드가 LLM 답변까지 서버에서 만들 경우 이 함수를 감싸는 것이 가장 빠르다. 단, 프롬프트에 공시 원문이 들어가므로 외부 LLM 사용 정책을 확인해야 한다(`src/llm/gemini_client.py`는 `GEMINI_API_KEY`, `GEMINI_MODEL` 환경변수 사용).

---

## 4. 현재 구현과 권장 인터페이스 비교

| 기능 | 현재: MySQL | 현재: Python 함수 | 권장 인터페이스 |
|---|---|---|---|
| 기업 목록 | `companies` | `DEFAULT_COMPANIES`(기업명만) | `list_companies()` |
| coverage | `coverage` | 없음 | `get_company_coverage()` |
| Financial | `standardized_financials`, `financial_facts` | `get_financial_context`(기업명, LIKE, CFS 고정) | `get_financial_metrics` |
| 표준계정 | `standard_accounts`, `standard_account_mapping` | `CORE_ACCOUNTS` 상수 | `list_standard_accounts()` |
| CAPEX | `capex_facts` | `get_financial_context(..., "CAPEX")` | `get_capex()` |
| 공시 검색(의미) | `chunk_embeddings` 벡터 + 팀원 PC BGE-M3(`team_client/dart_semantic_search.py`) | `retrieve_filing_context` | `search_filings` |
| 근거 조회 | `chunk_metadata`, `filings` | 없음 | `get_filing_evidence`, `list_filing_chunks` |
| 존재 여부 | `coverage` | 없음 | `check_data_availability` |
| 접근 권한 | `dart_backend` SELECT 전용 | Financial SQLite를 rw 모드로 엶 | 모두 읽기 전용 |

## 5. 경로·환경변수(현재 코드 기준)

| 이름 | 위치 | 값 |
|---|---|---|
| `FINANCIAL_DB_PATH` | `src/config.py` | `db/financial/financial.db` |
| `LEGACY_VECTOR_DB_PATH` | `src/retrieval/operational_config.py` | `db/vector/filings.db` |
| `BGE_DB_PATH` | 〃 | `db/vector/filings_bge_m3.db` |
| `BGE_FAISS_PATH` | 〃 | `db/vector/filings_bge_m3.faiss` |
| `MODEL_CACHE` | `src/retrieval/bge_m3_indexer.py` | `db/vector/model_cache/bge_m3` |
| `FILING_RETRIEVER` | 환경변수 | `bge_m3_dense`(기본) / `feature_hashing_hybrid` |
| `FILING_RETRIEVER_AUTO_FALLBACK` | 환경변수 | `false`(기본) |
| `BGE_M3_DEVICE` | 환경변수 | `auto`(기본) / `cuda` / `cpu` / `mps` |
| `DART_API_KEY` | `.env` | 수집용. 백엔드 조회에는 불필요 |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | 환경변수 | LLM 호출 시에만 |
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD` | 환경변수(백엔드 측, 제안) | 2.3절 참고. 현재 프로젝트 코드는 이 변수를 읽지 않는다 |
| MySQL 동기화 비밀번호 | `infra/mysql/.env` | 데이터 담당자 PC 전용(Git 제외) |

모든 경로는 `PROJECT_ROOT` 기준으로 **코드에 하드코딩**되어 있다. 백엔드를 다른 서버/디렉터리에 배포하려면 경로를 환경변수로 바꿀 수 있게 하는 작업이 필요하다(제안, 미구현).

## 6. 추후 확인 필요

1. **MySQL 원격 접속 개방**: 현재 데이터 담당자 PC에서만 접속 가능. 방화벽 허용 범위·접속 정보 전달 방법 결정 필요.
2. **공시 의미 검색 제공 방식**: 백엔드를 모델·FAISS가 있는 서버에서 실행해 `retrieve_filing_context`를 import할지, 검색 전용 서비스를 따로 둘지 결정 필요.
3. OFS 전용 기업을 추가할 경우 `get_financial_context`/`analyze_company`의 `fs_div` 처리 수정 필요(현재 5개사는 해당 없음).
4. `mapping_method`, `review_status`는 Financial DB에 저장되지 않는다(`save_financial_data`가 저장 컬럼을 고정). 백엔드에 노출하려면 저장 위치를 정해야 한다.
5. `data_version`은 MySQL `data_versions`에만 있고 SQLite·Python 함수 쪽에는 없다. Python 경로 응답에도 버전을 붙이려면 추가 작업이 필요하다.
6. 커밋되지 않은 `app/app.py`, `src/llm/analyzer.py` 등의 변경 — 연결 전에 최신 시그니처 재확인.
7. 웹 서버 동시 요청 시 BGE-M3 모델 메모리·스레드(코드에서 OMP 1 스레드 강제) 영향 — 부하 측정 필요.
