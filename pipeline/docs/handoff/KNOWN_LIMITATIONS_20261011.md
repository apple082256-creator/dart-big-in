# 알려진 제한사항 (2026-10-11, data_version `2026-10-11-dart25-2026q1h1-v14`)

백엔드가 화면·API를 만들 때 반드시 알아야 할 제한사항이다. 숫자는 운영 DB를 직접 조회해 확인했다.

## 1. POSCO홀딩스 2026 H1 영업활동현금흐름 (분기 단독)

| 항목 | 값 |
|---|---|
| 기업 | POSCO홀딩스 (`00155319`) |
| KPI | `OPERATING_CASH_FLOW`, `value_type='quarterly'`, CFS |
| 값 | 1,067,916,897,371원 = H1 누적 708,619,472,560 − Q1 누적 (−359,297,424,811) |
| 근거 공시 | Q1 `20260515000988`, H1 `20260814000957` |
| 현재 상태 | `review_status='approved'` |

Q1 누적이 음수, H1 누적이 양수라서 분기 차감 결과의 부호 해석에 주의가 필요한 행이다. 두 공시 원문을
직접 대조해 실제 부호 반전(Q1 순유출 → H1 순유입)임을 확인했고, 사용자 결정(2026-10-11)에 따라 `approved`로
승격했다. `source_values.verified_evidence`에 `category='document_real_reversal'`, `basis='document'`와 두
접수번호가 남아 있다. 화면에서 Q2 단독 영업현금흐름이 Q1·H1 누적값보다 크게 보이는 것은 정상이다.

## 2. HL만도 2026 H1 영업활동현금흐름 (분기 단독)

| 항목 | 값 |
|---|---|
| 기업 | HL만도 (`01042775`) |
| KPI | `OPERATING_CASH_FLOW`, `value_type='quarterly'`, CFS |
| 값 | 120,772,851,654원 = H1 누적 89,433,242,606 − Q1 누적 (−31,339,609,048) |
| 근거 공시 | Q1 `20260515002318`, H1 `20260814004041` |
| 현재 상태 | `review_status='approved'` |

1번과 같은 유형이며 같은 방식으로 검증·승인했다.

> 1·2번은 인계 요청서에 `review_required`로 적혀 있었으나, 원문 검증 후 `approved`로 확정됐다. 현재
> 2026 KPI 988행은 모두 `approved`이며 `review_required`는 0건이다. 판정 규칙은
> `src/db/financial_metrics.py`의 `_has_unverified_sign_conflict`에 있어 재실행해도 유지된다. 근거가 없는
> 반대 부호 차감은 계속 `review_required`가 된다.

## 3. 금융 비율 KPI는 구조화되어 있지 않다 (Vector 검색 fallback)

`financial_metric_definitions`에 정의는 있으나 `financial_metric_values` 행은 0개다.

| KPI 코드 | 이름 | 구조화 행 |
|---|---|---:|
| `NIM` | 순이자마진 | 0 |
| `BIS_CAPITAL_RATIO` | BIS자기자본비율 | 0 |
| `TOTAL_CAPITAL_RATIO` | 총자본비율 | 0 |
| `CET1_RATIO` | 보통주자본비율 | 0 |
| `NPL_RATIO` | 고정이하여신비율 | 0 |
| `DELINQUENCY_RATIO` | 연체율 | 0 |
| `ROA`, `ROE`, `LOAN_TO_DEPOSIT_RATIO`, `CREDIT_COST_RATIO` | 기타 금융 비율 | 0 |

- 이 값들은 공시 원문 청크(Vector 검색)로 답한다. 2026 금융 10개 슬롯(5개사 × Q1/H1) 중 원문 청크에 키워드가
  있는 슬롯 수: BIS 10, 고정이하여신비율 10, 연체율 10, 보통주자본비율 9, 순이자마진 4. NIM은 공시마다 표기가
  달라('순이자마진' 외 표현 포함) 검색 결과가 비거나 간접 표현일 수 있다.
- 백엔드는 행이 없다고 "데이터 없음"으로 표시하지 말고, 의미 검색 결과(원문 인용 + `rcept_no`)를 보여 줘야 한다.
- 이번 작업에서 임의로 구조화하지 않았다.
- 구조화된 금융 KPI(이자수익·이자비용·순이자이익·수수료·신용손실비용·예수부채·대출채권 등)는 정상 조회된다.

## 4. 2026 Q3·FY는 이번 수집에서 제외

- 2026 데이터는 Q1(`11013`)·H1(`11012`)만 있다. Q3 제출기한은 2026-11-14다.
- 2026 Q3/FY 행은 periods, financial_facts, KPI, CAPEX, filings, 청크, 벡터 모두 0건이다.
- 2026 연간·Q3 질문에는 "아직 공시 전/미수집"으로 답해야 한다.

## 5. `review_required` 값을 확정 수치처럼 쓰면 안 된다

- `review_required`는 값을 숨기는 상태가 아니다. **원문과 계산 결과는 보존하지만, 서비스에서 확정값으로
  표시하기 전에 주의가 필요한 상태**다.
- 현재 운영 DB 기준 `review_required` 행:
  - `financial_metric_values`: 10행, 모두 은행지주 2023~2024
    (`LOANS_AT_AMORTISED_COST` 7, `OPERATING_INCOME`·`OWNERS_NET_INCOME`·`PRETAX_INCOME` 각 1). 2026은 0건.
  - `capex_facts.review_required=1`: 2행. HLB 2023 H1·에이치브이엠 2025 H1의 분기 단독(Q2) CAPEX가 음수로 계산된 행.
    `calculated_value`(음수)는 보존하고 `cash_outflow_amount`는 NULL이며 표준 VIEW에서 제외된다. 2026은 0건.
- 권장 표시: 값 옆에 "검토 필요" 배지, 근거(`calculation_method`, `source_values`) 링크. 합계·순위·차트 계산에는 넣지 않는다.

## 6. Financial version과 Embedding version은 독립 버전

| 축 | 현재 값 | 저장 위치 |
|---|---|---|
| 재무 데이터 버전 | `2026-10-11-dart25-2026q1h1-v14` | `data_versions.is_current=1` |
| 임베딩/인덱스 버전 | `bge-m3-2026-10-11-dart25-2026q1h1-v14` | `embedding_metadata.is_current=1` |

- 두 문자열이 이번에는 비슷하지만, 재무만 바뀌는 배포(예: v12, v13)에서는 인덱스 버전이 그대로 유지된다.
- `financial_version == embedding_version` 같은 문자열 비교를 정합성 조건으로 쓰지 말 것.
- 정합성은 실제 자산으로 판정한다: `embedding_metadata.sqlite_sha256`/`faiss_sha256`과 실제 파일 해시,
  `faiss_ntotal = 검색 가능 벡터 수`, `chunk_metadata.vector_id` 집합 = FAISS ID 집합.
- 벡터 캐시 키는 `embedding_metadata.index_version`을 쓴다.

## 7. 2023~2025 KPI 계층은 부분 적재 상태다

`financial_metric_values`의 연도·산업별 적재 범위가 다르다.

| 산업 | 2023~2025 KPI | 2026 KPI |
|---|---|---|
| 은행(4) + 인터넷은행(1) | 전체 금융 KPI | 전체 |
| 헬스케어(5) | R&D 2지표만 (120행) | 일반 KPI + R&D |
| 반도체·자동차·철강(15) | 없음 | 일반 KPI 전체 |

- 2023~2025 일반 기업 KPI(매출·영업이익률·부채비율 등)는 KPI 테이블이 아니라
  `standardized_financials` VIEW 또는 `financial_facts`로 조회한다.
- 전체 재생성(`materialize_financial_metric_values`)을 하면 헬스케어·에이치브이엠 2023~2025 일반 KPI 1,181행(2026 성장률 24행 포함 시 1,205행)이
  추가로 생긴다. 기존 행의 값·상태는 바뀌지 않는다(검증 완료). 백필 여부는 별도 결정 사항이다.
- 같은 이유로 헬스케어·에이치브이엠의 2026 `REVENUE_GROWTH_YOY`(24행)는 2025 기준 KPI 행이 없어 생성되지 않았다.
  반도체·자동차·철강 14개사의 2026 성장률 KPI도 2025 KPI 행이 없으므로 같은 제약을 받는다.

## 8. 기타

- MySQL `financial_metric_values`에는 `data_version`/`is_active` 컬럼이 없다. 항상 최신 적재 상태 1벌이다.
- MySQL `chunk_metadata`에는 문서 ID 컬럼이 없다. 청크→원문 연결은 `chunk_source_documents`를 쓴다.
- 로컬 Vector SQLite(`filing_chunks`)의 `source_document_id`는 2026 청크 22,814건에만 채워져 있다.
  기존 147,720건은 NULL이며, 연결은 MySQL `chunk_source_documents`(170,534/170,534)로 한다.
- MySQL `filings.dart_report_name`, `is_amended`는 동기화가 채우지 않아 전 연도 345행 모두 NULL이다. 정정 여부는
  `rcept_no`와 `financial_collection_status`, 품질 CSV(`data/processed/quality/dart25_2026_q1_h1_coverage.csv`의 정정 여부)로 본다.
- KB금융(`00688996`) 2025 FY: 재무 API는 정정공시 `20260619000667`, 원문·청크(`filings`)는 원 공시 `20260324000835` 기준이다.
  v14 이전부터 있던 차이이며 2026 범위 밖이다. 이 슬롯은 `rcept_no`가 아니라 (corp_code, year, report_type)로 연결한다.
- SQLite `capex_facts.capex_id`, `financial_facts.fact_id`, `period_id`는 빌드마다 바뀔 수 있다. 자연키로 조인한다.
- MySQL `data_versions`의 v13(`2026-10-10-healthcare-rnd-docid-v13`)은 `validated=1, is_current=0`으로 남아 있다.
  v14 직전 current는 v12였다(이전 문서 일부는 v13을 current로 적고 있음).
