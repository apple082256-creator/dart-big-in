# 백엔드 데이터 계약 (2026-10-11)

- 기준 버전: 재무 `2026-10-11-dart25-2026q1h1-v14`, 인덱스 `bge-m3-2026-10-11-dart25-2026q1h1-v14`
- 기준 DB: 중앙 MySQL `dart_big_in` (`KHP-SD1-03:3306`). 로컬 SQLite/FAISS는 같은 데이터의 원본이다.
- 테이블·컬럼 상세: [`DB_SCHEMA_20261011.md`](DB_SCHEMA_20261011.md),
  [`COLUMN_DICTIONARY_20261011.csv`](COLUMN_DICTIONARY_20261011.csv)
- 실행 가능한 SQL: [`BACKEND_QUERY_EXAMPLES_20261011.md`](BACKEND_QUERY_EXAMPLES_20261011.md)

## 1. 핵심 키

| 키 | 형식 | 의미 | 규칙 |
|---|---|---|---|
| `corp_code` | `CHAR(8)`, 예 `00126380` | DART 고유번호 | 모든 기업 조인의 기준. 기업명으로 조인하지 않는다 |
| `stock_code` | `CHAR(6)`, 예 `005930` | KRX 종목코드 | 표시·외부 시세 연동용. 25개사 모두 있음 |
| `year` | 정수, 예 `2026` | 사업연도 | 결산월 기준 연도 |
| `report_type` | `Q1` / `H1` / `Q3` / `FY` | 보고 기간 | Q1=1분기보고서(11013), H1=반기보고서(11012), Q3=3분기보고서(11014), FY=사업보고서(11011) |
| period | (`year`, `report_type`) | 기간 키 | MySQL `periods` PK. SQLite `period_id`는 내부 번호라 외부에 노출하지 않는다 |
| `fs_div` | `CFS` / `OFS` | 연결/별도 | 기업별로 하나만 쓴다(혼용 0). 에이치브이엠·카카오뱅크만 OFS |
| `rcept_no` | `CHAR(14)` | DART 접수번호 | 공시 1건을 식별. 정정공시가 있으면 최신 유효 정정본 번호 |
| `source_document_id` | 64자리 hex | 원문 XML 문서 ID | `SHA-256(rcept_no + '\0' + source_path)`. MySQL `filing_source_documents.document_id`와 같은 값. 파일 내용 해시(`sha256`)와 다르다 |
| `chunk_id` | `{rcept_no}:v2:{섹션}:{순번}` | 검색 단위 청크 ID | 예 `20260814003751:v2:13:15`. 앞 14자리가 rcept_no |
| `vector_id` | 정수 | FAISS 벡터 ID | `chunk_metadata.vector_id` = FAISS ID. 1:1 |
| `data_version` | 예 `2026-10-11-dart25-2026q1h1-v14` | 재무 데이터 버전 | `data_versions.is_current=1`인 버전이 현재 서비스 버전(정확히 1개) |
| `index_version` | 예 `bge-m3-2026-10-11-dart25-2026q1h1-v14` | 임베딩/FAISS 버전 | `embedding_metadata.is_current=1`(정확히 1개). 재무 버전과 독립 |
| `review_required` | KPI: `review_status='review_required'`, CAPEX: `review_required=1` | 확정 표시 전 주의 필요 | 6절 참고 |

### 기간 값의 종류 (`value_type`)

| value_type | 의미 | 예 |
|---|---|---|
| `cumulative` | 연초부터 누적 | H1 cumulative = 1~6월 |
| `quarterly` | 해당 분기 단독 | H1 quarterly = 4~6월(Q2). Q1은 cumulative와 같다 |
| `annual` | 연간(FY만) | |
| `point_in_time` | 기말 잔액(재무상태표) | 자산총계 |
| `ratio` | 공시에 비율로 적힌 값 | 헬스케어 `RND_TO_REVENUE` |

## 2. 연결 흐름

```
companies
  │ corp_code                                   (FK)
  ▼
financial_facts / financial_metric_values / capex_facts
  │ rcept_no  (financial_facts.rcept_no, metric.source_rcept_no, capex.rcept_no)   (logical relation)
  ▼
filings  (슬롯별 대표 공시, rcept_no PK)
  │ rcept_no                                    (FK: filing_source_documents.rcept_no → filings)
  ▼
filing_source_documents  (원문 XML 원본 바이트, document_id = source_document_id)
  │ document_id                                 (FK: chunk_source_documents.document_id → filing_source_documents)
  ▼
chunk_source_documents ── chunk_id ──▶ chunk_metadata   (FK)
  │ chunk_id                                    (FK: chunk_embeddings.chunk_id → chunk_metadata)
  ▼
chunk_embeddings  (float32 × 1024)
  │ vector_id                                   (logical relation: FAISS 파일)
  ▼
FAISS  db/vector/filings_bge_m3.faiss  (IndexIDMap2, ID = vector_id)
```

원문에서 추출한 KPI(`source_kind='DOCUMENT_EXTRACTED'`, 헬스케어 R&D·은행 XML)는 두 단계를 건너뛰어 바로 연결된다.

```
financial_metric_values.source_document_id ──▶ filing_source_documents.document_id   (logical relation)
financial_metric_values.source_chunk_id    ──▶ chunk_metadata.chunk_id                (logical relation)
```

문서 ID가 있는 KPI는 316행(2023 208, 2024 44, 2025 44, 2026 20)이며, 전부 MySQL 원문 문서·청크·청크→문서 매핑과 일치한다(316/316).

## 3. ER 관계 요약

`FK` = DB에 외래키 제약이 있음. `logical relation` = 제약은 없고 값으로만 연결됨(백엔드가 조인 조건을 직접 써야 함).

### 재무 계층

| 부모 → 자식 | 연결 컬럼 | MySQL | SQLite |
|---|---|---|---|
| companies → financial_facts | corp_code | FK | FK |
| periods → financial_facts | (year, report_type) / period_id | FK | FK |
| companies → capex_facts | corp_code | FK | FK |
| periods → capex_facts | (year, report_type) / period_id | FK | FK |
| financial_facts → capex_facts | (corp_code, 기간, fs_div, account_id) | logical relation | logical relation |
| companies → financial_metric_values | corp_code | FK | FK |
| financial_metric_definitions → financial_metric_values | metric_code | FK | FK |
| financial_facts → financial_metric_values | `source_fact_key`(fact 자연키 해시) | logical relation | logical relation |
| capex_facts → financial_metric_values(CAPEX) | (corp_code, 기간, fs_div, value_type) | logical relation | logical relation |
| companies → company_financial_profiles | corp_code | FK | FK |
| krx_indices → krx_index_constituents → companies | index_code, corp_code | FK | FK |

### 원문·벡터 계층

| 부모 → 자식 | 연결 컬럼 | MySQL | 로컬 |
|---|---|---|---|
| companies → filings | corp_code | FK | (SQLite 테이블 없음) |
| filings → filing_source_documents | rcept_no | FK | 파일 경로 `data/raw/filings/xml/{corp}/{year}/{rt}/{rcept}/` |
| filing_source_documents → chunk_source_documents | document_id | FK | `filing_chunks.source_document_id`(2026 청크만) |
| chunk_metadata → chunk_source_documents | chunk_id | FK | - |
| chunk_metadata → chunk_embeddings | chunk_id | FK | 같은 행(`filing_chunks.embedding`) |
| chunk_embeddings → FAISS | vector_id | logical relation | logical relation |
| financial_facts / KPI → filings | rcept_no | logical relation | logical relation |
| KPI → filing_source_documents | source_document_id | logical relation | logical relation |
| KPI → chunk_metadata | source_chunk_id | logical relation | logical relation |

로컬 Vector SQLite에는 FK가 없다(`filing_chunks` 단일 테이블).

## 4. Financial ↔ Vector 연결 규칙

1. **기본 연결 키는 (`corp_code`, `year`, `report_type`, `rcept_no`)다.**
   - 한 슬롯(기업·기간)의 공시는 `filings`에 1건이다.
   - 재무 값(`financial_facts.rcept_no`)과 청크(`chunk_metadata.rcept_no`)는 2026 50개 슬롯 전부 같은 접수번호다.
2. **원문 문서까지 갈 때는 `source_document_id`를 쓴다.**
   - KPI 행에 `source_document_id`가 있으면 `filing_source_documents.document_id`로 바로 조회한다.
   - 없으면(대부분의 API 기반 값) `rcept_no`로 `filing_source_documents`를 찾는다. 한 공시에 문서가 여러 개면
     `document_sequence`로 구분한다.
3. **검색 결과에서 원문·숫자로 갈 때**
   ```
   FAISS 검색 → vector_id
     → chunk_metadata (vector_id)        : chunk_id, corp_code, year, report_type, rcept_no, section_name
     → chunk_source_documents (chunk_id) : document_id (= source_document_id)
     → filing_source_documents           : rcept_no, source_path, raw_xml
     → financial_facts / financial_metric_values (corp_code, year, report_type[, rcept_no]) : 같은 슬롯의 숫자
   ```
4. **주의**
   - `rcept_no`로 재무와 원문을 맞출 때는 슬롯 키를 먼저 맞추고 `rcept_no`를 확인용으로 쓴다.
     2025 FY KB금융(`00688996`)은 재무 API가 정정공시 `20260619000667`, 원문·청크가 원 공시
     `20260324000835`라서 두 번호가 다르다(2026 범위 밖, 기존부터 있던 차이).
   - 원문 경로 문자열로 직접 비교하지 말고 `document_id`를 쓴다(경로가 바뀌면 ID도 바뀐다).
   - FAISS 결과 ID는 반드시 `chunk_metadata.vector_id`로 역조회한다. FAISS 순번(0..ntotal-1)이 아니다.

## 5. FAISS 검색 흐름

1. `embedding_metadata`에서 `is_current=1` 행을 읽는다: 모델 `BAAI/bge-m3`, revision
   `5617a9f61b028005a4858fdac845db406aefb181`, 1,024차원, 정규화, `IndexIDMap2(IndexFlatIP)`, `faiss_ntotal=170,534`.
2. 질의를 같은 모델·revision으로 임베딩하고 L2 정규화한다(내적 = 코사인).
3. FAISS에서 상위 k개 `vector_id`를 얻는다. 기업·기간 필터는 `chunk_metadata`(corp_code, year, report_type)로 건다.
4. `vector_id` → `chunk_metadata` → `chunk_source_documents` → `filing_source_documents` 순으로 원문을 붙인다.
5. 벡터 캐시 키는 `embedding_metadata.index_version`을 쓴다. 재무 `data_version`이 바뀌어도 인덱스 버전이 같으면 캐시를 유지한다.

FAISS 파일은 MySQL에 없다. 같은 벡터가 `chunk_embeddings`(float32 BLOB)에 있으므로 서버에서 인덱스를 다시 만들 수 있다.
`infra/mysql/team_client/dart_semantic_search.py`는 FAISS 없이 MySQL 벡터를 내려받아 numpy 내적으로 검색하는 예시다.

## 6. status 정책

| 값 | 위치 | 의미 | 백엔드 처리 |
|---|---|---|---|
| `approved` | `financial_metric_values.review_status` | 원천과 대조해 검증된 값 | 확정값으로 표시 |
| `review_required` | `financial_metric_values.review_status`, `capex_facts.review_required=1` | **원문과 계산 결과는 보존하지만, 서비스에서 확정값으로 표시하기 전에 주의가 필요한 상태** | 값을 숨기지 않는다. "검토 필요" 표시, 근거(`calculation_method`, `source_values`) 노출, 합계·순위·차트 계산에서 제외 |
| `is_active` | 버전 관리 테이블 | 1=현재 버전에 있는 행, 0=이전 버전에만 있던 행(삭제하지 않고 남김) | 항상 `is_active=1`로 조회 |
| `is_current` | `data_versions`, `embedding_metadata` | 현재 서비스 버전(각각 정확히 1개) | 버전 표시·캐시 키에 사용 |
| `validated` | `data_versions` | 1=적재 후 SQLite↔MySQL 대조 검증 통과 | `validated=1`이 아닌 버전은 current가 될 수 없다 |

현재 `review_required`: KPI 10행(은행지주 2023~2024), CAPEX 2행(HLB 2023 H1·에이치브이엠 2025 H1 분기 단독).
2026 데이터는 KPI 988행·CAPEX 80행 모두 `approved`다.

CAPEX `review_required=1` 행은 `cash_outflow_amount`가 NULL이고 `calculated_value`에 음수 계산값이 남아 있다.
표준 VIEW `standardized_financials`는 이 행을 제외한다.

## 7. 산업 구분

- `company_financial_profiles`에는 11개사만 행이 있다(은행 5, 헬스케어 5, 에이치브이엠).
- 나머지 14개사는 프로필 행이 없고, KRX 그룹으로 산업을 정한다: `KRX_SEMI`→SEMICONDUCTOR, `KRX_AUTO`→AUTOMOTIVE,
  `KRX_STEEL`→STEEL. 서비스 함수 `get_industry_kpis`와 감사 코드가 같은 규칙을 쓴다.
- 금융주 판별: `industry_profile IN ('BANK_HOLDING','INTERNET_BANK')`. 금융주는 CAPEX 대상이 아니다(`capex_applicable=0`, CAPEX 행 0).
- 금융주 질문은 `financial_metric_values`(금융 KPI)를 먼저 조회한다. NIM·BIS·CET1·NPL·연체율은 구조화되어 있지 않으므로
  원문 검색으로 답한다([`KNOWN_LIMITATIONS_20261011.md`](KNOWN_LIMITATIONS_20261011.md) 3절).

## 8. 단위

| 항목 | 단위 |
|---|---|
| 금액(`financial_facts`, `capex_facts`, KPI `unit='KRW'`) | 원 |
| KPI 비율(`unit='BPS'`) | 1 = 0.01%. 1159 = 11.59% |
| `financial_metric_definitions.unit_type='PERCENT'`(은행 비율) | 정의만 있고 값 행은 없음 |
