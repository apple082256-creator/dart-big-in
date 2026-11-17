# 산업별 KPI 계층 설계 및 1차 KPI 정의서

- 작성일: 2026-10-09
- 기준 데이터: quality-v6 staging (운영 `residual-v5`과 일반 20개사 원천 동일)
- 범위: 데이터베이스 계층만 다룬다. 그래프·비교 화면·LLM 로직·UI는 범위 밖이다.
- 상태: 설계·정의 완료. 구현과 staging 결과는 `docs/handoff/INDUSTRY_KPI_STAGING_20261009.md` 참조

## 1. 원칙

1. KPI는 전체 재무계정을 대체하지 않는다.
   - `financial_facts`(API 원천 49,054행과 분기 계산값)는 그대로 둔다.
   - KPI는 그중 산업 분석용 지표를 표준화한 선택 계층이다.
2. 조회 경로는 두 가지를 유지한다.
   - **A. KPI 조회:** `financial_metric_values` + `financial_metric_definitions`
   - **B. 전체 계정 조회:** `financial_facts` + `periods` + `companies`(기존 `get_financial_context`)
3. 공식 계정 태그로 식별한다.
   - 표준명은 같은 태그에 복수 존재하므로 식별 키로 쓰지 않는다.
   - 다른 원천 계정을 이름만으로 합치지 않는다.
4. 3개월·누적·연간·시점 잔액을 구분한다.
   - 시점 잔액에는 차감 공식을 쓰지 않는다.
   - 비율의 분자·분모는 같은 기업·기간·값 구분·연결/별도에서만 계산한다.
5. 값이 없으면 행을 만들지 않는다(0이나 보간 금지). 검증되지 않은 값은 `review_required`로 둔다.

## 2. 기존 구조 분석

| 계층 | 테이블 | 현재 상태 |
|---|---|---|
| 전체 계정 | `financial_facts`, `periods`, `companies`, `account_mapping` | 25개사, 62,602행(staging 62,608). 매핑 1,552건이 사람 검토 전 상태 |
| CAPEX | `capex_facts` | 일반 20개사, 469행. 직접 계정 / 구성요소 합산, `cash_outflow_amount` 양수 |
| 금융 KPI | `financial_metric_definitions`(29), `financial_metric_mappings`(42), `financial_metric_values`(1,554), `company_financial_profiles`(5) | 금융 5개사. DIRECT / DERIVED / DOCUMENT_EXTRACTED, 출처·검토 상태 보유 |

금융 KPI 계층은 이미 다음을 갖추고 있으므로 **새 테이블 없이 이 계층을 일반산업으로 확장한다.**
- 정의·매핑·값 분리
- 공식 태그 매핑과 우선순위
- 회사별 예외(`applies_to_corp_code`)
- 출처 추적: `source_fact_key`, `source_rcept_no`, `source_values`
- 검토 상태

## 3. 확장 설계

### 3.1 기업 프로필

`company_financial_profiles`에 일반 20개사를 추가한다.

| industry_profile | 대상(KRX 그룹) | preferred_fs_div |
|---|---|---|
| SEMICONDUCTOR | KRX_SEMI 5개사 | CFS(일반 19개사), 에이치브이엠만 OFS |
| HEALTHCARE | KRX_HEALTH 5개사 | 〃 |
| STEEL | KRX_STEEL 5개사 | 〃 |
| AUTOMOTIVE | KRX_AUTO 5개사 | 〃 |

- `capex_applicable=1`로 둔다(금융사는 0 유지).
- `comparison_scope`는 `consolidated` 또는 `separate`로 둔다.

### 3.2 지표 정의

- 공통 KPI는 `industry_profile='GENERAL'`로 한 번만 정의한다. 산업별 중복 정의를 하지 않는다.
- 산업별 확장 KPI만 해당 산업으로 정의한다.
- 매핑(`financial_metric_mappings`)은 조인 구조상 회사 프로필별로 두므로 4개 산업에 같은 공식 태그 행을 둔다(약 40행).

### 3.3 값 생성

| 값 종류 | source_kind | 계산 |
|---|---|---|
| 공식 태그 원천값 | DIRECT | `financial_facts` 그대로. 3개월·누적·연간·시점 잔액을 모두 보존 |
| 분기 계산값 | DERIVED | 기존 규칙(ord 일치, 계정 ID 매칭, 검증 목록)으로 만든 분기 사실값 |
| CAPEX | DIRECT / DERIVED | `capex_facts.cash_outflow_amount`. 직접 계정은 DIRECT, 구성요소 합산은 DERIVED |
| 비율·증가율 | DERIVED | 같은 기업·value_type·fs_div·scope의 두 지표값. 계산식과 분자·분모 값과 출처 키 기록 |

- 비용 부호: 비율 계산 시 비용·CAPEX는 유출 크기(양수)로 정규화한다.
  - 원천 DIRECT 값은 공시 부호 그대로 둔다.
  - 정규화 여부는 `source_values`에 기록한다.
- 증가율
  - 3개월은 전년 동기 3개월과 비교한다.
  - 연간은 전년 연간과 비교한다.
  - 누적끼리는 같은 누적 기간끼리만 비교한다.
  - 분모 ≤ 0이면 계산하지 않는다.
- 부채비율 = 부채총계 / 자본총계(같은 시점). 자본 ≤ 0이면 계산하지 않는다.
- 공식 태그가 없는 기간
  - 회사별 예외 매핑(`applies_to_corp_code`)을 `review_required`로 추가한다.
  - 검증 규칙을 통과한 경우에만 `approved`로 둔다. 예: 매출액 = 매출원가 + 매출총이익

### 3.4 백엔드 호환성 (결정 필요)

현재 분석기(`src/llm/analyzer.py`, 미커밋 작업 영역)는 회사에 `financial_metric_values` 행이 하나라도 있으면 원천 계정 대신 지표 행을 사용한다.
일반 20개사에 KPI 행을 넣으면 해당 회사의 분석기 동작이 바뀐다.

| 선택지 | 내용 | 영향 |
|---|---|---|
| A (권장) | KPI는 저장만 하고, 분석기의 지표 우선 사용을 금융 프로필(`BANK_HOLDING`, `INTERNET_BANK`)로 한정 | 분석기 한 줄 수정(백엔드 합의 필요). 기존 일반사 응답 불변 |
| B | 분석기가 KPI와 원천 계정을 함께 사용하도록 변경 | 백엔드 작업 필요 |
| C | 일반 KPI를 별도 조회 함수로만 제공하고 지표 테이블 밖에 저장 | 계층 중복. 비권장 |

## 4. 1차 KPI 정의서

확보율: 일반 20개사 × 12개 기간(2023~2025, Q1/H1/Q3/FY), 공식 태그 기준.
에이치브이엠은 상장 전 기간이 없어 최대 7개 기간이다.

### 4.1 공통 KPI (GENERAL, 1차 채택)

| metric_code | 지표명 | 원천 태그 / 계산식 | 재무제표 | period_nature | 단위 | 확보율(12개 기간 완비) | 비고 |
|---|---|---|---|---|---|---|---|
| REVENUE | 매출액 | `ifrs-full_Revenue` | IS/CIS | flow | KRW | 18/20 | POSCO 2023 Q1~Q3는 비공식 '매출액'. 예외 매핑 후 검증 필요 |
| OPERATING_INCOME | 영업이익 | `dart_OperatingIncomeLoss` > `ifrs-full_ProfitLossFromOperatingActivities` | IS/CIS | flow | KRW | 19/20 | |
| NET_INCOME | 당기순이익 | `ifrs-full_ProfitLoss` | IS/CIS | flow | KRW | 18/20 | 현대차 2023 Q1~Q3는 비공식·중복 행. 검토 대상 |
| OPERATING_CASH_FLOW | 영업활동현금흐름 | `ifrs-full_CashFlowsFromUsedInOperatingActivities` | CF | flow(누적 공시) | KRW | 18/20 | 풍산 2023 Q1·H1은 비공식 키 |
| CAPEX | 설비투자 | `capex_facts` 유출액 | CF | flow(누적 공시) | KRW | 19/20 | |
| TOTAL_ASSETS | 자산총계 | `ifrs-full_Assets` | BS | point_in_time | KRW | 19/20 | |
| TOTAL_LIABILITIES | 부채총계 | `ifrs-full_Liabilities` | BS | point_in_time | KRW | 19/20 | |
| TOTAL_EQUITY | 자본총계 | `ifrs-full_Equity` | BS | point_in_time | KRW | 19/20 | |
| REVENUE_GROWTH_YOY | 매출 증가율 | REVENUE / 전년 동기 REVENUE − 1 | — | ratio | PERCENT | 매출 확보 기간에 의존 | 2023은 2022 원천이 없어 계산 불가 |
| OPERATING_MARGIN | 영업이익률 | OPERATING_INCOME / REVENUE | — | ratio | PERCENT | 매출·영업이익 공통 기간 | |
| DEBT_RATIO | 부채비율 | TOTAL_LIABILITIES / TOTAL_EQUITY | — | ratio | PERCENT | 19/20 | |

### 4.2 산업별 확장 KPI

| 산업 | metric_code | 계산 | 1차 채택 | 근거 |
|---|---|---|---|---|
| 반도체 | CAPEX_GROWTH_YOY | CAPEX / 전년 동기 CAPEX − 1 | 채택 | CAPEX 확보 |
| 반도체 | CAPEX_TO_OCF | CAPEX / OPERATING_CASH_FLOW (같은 누적 기간) | 채택 | 둘 다 누적 공시 |
| 반도체·자동차 | INVENTORIES / INVENTORY_CHANGE | `ifrs-full_Inventories`(+ `ifrs-full_InventoriesTotal`), 전 시점 대비 증감 | 채택 | 18/20. 한미 2024 Q3는 대체 태그 |
| 헬스케어 | RND_EXPENSE / RND_TO_REVENUE | 연구개발비 | **문서 추출 구현** | 본표 대신 사업보고서 연구개발활동 표를 연결 우선으로 추출하며 출처·범위·구성요소를 보존한다. `HEALTHCARE_RND_DOCUMENT_KPI.md` 참고 |
| 철강 | CAPEX_TO_REVENUE | CAPEX / REVENUE (같은 누적 기간) | 채택 | |
| 철강·자동차 | OPERATING_MARGIN, DEBT_RATIO | 공통 KPI 재사용 | 채택 | 별도 정의 없음 |

### 4.3 금융업 KPI (BANKING, 기존 계층 재사용)

| metric_code | 상태 |
|---|---|
| NET_INTEREST_INCOME, NET_FEE_COMMISSION_INCOME, NET_INCOME, LOANS_AT_AMORTISED_COST(+우리 `_NOTE`), CUSTOMER_DEPOSITS, CREDIT_LOSS_EXPENSE, TOTAL_ASSETS, TOTAL_EQUITY | 저장 완료 |
| ROA, ROE | 정의만 있다. 1차 계산 대상: 연간 순이익 / 기초·기말 평균 자산(자본)을 FY에 한정. 분기 연환산은 하지 않는다 |
| NIM, BIS, CET1, NPL 등 | 공시 원문 수치라 문서 추출과 검증이 필요하다. 1차 필수 KPI에서 제외하고 공시 검색으로 대체 |

금융지주 4개사는 연결(CFS), 카카오뱅크는 별도(OFS)다. 같은 화면에서 비교할 때 기준 차이를 표시해야 한다.

## 5. 다음 구현 단계 (승인 후)

1. 일반 20개사 프로필, 공통·확장 KPI 정의, 공식 태그 매핑을 추가한다(staging).
2. 결손 기간의 회사별 예외 매핑을 검증한다(매출 = 원가 + 총이익 등).
3. 비율·증가율 파생기를 추가한다. 분자·분모의 기간·범위 일치를 검사하고 원천 키를 기록한다.
4. 감사 모듈에 KPI 검사를 추가한다(확보율, 비율 재계산, 단위·범위 일치).
5. 3.4절 결정에 따라 백엔드 호환성 처리 → staging 검증 → 운영 반영 승인 요청.

## 6. DB 최신화 절차 점검 (현재 재현 가능 여부)

| 단계 | 현재 도구 | 상태 |
|---|---|---|
| 1. 신규 공시 수집 | `src/collect/financial_collector.py`, `filing_collector.py` | 있음(원본 JSON·ZIP·XML 보존) |
| 2. 원본 보존 | `data/raw/...`, MySQL `filing_source_documents` | 있음 |
| 3. 재무계정 추출 | `financial_cleaner`, `period_normalizer` | 있음. 2단계 매칭과 검증 목록 반영 |
| 4. 매핑·정규화 | `account_mapper`, `financial_metric_mappings` | 있음. 일반 KPI 매핑은 구현 예정 |
| 5. 분기값 계산 | `period_normalizer`, `quarter_backfill` | 있음(회귀 게이트 포함) |
| 6. KPI 생성 | `materialize_financial_metric_values`, `refresh_derived_metrics` | 금융만. 일반 KPI는 구현 예정 |
| 7. 품질 검사 | `src/quality/financial_db_audit.py` | 있음 |
| 8. SQLite·MySQL 동기화 | `infra/mysql/sync_sqlite_to_mysql.py` | 있음 |
| 9. 버전 승격 | `infra/mysql/validate_dart15_xml_mysql.py --promote` | 있음 |

Vector DB는 별도 경로로 갱신한다(`filing_pipeline_v2`, `bge_m3_indexer`). Financial DB와는 `corp_code`·`year`·`report_type`·`rcept_no`로 연결된다.
금융 KPI 문서 추출값은 `source_document_id`·`source_chunk_id`로 원본 XML·청크에 연결된다.
