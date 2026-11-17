# 백엔드 인계: 산업별 KPI 계층

- 작성일: 2026-10-09
- 운영 데이터 버전: `2026-10-09-dart25-bank-quality-v6` (KPI 미포함)
- KPI 검증본(staging): `db/staging/20261009_kpi_v7/financial.db` (`337bb2fc…e47a`)
- 상태: **KPI 운영 승격 보류.** 아래 4절 분석기 패치의 적용 여부를 백엔드 팀이 결정한 뒤 진행한다.
- 백엔드 코드(`src/llm/analyzer.py`, `src/retrieval/financial_retriever.py`)는 수정하지 않았다.

## 1. 두 가지 조회 경로 (KPI 사용은 선택)

| 경로 | 함수 / 테이블 | 용도 |
|---|---|---|
| 전체 재무계정 | `financial_retriever.get_financial_context(corp_name, year, report_type, account_name=None, fs_div="AUTO")` / `financial_facts` | 재고자산·매출채권·차입금 등 모든 계정. 기존과 동일 |
| 금융 KPI | `financial_retriever.get_financial_metrics(corp_name, year, report_type)` | 금융 5개사. 기존과 동일 |
| 일반산업 KPI(신규, 선택) | `industry_kpi.get_industry_kpis(corp_code, year=None, report_type=None, metric_codes=None, value_type=None)` | 20개사 표준 KPI와 출처 |
| 산업 그룹 KPI(신규) | `industry_kpi.get_industry_kpis_by_index("KRX_SEMI", …)` | 그룹 5개사 비교 |
| 보조(신규) | `industry_kpi.kpi_coverage()`, `industry_kpi.is_bank_company(corp_name)` | 확보율, 금융사 판별 |

KPI를 쓰지 않아도 전체 계정 조회는 그대로 동작한다. KPI 행은 원천 계정을 대체하거나 삭제하지 않는다.

## 2. KPI 정의서

### 공통 KPI (반도체·헬스케어·철강·자동차)

| metric_code | 이름 | 계산 / 원천 | 단위 | value_type |
|---|---|---|---|---|
| REVENUE | 매출액 | `ifrs-full_Revenue` | KRW | quarterly / cumulative / annual |
| OPERATING_INCOME | 영업이익 | `dart_OperatingIncomeLoss`(우선), `ifrs-full_ProfitLossFromOperatingActivities` | KRW | 〃 |
| NET_INCOME | 당기순이익 | `ifrs-full_ProfitLoss` | KRW | 〃 |
| OPERATING_CASH_FLOW | 영업활동현금흐름 | `ifrs-full_CashFlowsFromUsedInOperatingActivities` | KRW | 〃(3개월은 누적 차이) |
| CAPEX | 설비투자 | `capex_facts` 현금유출액(양수) | KRW | 〃 |
| TOTAL_ASSETS / TOTAL_LIABILITIES / TOTAL_EQUITY | 자산·부채·자본총계 | `ifrs-full_Assets` / `Liabilities` / `Equity` | KRW | point_in_time |
| OPERATING_MARGIN | 영업이익률 | 영업이익 / 매출액 | BPS | 입력과 같음 |
| DEBT_RATIO | 부채비율 | 부채총계 / 자본총계 (자본 > 0) | BPS | point_in_time |
| REVENUE_GROWTH_YOY | 매출 증가율 | 같은 value_type의 전년 동기 대비 (전년 > 0) | BPS | 입력과 같음 |

### 산업별 확장 KPI

| 산업 | metric_code | 계산 | 단위 |
|---|---|---|---|
| 반도체 | CAPEX_GROWTH_YOY | CAPEX 전년 동기 대비 | BPS |
| 반도체 | CAPEX_TO_OCF | CAPEX / 영업현금흐름 (영업CF > 0) | BPS |
| 반도체·자동차 | INVENTORIES | `ifrs-full_Inventories`(대체 `ifrs-full_InventoriesTotal`) | KRW |
| 반도체·자동차 | INVENTORY_CHANGE | 직전 보고 시점 대비 재고 증감 | KRW |
| 철강 | CAPEX_TO_REVENUE | CAPEX / 매출액 | BPS |

- 헬스케어 연구개발비는 본표에 공식 태그가 거의 없어 1차에서 보류했다.
- 금융 5개사 KPI(순이자이익·순수수료이익·대출채권·예수부채·신용손실비용 등)는 기존 그대로다.
  - 금융지주 4개사는 연결(CFS), 카카오뱅크는 별도(OFS) 기준이다.
  - 우리금융 순수 대출채권은 `LOANS_AT_AMORTISED_COST_NOTE` 지표다.

### 값 해석 규칙

- **BPS:** 0.01%다. `1088` = 10.88%이며, 원 값 비율 × 10,000을 반올림한 값이다. 정확한 분자·분모는 `source_values`에 있다.
- **value_type**

  | 값 | 의미 |
  |---|---|
  | `quarterly` | 해당 분기 3개월(FY 행의 quarterly는 4분기) |
  | `cumulative` | 연초부터 누적 |
  | `annual` | FY 연간 |
  | `point_in_time` | 기말 잔액 |

  같은 차트 계열에서 `quarterly`와 `cumulative`를 섞지 않는다.
- **source_kind**

  | 값 | 의미 |
  |---|---|
  | `DIRECT` | 공시 원천값 |
  | `DERIVED` | 계산값(계산 분기·비율·증가율·CAPEX 구성요소 합산) |
  | `DOCUMENT_EXTRACTED` | 공시 XML에서 추출한 값(금융) |

- **review_status:** `approved` 또는 `review_required`다. 검토 대상은 화면에서 구분 표시하거나 분석에서 제외한다.
  - 일반산업은 15행이 검토 대상이다. 원천 사실값의 부호 충돌과 CAPEX 검토 표시를 이어받은 것이다.
- **결손:** 값이 없으면 행이 없다. 0이나 보간값을 만들지 않는다.
  - 2023년 전년 대비 증가율은 2022년 원천이 없어 계산하지 않는다.
- **출처**
  - `source_rcept_no`: DART 접수번호
  - `source_fact_key`: 원천 사실값 키
  - `calculation_method`: 계산식
  - `source_values`: 입력값과 그 출처 키

## 3. 조회 예시

```python
from src.retrieval.industry_kpi import get_industry_kpis, get_industry_kpis_by_index

rows = get_industry_kpis("00126380", 2024, "FY",
                         metric_codes=("REVENUE", "OPERATING_MARGIN", "CAPEX_TO_OCF"),
                         value_type="annual")
# 삼성전자 2024 FY
#   REVENUE           300,870,903,000,000 KRW  DIRECT   approved  rcept 20250311001085
#   OPERATING_MARGIN  1088 BPS (10.88%)        DERIVED  OPERATING_INCOME / REVENUE x 10000 (bps)
#   CAPEX_TO_OCF      7044 BPS (70.44%)        DERIVED
# OPERATING_MARGIN source_values:
#   numerator   OPERATING_INCOME 32,725,961,000,000 (DIRECT, fact key 7161ff04…)
#   denominator REVENUE 300,870,903,000,000 (DIRECT, fact key 040a727e…)

get_industry_kpis_by_index("KRX_SEMI", 2025, "Q3",
                           metric_codes=("OPERATING_MARGIN",), value_type="quarterly")
# 2025 Q3 3개월 영업이익률(bp): 삼성전자 1414, 한미반도체 4080, SK하이닉스 4656, 주성엔지니어링 571, 원익IPS 1026
```

```sql
-- 산업 그룹의 분기 영업이익률 (SQLite/MySQL 공통)
SELECT c.company_name, v.year, v.report_type, v.value / 100.0 AS operating_margin_pct,
       v.review_status, v.source_rcept_no
FROM financial_metric_values v
JOIN companies c USING (corp_code)
JOIN krx_index_constituents k ON k.corp_code = v.corp_code AND k.is_current = 1
WHERE k.index_code = 'KRX_AUTO' AND v.metric_code = 'OPERATING_MARGIN' AND v.value_type = 'quarterly'
ORDER BY v.year, v.report_type, c.company_name;

-- KPI가 아닌 일반 계정(예: 매출채권)은 기존처럼 financial_facts에서 조회
SELECT f.original_account_nm, f.value_type, COALESCE(f.normalized_value, f.calculated_value) AS value
FROM financial_facts f JOIN periods p USING (period_id) JOIN companies c USING (corp_code)
WHERE c.company_name = '현대자동차' AND p.year = 2024 AND p.report_type = 'FY'
  AND f.original_account_nm LIKE '%매출채권%';
```

## 4. 변경 영향과 분석기 패치

### KPI 적재 시 데이터 계층 영향 (staging에서 측정)

| 항목 | 결과 |
|---|---|
| `get_financial_context`, fs_div 지정(CFS/OFS) | 25개사 전 기간 동일 |
| `get_financial_context`, fs_div AUTO | 24개사 동일(900건 중 879건). **에이치브이엠만 개선**(승인됨): 이전에는 CFS 기본값으로 항상 빈 결과였고, 이제 OFS 결과(별도 지정 조회와 동일)가 나온다 |
| `get_financial_metrics`, 금융 5개사 | 전 기간 동일 |
| 원천 테이블(`financial_facts`, `capex_facts`) | 변경 없음 |

### 분석기 영향과 패치

`src/llm/analyzer.py`의 `_financial_rows_for_account`는 회사에 지표 행이 하나라도 있으면 원천 계정 대신 지표 행을 사용한다.

| 상황 | 금융 5개사(30개 질의) | 일반 20개사(120개 질의) |
|---|---|---|
| 현재 운영 + 패치 적용 | 30/30 동일 | 120/120 동일 → **지금 적용해도 동작 변화 없음** |
| KPI 적재 + 패치 미적용 | 30/30 동일 | **101/120 변경**(20개사 모두 원천 계정 대신 KPI 행 사용) |
| KPI 적재 + 패치 적용 | 30/30 동일 | 114/120 동일. 나머지 6개는 모두 에이치브이엠 AUTO 개선에 따른 변화(빈 결과 → 실제 값) |

질의 세트: 분기별 매출·영업이익 추이, 2024 매출·영업이익·순이익·자산·부채, 이자수익·순이자이익을 25개사 각각에 수행했다.

- **패치 파일:** `docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
- **변경 내용**
  - `from src.retrieval.industry_kpi import is_bank_company` 추가
  - `_financial_rows_for_account`에서 `_finance_metric_context`를 `is_bank_company(corp_name)`인 경우에만 호출
  - 그 외 회사는 지금처럼 `get_financial_context`를 사용한다.
- **효과:** 분석기는 KPI 도입과 무관하게 현재 결과를 유지한다. 일반산업 KPI는 백엔드가 필요할 때 `industry_kpi` 모듈로 명시적으로 조회한다.

### 적용 가이드 (백엔드 담당자)

1. 패치를 검토한다: `git apply --check docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
2. 적용한다: `git apply docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
3. 테스트한다: `.venv/bin/python -m unittest discover -s tests -t .`
   - 현재 82개 통과 기준
   - `tests/test_industry_kpis.py`가 패치 동작을 재현해 검증한다.
4. DB 담당에게 KPI 운영 승격을 요청한다.
   - 버전 제안: `2026-10-09-dart25-kpi-v7`
   - MySQL 변경: profiles +20, definitions +11(공유 5개는 `industry_profile` 표시만 COMMON), mappings +52, metric_values +4,479
5. 되돌리기: `git apply -R docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
   - KPI가 운영에 적재된 뒤 되돌리면 일반사 분석 결과가 KPI 기반으로 바뀐다.

대안으로 `get_financial_metrics`에 금융 프로필 필터를 넣을 수도 있다. 다만 함수의 다른 호출부에도 영향을 주므로 분석기 패치를 권장한다.

## 5. KPI 검증 결과 (staging 6,033행)

| 검사 | 결과 |
|---|---|
| DIRECT 3,716행 = 원천 사실값·CAPEX | 위반 0 |
| DERIVED 2,141행이 기록된 입력으로 재계산 | 위반 0 |
| 정의 존재·단위 일치(KRW/BPS) | 위반 0 |
| 기간 성격과 value_type, FY 누적·분기 연간 금지 | 위반 0 |
| fs_div·scope = 회사 프로필 | 위반 0 |
| 출처 필수 항목(DIRECT 원천 키·접수번호·계정명, DERIVED 계산식·입력, 문서 위치) | 위반 0 |
| review_status 값 | 위반 0 |
| 비율 입력이 같은 기간·value_type·범위이고 저장값과 일치(증가율은 전년 동기, 증감은 직전 시점) | 위반 0 |
| Q1 3개월 = Q1 누적 | 불일치 0 |
| 독립 재계산(원천 계정에서 영업이익률·부채비율 300건 이상) | 일치 |
| 전체 재계산(materialize) | 6,033행 동일 |
| 재실행 / 중복 / FK / integrity | 변경 0 / 0 / 0 / ok |

구성:
- DIRECT KRW 3,716(검토 6)
- DERIVED KRW 887(검토 12)
- DERIVED BPS 1,254(검토 3)
- DOCUMENT_EXTRACTED 176(검토 2)

확보율 상세는 `docs/handoff/INDUSTRY_KPI_STAGING_20261009.md` 3절과 `db/staging/20261009_kpi_v7/kpi_coverage.json`에 있다.

## 6. 주의사항

- 비용 부호: 원천 DIRECT 값은 공시 부호 그대로다. 예: 신한 2024 이자비용은 Q1~Q3 음수, Q4 양수. 비율 KPI는 CAPEX를 유출 크기(양수)로 사용한다.
- 은행 비용 지표(`CREDIT_LOSS_EXPENSE`, `INTEREST_EXPENSE`, `FEE_COMMISSION_EXPENSE`)의 부호는 비용/환입을 뜻하지 않는다.
  - 2023~2025 금융 5개사 신용손실비용은 모든 기간이 실제 비용이고, 음수 28행도 비용이다.
  - 2026-10-09 `expense-sign-v9` 반영으로 `value`는 양수 = 비용, 음수 = 순환입이다. 절댓값 계산 임시 규칙은 폐기하고 `value`를 그대로 쓴다(`docs/handoff/EXPENSE_SIGN_V9_STAGING_20261009.md` 7절).
  - 예: 우리 2024 Q3 −4,783억 → Q4 +4,625억은 158억 감소다.
  - 상세: `docs/handoff/CREDIT_LOSS_SIGN_INVESTIGATION_20261009.md`
- 2023 Q1~Q3 일부 기업은 공식 태그가 없어 KPI가 없다(POSCO 매출, 현대차 순이익, 풍산 영업현금흐름). 원천 계정은 `financial_facts`에서 조회할 수 있다.
- 금융사와 일반사 KPI를 같은 지표로 비교할 때는 연결/별도와 산업 특성 차이를 표시한다.
