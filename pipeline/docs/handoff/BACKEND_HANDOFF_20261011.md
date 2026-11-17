# 백엔드 인계 (2026-10-11)

이 문서 하나로 현재 데이터를 조회할 수 있도록 정리했다. 상세는 맨 아래 문서 링크를 본다.

## 1. 현재 데이터 상태

- 25개 기업, 2023~2025(Q1·H1·Q3·FY) + **2026 Q1·H1**. 2026 Q3·FY는 아직 없다(Q3 제출기한 2026-11-14).
- 2026 Q1/H1 50개 슬롯 모두 재무·원문 XML·청크·임베딩이 있다(coverage 50/50 PASS).
- 2026 KPI 988행, CAPEX 80행은 모두 `approved`다.
- 운영 승격: 2026-10-11 20:11 KST(로컬), 20:53 KST(MySQL current 전환).

| 항목 | 건수 |
|---|---:|
| 기업 | 25 |
| financial_facts | 75,304 |
| capex_facts | 549 |
| KPI | 2,665 |
| 원문 문서 | 449 |
| 청크 / 임베딩 / FAISS | 170,534 / 170,534 / 170,534 |

## 2. 현재 운영 버전

| 축 | 버전 |
|---|---|
| 재무 data_version | `2026-10-11-dart25-2026q1h1-v14` |
| 임베딩/FAISS index_version | `bge-m3-2026-10-11-dart25-2026q1h1-v14` |

두 버전은 **독립**이다. 문자열이 같은지로 정합성을 판단하지 않는다([`DATA_VERSION_HANDOFF_20261011.md`](DATA_VERSION_HANDOFF_20261011.md)).

```sql
SELECT data_version FROM data_versions WHERE is_current = 1;          -- 1행
SELECT index_version FROM embedding_metadata WHERE is_current = 1;    -- 1행
```

## 3. DB 위치

| 자산 | 위치 | 비고 |
|---|---|---|
| 중앙 MySQL | `KHP-SD1-03:3306`, DB `dart_big_in` | **백엔드 기준 DB.** 계정 `dart_backend`(읽기 전용). 비밀번호는 별도 전달 |
| Financial SQLite | `db/financial/financial.db` | MySQL 재무 테이블의 원본 |
| Vector SQLite | `db/vector/filings_bge_m3.db` | 청크 + 임베딩 원본 |
| FAISS | `db/vector/filings_bge_m3.faiss` | MySQL에 없음. 필요하면 `chunk_embeddings`로 재구성 |
| 원문 XML | `data/raw/filings/xml/{corp_code}/{year}/{report_type}/{rcept_no}/` | MySQL `filing_source_documents.raw_xml`에 원본 바이트 동일 보관 |

## 4. MySQL 사용 기준

- 버전 관리 테이블(`financial_facts`, `capex_facts`, `filings`, `filing_source_documents`, `chunk_metadata`,
  `chunk_embeddings`)은 **항상 `is_active = 1`**로 조회한다(이전 버전 행이 `is_active=0`으로 남아 있음).
- `financial_metric_values`는 버전 컬럼이 없다. 그대로 조회한다.
- 조인은 자연키로 한다. SQLite 내부 번호(`period_id`, `fact_id`, `capex_id`)는 쓰지 않는다.
- 재무제표 표준 조회는 VIEW `standardized_financials`를 쓴다(공식 태그 매칭, 확정 CAPEX만 포함).

## 5. 주요 테이블

| 테이블 | 용도 | 키 |
|---|---|---|
| `companies` | 기업 마스터 | `corp_code` |
| `periods` | 기간 | (`year`, `report_type`) |
| `krx_index_constituents` | 산업 그룹 구성 | (`index_code`, `corp_code`) |
| `company_financial_profiles` | 산업 프로필(11개사만) | `corp_code` |
| `financial_facts` | 재무제표 계정 값 | uk_fact |
| `capex_facts` | CAPEX | (`corp_code`, `year`, `report_type`, `fs_div`, `value_type`) |
| `financial_metric_definitions` / `financial_metric_values` | KPI 정의 / 값 | uk_fin_metric_value |
| `filings` | 슬롯별 대표 공시 | `rcept_no` |
| `filing_source_documents` | 원문 XML | `document_id` |
| `chunk_metadata` / `chunk_embeddings` / `chunk_source_documents` | 청크 / 벡터 / 청크→문서 | `chunk_id` |
| `coverage` | 슬롯별 존재 여부 | (`corp_code`, `year`, `report_type`) |
| `data_versions` / `embedding_metadata` | 버전 | |

전체 컬럼: [`DB_SCHEMA_20261011.md`](DB_SCHEMA_20261011.md), [`COLUMN_DICTIONARY_20261011.csv`](COLUMN_DICTIONARY_20261011.csv)

## 6. 기업/KPI 조회

```sql
-- 기업과 산업
SELECT c.corp_code, c.corp_name, c.stock_code, k.index_code,
       COALESCE(p.industry_profile, CASE k.index_code WHEN 'KRX_SEMI' THEN 'SEMICONDUCTOR'
                WHEN 'KRX_AUTO' THEN 'AUTOMOTIVE' WHEN 'KRX_STEEL' THEN 'STEEL'
                WHEN 'KRX_HEALTH' THEN 'HEALTHCARE' END) AS industry_profile
FROM companies c
LEFT JOIN krx_index_constituents k ON k.corp_code = c.corp_code AND k.is_current = 1
LEFT JOIN company_financial_profiles p ON p.corp_code = c.corp_code;

-- 기업 KPI (예: 현대자동차 2026 H1)
SELECT v.metric_code, d.metric_name, v.value_type, v.value, v.unit, v.review_status, v.source_rcept_no
FROM financial_metric_values v JOIN financial_metric_definitions d USING (metric_code)
WHERE v.corp_code = '00164742' AND v.year = 2026 AND v.report_type = 'H1';
```

- KPI 단위: 금액 `KRW`(원), 비율 `BPS`(1 = 0.01%).
- `value_type`: `cumulative`(누적), `quarterly`(해당 분기 단독), `annual`, `point_in_time`(잔액), `ratio`(공시 비율).
- 프로필 행이 없는 14개사(반도체·자동차·철강)는 KRX 그룹으로 산업을 정한다. Python 서비스 함수
  `src.retrieval.industry_kpi.get_industry_kpis`도 같은 규칙을 쓴다(이번 작업에서 수정).
- **2023~2025 KPI는 은행·헬스케어 R&D만 있다.** 일반 기업의 과거 재무 지표는 `standardized_financials`로 조회한다.

예제 16개(모두 실행 확인): [`BACKEND_QUERY_EXAMPLES_20261011.md`](BACKEND_QUERY_EXAMPLES_20261011.md)

## 7. Financial ↔ DART 원문 ↔ Vector 연결

```
companies ─corp_code→ financial_facts / KPI / CAPEX ─rcept_no→ filings ─rcept_no→ filing_source_documents
   ─document_id→ chunk_source_documents ─chunk_id→ chunk_metadata ─chunk_id→ chunk_embeddings ─vector_id→ FAISS
```

- 기본 연결 키: (`corp_code`, `year`, `report_type`, `rcept_no`).
- 원문 문서: `source_document_id` = `filing_source_documents.document_id` = `SHA-256(rcept_no + '\0' + source_path)`.
- 원문 추출 KPI(헬스케어 R&D, 은행 XML)는 `source_document_id`·`source_chunk_id`로 바로 원문·청크에 연결된다(316/316 확인).
- 검색 결과 역추적: `chunk_id → chunk_source_documents.document_id → filing_source_documents(rcept_no, source_path)`.
- 상세 규칙과 FK/논리 관계 표: [`BACKEND_DATA_CONTRACT_20261011.md`](BACKEND_DATA_CONTRACT_20261011.md)

## 8. FAISS 검색 흐름

1. `embedding_metadata`(current)에서 모델·revision·차원 확인: `BAAI/bge-m3`, `5617a9f6…`, 1,024, 정규화.
2. 질의 임베딩(L2 정규화) → FAISS `IndexIDMap2(IndexFlatIP)` 검색 → `vector_id` 목록.
3. `vector_id`로 `chunk_metadata`를 조회해 기업·기간 필터, 섹션, 본문, `rcept_no`를 얻는다.
4. `chunk_source_documents` → `filing_source_documents`로 원문을 붙인다.
5. 캐시 키는 `index_version`.

Python 서비스 경로: `src.retrieval.operational_retriever.retrieve_filing_context(query, corp_name=…, year=…, report_type=…)`
(BGE-M3 dense, 2026-10-11 smoke 14/14 통과).

## 9. review_required 처리

- 의미: **원문과 계산 결과는 보존하지만, 서비스에서 확정값으로 표시하기 전에 주의가 필요한 상태.**
  값을 숨기지 않는다.
- 위치: KPI `review_status = 'review_required'`, CAPEX `review_required = 1`.
- 표시: "검토 필요" 배지 + 근거(`calculation_method`, `source_values`). 합계·순위·차트·비교 계산에서는 제외.
- 현재: KPI 10행(은행지주 2023~2024), CAPEX 2행(HLB 2023 H1·에이치브이엠 2025 H1 분기 단독, `cash_outflow_amount` NULL).
  2026은 0건.
- POSCO홀딩스·HL만도 2026 H1 영업활동현금흐름(분기 단독)은 Q1 누적이 음수라 Q2 단독이 H1 누적보다 크다.
  원문으로 실제 부호 반전을 확인해 `approved`로 확정했다.

## 10. 금융 Vector fallback

- 구조화된 금융 KPI: 이자수익·이자비용·순이자이익·수수료수익/비용·순수수료이익·신용손실비용·예수부채·대출채권·손익 등.
  금융주 질문은 이 KPI를 먼저 조회한다.
- **NIM, BIS·총자본비율, CET1, 고정이하여신비율(NPL), 연체율(그리고 ROA·ROE·예대율·대손비용률)은 구조화 행이 0개다.**
  원문 검색(`retrieve_filing_context` 또는 FAISS)으로 공시 표를 찾아 답한다. 예: KB금융 2026 H1
  "BIS 자기자본비율" 질의 → `II. 사업의 내용 > 5. 재무건전성 등 기타 참고사항` 표 청크.
- 행이 없다고 "데이터 없음"으로 답하지 않는다.

## 11. 알려진 제한사항 (요약)

1. POSCO홀딩스 2026 H1 영업활동현금흐름(분기): 부호 반전 검증 후 `approved`.
2. HL만도 2026 H1 영업활동현금흐름(분기): 같은 유형, `approved`.
3. NIM/BIS/CET1/NPL/연체율은 Vector fallback.
4. 2026 Q3/FY 미수집.
5. `review_required` 값은 확정 수치로 쓰지 않는다.
6. Financial version과 Embedding version은 독립이다.
7. 2023~2025 KPI 부분 적재(일반 기업 없음), `filings.dart_report_name`·`is_amended` 전 행 NULL,
   KB금융 2025 FY 재무(정정)·원문(원 공시) 접수번호 차이.

전체: [`KNOWN_LIMITATIONS_20261011.md`](KNOWN_LIMITATIONS_20261011.md)

## 12. 이번 작업의 코드 변경

| 파일 | 변경 |
|---|---|
| `src/retrieval/industry_kpi.py` | 프로필 행이 없는 기업도 KRX 그룹으로 산업을 정해 KPI를 반환(이전엔 14개사 2026 KPI 506행이 서비스에서 안 보였음) |
| `src/quality/financial_db_audit.py` | 감사가 암묵 프로필·CAPEX 음수 단독 정책·공시 비율 `value_type='ratio'`를 인식 |
| `src/staging_filing_increment.py` | 청크 `source_document_id`를 파일 해시가 아니라 정식 문서 ID로 기록 |
| `src/quality/dart25_2026_coverage.py` | 커버리지 CSV의 문서 ID를 정식 ID로, DB는 읽기 전용으로 연다 |
| `src/db/financial_metrics.py` | 원문으로 검증된 실제 부호 반전 분기값은 재실행해도 `approved` 유지 |
| `tests/*` | 2026 증분·부분 적재 기준에 맞게 기대값 정리, 서비스 KPI 회귀 테스트 추가 |

## 13. 관련 문서

| 문서 | 내용 |
|---|---|
| [`DB_SCHEMA_20261011.md`](DB_SCHEMA_20261011.md) | 실제 스키마(introspection), SQLite↔MySQL 차이 |
| [`COLUMN_DICTIONARY_20261011.csv`](COLUMN_DICTIONARY_20261011.csv) | 컬럼 정의서(504개) |
| [`BACKEND_DATA_CONTRACT_20261011.md`](BACKEND_DATA_CONTRACT_20261011.md) | 키 의미, 연결 흐름, ER, status 정책 |
| [`BACKEND_QUERY_EXAMPLES_20261011.md`](BACKEND_QUERY_EXAMPLES_20261011.md) | 실행 확인된 SQL 예제 |
| [`DATA_VERSION_HANDOFF_20261011.md`](DATA_VERSION_HANDOFF_20261011.md) | 버전, 건수, 증가분, 해시 |
| [`KNOWN_LIMITATIONS_20261011.md`](KNOWN_LIMITATIONS_20261011.md) | 제한사항 |
| [`ROLLBACK_20261011.md`](ROLLBACK_20261011.md) | 롤백 절차 |
| [`DART25_2026_Q1_H1_PROMOTION_REPORT_20261011.md`](DART25_2026_Q1_H1_PROMOTION_REPORT_20261011.md) | 이번 승격 작업 보고서 |
