# 일반산업 KPI 계층 (kpi-v7 staging)과 quality-v6 운영 반영 결과

- 작성일: 2026-10-09
- 운영 버전: `2026-10-09-dart25-bank-quality-v6` (SQLite·MySQL current, `financial.db` `34d6f2ea…19fb`)
- KPI staging: `db/staging/20261009_kpi_v7/financial.db` (`337bb2fcb473ec7913a217cb50580a908416ffc32677bcfdf4159bb11be8e47a`)
- 상태
  - quality-v6: **운영 반영 완료**
  - 일반산업 KPI: **staging 검증 완료, 운영 미반영**(백엔드 패치 합의와 별도 승인 필요)
- 변경하지 않음: 원본 API·XML, Vector DB, FAISS, 임베딩, 백엔드 코드(`src/llm/analyzer.py`, `src/retrieval/financial_retriever.py`)

## 1. quality-v6 운영 반영

| 항목 | 결과 |
|---|---|
| 로컬 백업 | `backups/local/20261009_pre_quality_v6/financial.db` (이전 `7083ad56…`) |
| MySQL 백업 | `backups/mysql/20261009_pre_quality_v6/dart_big_in_pre_quality_v6.sql` (`b1c4c299…`, 테이블 23) |
| SQLite 교체 | `34d6f2ea…`, integrity ok, FK 0, 테스트 75개 통과, AppTest 예외 0·기업 25 |
| MySQL 적재 | `2026-10-09-dart25-bank-quality-v6`, 전체 검증 PASS 2회 후 current 승격 |
| SQLite–MySQL 일치 | 지표 1,554행(20개 컬럼), 계산 분기 사실값 13,554행 일치. 검증 Q4 슬롯 10행 approved |
| 데이터 수 | 사실값 62,608, CAPEX 469, 청크·임베딩 147,720, 원본 XML 399, 기업 25 |

## 2. KPI 계층 구현

새 테이블 없이 기존 지표 계층을 확장했다.

### 2.1 구성

- **설정:** `src/db/industry_kpi_config.py`. 산업 프로필, 지표 정의, 공식 태그 매핑, 산업별 KPI 묶음을 한 곳에서 관리한다.
- **생성:** `src/db/financial_metrics.py`
  - 공식 태그 매핑 → DIRECT
  - 계산된 분기값 → DERIVED
  - CAPEX → `capex_facts` 기반
  - 비율·증가율·증감 → `derive_general_kpis`
  - `materialize`(전체 재계산)와 `add_missing_fact_metrics`(빈 슬롯만 채움)가 같은 결과를 낸다.
- **staging 구축:** `python -m src.industry_kpi_pipeline --target <staging>` (재실행 시 변경 0)
- **선택 조회(신규, 읽기 전용):** `src/retrieval/industry_kpi.py`
  - `get_industry_kpis`, `get_industry_kpis_by_index`, `kpi_coverage`, `is_bank_company`
- **산업 프로필:** `company_financial_profiles`에 일반 20개사 추가
  - KRX 그룹 → SEMICONDUCTOR / HEALTHCARE / STEEL / AUTOMOTIVE
  - CFS 19개사, 에이치브이엠 OFS
  - `capex_applicable=1`

### 2.2 KPI 목록

| 구분 | metric_code | 계산 | 단위 |
|---|---|---|---|
| 공통 (4개 산업) | REVENUE | `ifrs-full_Revenue` | KRW |
| | OPERATING_INCOME | `dart_OperatingIncomeLoss` > `ifrs-full_ProfitLossFromOperatingActivities` | KRW |
| | NET_INCOME | `ifrs-full_ProfitLoss` | KRW |
| | OPERATING_CASH_FLOW | `ifrs-full_CashFlowsFromUsedInOperatingActivities` | KRW |
| | CAPEX | `capex_facts` 현금유출액(양수) | KRW |
| | TOTAL_ASSETS / TOTAL_LIABILITIES / TOTAL_EQUITY | `ifrs-full_Assets` / `Liabilities` / `Equity` | KRW |
| | OPERATING_MARGIN | 영업이익 / 매출액 | BPS |
| | DEBT_RATIO | 부채총계 / 자본총계(같은 시점, 자본 > 0) | BPS |
| | REVENUE_GROWTH_YOY | 같은 값 구분의 전년 동기 대비(전년 > 0) | BPS |
| 반도체 | CAPEX_GROWTH_YOY, CAPEX_TO_OCF(영업CF > 0), INVENTORIES, INVENTORY_CHANGE | | BPS / KRW |
| 철강 | CAPEX_TO_REVENUE | | BPS |
| 자동차 | INVENTORIES, INVENTORY_CHANGE | | KRW |
| 헬스케어 | 공통 KPI만 | 연구개발비는 본표 태그 부족으로 보류 | |

- BPS = 0.01%(예: 1234 = 12.34%). 정확한 분자·분모는 `source_values`에 있다.
- 공유 지표 5개(OPERATING_INCOME, NET_INCOME, TOTAL_ASSETS/LIABILITIES/EQUITY)는 정의를 하나만 둔다.
  - `industry_profile` 표시를 BANKING에서 COMMON으로 바꿨다.
  - 금융업 정의 중복은 없다.
- 기록 항목: 값, 단위, `value_type`(3개월·누적·연간·시점 잔액), `fs_div`, `scope`, `source_kind`, `source_type`, 접수번호, 원천 계정 ID·계정명, `source_fact_key`, 계산식, 입력값(`source_values`), `review_status`

### 2.3 staging 변경 (운영 quality-v6 대비)

| 테이블 | 변경 |
|---|---|
| company_financial_profiles | +20(금융 5개사 행 불변) |
| financial_metric_definitions | +11, 공유 5개는 `industry_profile`만 COMMON |
| financial_metric_mappings | +52 |
| financial_metric_values | +4,479(DIRECT 2,502 / DERIVED 1,977). 기존 1,554행 변경 0 |
| financial_facts, capex_facts, 그 밖의 테이블 | 변경 없음 |

## 3. 기업별·기간별 KPI 확보율 (보고 기간 수)

"기간"은 해당 기업의 원천 보고 기간 수다. 에이치브이엠은 상장 전이 없어 7개다. 셀의 숫자는 그 KPI 값이 있는 기간 수이고, `–`는 해당 산업 KPI가 아니라는 뜻이다.

| 산업 | 기업 | 기간 | 매출 | 영업익 | 순익 | 영업CF | CAPEX | 자산 | 부채 | 자본 | 영업이익률 | 부채비율 | 매출증가율 | CAPEX증가율 | CAPEX/영업CF | CAPEX/매출 | 재고 | 재고증감 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 반도체 | SK하이닉스 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 11 | – | 12 | 11 |
| 반도체 | 삼성전자 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 12 | – | 12 | 11 |
| 반도체 | 원익IPS | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 9 | – | 12 | 11 |
| 반도체 | 주성엔지니어링 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 8 | – | 12 | 11 |
| 반도체 | 한미반도체 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 11 | – | 12 | 11 |
| 헬스케어 | HLB | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | – | – |
| 헬스케어 | 삼성바이오로직스 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | – | – |
| 헬스케어 | 셀트리온 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | – | – |
| 헬스케어 | 알테오젠 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | – | – |
| 헬스케어 | 유한양행 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | – | – |
| 철강 | POSCO홀딩스 | 12 | 9 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 9 | 12 | 5 | – | – | 9 | – | – |
| 철강 | 고려아연 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | 12 | – | – |
| 철강 | 에이치브이엠 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 3 | – | – | 7 | – | – |
| 철강 | 풍산 | 12 | 12 | 12 | 12 | 10 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | 12 | – | – |
| 철강 | 현대제철 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | 12 | – | – |
| 자동차 | HL만도 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | 12 | 11 |
| 자동차 | 기아 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | 12 | 11 |
| 자동차 | 한국타이어앤테크놀로지 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | 12 | 11 |
| 자동차 | 현대모비스 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | 12 | 11 |
| 자동차 | 현대자동차 | 12 | 12 | 12 | 9 | 12 | 12 | 12 | 12 | 12 | 12 | 12 | 8 | – | – | – | 12 | 11 |


결손 원인:
- **2022년 원천 없음:** 2023년의 전년 대비 증가율, 2023 Q1 재고 증감
- **공식 태그 없는 기간(2023 Q1~Q3):** POSCO 매출, 현대차 순이익, 풍산 영업현금흐름. 원천에는 비공식 키로 존재한다.
- **분모 0 이하 제외 규칙:** CAPEX/영업CF 일부 기간
- 기업별 결손 기간 목록: `db/staging/20261009_kpi_v7/kpi_coverage.json`

## 4. 정확성과 출처 검증

- 감사 모듈(KPI·CAPEX 재계산 포함): DIRECT 3,716행이 원천 사실값·CAPEX와 일치, DERIVED 2,141행이 기록된 입력으로 재현(위반 0)
- 독립 재계산: 영업이익률·부채비율 300건 이상을 지표 테이블이 아닌 원천 계정에서 직접 계산해 일치를 확인했다.
- 전체 재계산(materialize): 6,033행의 키·값·검토상태 동일
- 자연키 중복 0, integrity ok, FK 0, 재실행 변경 0
- review_required 행: 원천 사실값의 부호 충돌·CAPEX 검토 플래그를 상속(영업CF 7, 순이익 2, 영업이익 1, CAPEX 2 등). 이를 쓰는 비율도 review_required다.

## 5. 기존 조회 영향

| 조회 | KPI staging 전후 | 비고 |
|---|---|---|
| `get_financial_context`(fs_div CFS/OFS 지정) | 25개사 전 기간 동일 | 전체 계정 조회 유지 |
| `get_financial_metrics`(금융 5개사) | 전 기간 동일 | 금융 KPI 조회 방식 유지 |
| `get_financial_context`(fs_div AUTO) | 에이치브이엠만 변경(0행 → 206행) | 현재 프로필이 없어 CFS 기본값으로 조회되는데, OFS만 있는 회사라 항상 빈 결과다. 프로필(OFS) 추가 시 정상 조회된다. 사실상 오류 수정이지만 결과가 바뀌므로 확인이 필요하다 |
| 분석기(현재 코드, 패치 미적용) | 일반 20개사 모두 변경 | 지표 행이 생기면 원천 계정 대신 KPI 행을 사용한다 |
| 분석기(아래 패치 적용) | 일반 20개사 결과 동일 | 테스트로 확인 |

## 6. 백엔드 협의가 필요한 최소 수정 (미적용)

- 패치: `docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
- 위치: `src/llm/analyzer.py`의 `_financial_rows_for_account`(+ import 1줄)
- 내용: 지표 계층(`_finance_metric_context`)을 금융 프로필 회사에만 사용한다(`is_bank_company`). 일반사는 지금처럼 `get_financial_context`를 쓴다.
- 영향
  - 금융 5개사: 동작 동일
  - 일반 20개사: KPI 도입 전후 결과 동일
  - KPI 적재 전에 적용해도 동작이 바뀌지 않는다. 현재 일반사 지표 행이 0이기 때문이다.
- 적용 가이드
  1. 백엔드 담당자가 패치를 검토한다.
  2. `git apply docs/handoff/patches/analyzer_bank_only_metric_layer.patch`
  3. `python -m unittest discover -s tests -t .`
  4. KPI 운영 적재 승인을 요청한다.
- 대안: `get_financial_metrics`에 금융 프로필 필터를 넣는 방식도 가능하다. 다만 다른 호출부에도 영향을 주므로 분석기 쪽 패치를 권장한다.

## 7. 다음 운영 반영에 필요한 사항

1. 백엔드 패치 적용 합의(6절). 미적용 상태로 KPI를 운영에 적재하면 일반사 분석 결과가 바뀐다.
2. 에이치브이엠 AUTO 조회 변경 수용 여부
3. 승인 후 진행
   - 로컬 백업 → staging(`337bb2fc…`) 교체 → 테스트·AppTest
   - MySQL 덤프 → 버전 `2026-10-09-dart25-kpi-v7` 적재 → 전체 검증 → 승격
   - MySQL 변경: profiles +20, definitions +11(공유 5개 메타 변경), mappings +52, metric_values +4,479
4. 후속 KPI 보강
   - 공식 태그 없는 2023 기간의 회사별 예외 매핑을 검증해 보강
   - 비용 부호를 유출 크기로 정규화한 표시용 지표 검토(예: 신한 2024 이자비용 Q1~Q3 음수 / Q4 양수 혼재)
