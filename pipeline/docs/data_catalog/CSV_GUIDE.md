# CSV 사용 설명서

이 문서는 `data/mapping/`과 `data/processed/quality/`에서 실제 데이터가 있는 주요 CSV 19개를 설명합니다. 예시는 2026-10-02 확인 시점의 실제 행에서 가져왔습니다. 빈 파일인 `account_mapping.csv`와 존재하지 않는 `capex_v2_validation.csv`는 설명 대상에서 제외했습니다.

## CSV 역할별 구분

### 1. 계정 표준화 관련 CSV

기업마다 다른 계정 ID와 명칭을 어떤 표준계정으로 묶는지 보여줍니다. core는 서비스 범위, prioritized는 전체 검토 순서, review는 충돌·예외에 초점을 둡니다.

포함 파일: `account_mapping_core.csv`, `account_mapping_draft.csv`, `account_mapping_prioritized.csv`, `account_mapping_review.csv`, `account_mapping_review_prioritized.csv`

### 2. CAPEX 규칙·검증 CSV

직접 유형자산 취득 계정과 세부 계정 합산 방식, 최종 CAPEX 값을 추적합니다.

포함 파일: `capex_component_mapping_v2.csv`, `capex_mapping_validation.csv`, `capex_final_validation.csv`

### 3. 재무 데이터 검증 CSV

핵심 계정 커버리지, 기간값 이상 가능성, DB 승격 전후 값 보존 여부를 확인합니다.

포함 파일: `financial_core_validation.csv`, `financial_quality_report.csv`, `financial_final_regression.csv`

### 4. 공시·Vector DB 검증 CSV

공시 원문 수집부터 섹션·청크 생성과 실제 검색 결과까지 출처를 따라갑니다.

포함 파일: `filing_collection_report.csv`, `filing_quality_report.csv`, `search_validation_report.csv`

### 5. 검색 성능 비교 CSV

동일 질의에서 검색 방식별 상위 결과를 비교합니다.

포함 파일: `vector_search_comparison.csv`

### 6. LLM·Smoke Test 검증 CSV

숫자·단위·기업·연도·접수번호 근거와 최종 서비스 동작을 확인합니다.

포함 파일: `gemini_llm_validation_summary.csv`, `gemini_llm_validation_summary_v2.csv`, `final_db_llm_validation_summary.csv`, `post_promotion_smoke_test_summary.csv`

## 공통 용어 사전

| 용어 | 프로젝트에서의 의미 |
|---|---|
| account_id | DART/XBRL 계정 식별자입니다. 기업별 확장 태그나 코드 미사용 계정이 있을 수 있어 ID 하나만으로 모든 계정을 설명하지는 않습니다. |
| account_nm / original_account_nm | DART에 실제 표시된 계정명입니다. 같은 경제적 의미라도 기업·보고서에 따라 표현이 달라질 수 있습니다. |
| standard_account_nm | 서로 다른 원본 계정 표현을 프로젝트 내부의 같은 경제적 의미로 통일한 이름입니다. |
| sj_div | 재무상태표 BS, 손익계산서 IS, 포괄손익계산서 CIS, 현금흐름표 CF, 자본변동표 SCE를 구분합니다. |
| fs_div | CFS는 연결재무제표, OFS는 별도재무제표입니다. 현재 핵심 매핑 예시는 CFS입니다. |
| report_type | Q1은 1분기, H1은 반기, Q3는 3분기, FY는 사업보고서입니다. |
| rcept_no | Financial DB 수치와 실제 공시 원문을 연결하는 DART 접수번호입니다. |
| period_type | 값이 특정 시점 잔액인지 기간 흐름인지 나타내는 개념입니다. 대상 주요 CSV에는 이 이름의 컬럼이 없으며 value_type과 sj_div로 기간 의미를 확인합니다. |
| value_source | direct는 DART의 유형자산 취득 총액 계정을 직접 사용하고, derived는 총액이 없어 승인된 세부 유형자산 취득 계정을 합산한 값입니다. |
| review_required | True 또는 1이면 사람이 추가 확인해야 합니다. False 또는 0이면 해당 검증 규칙상 추가 검토 대상이 아닙니다. |
| validation_status | PASS는 해당 자동검사를 통과했다는 뜻이고 FAIL은 확인이 필요하다는 뜻입니다. |

## 계정 매핑 3개 파일의 차이

- `account_mapping_core.csv`: 서비스 MVP에서 사용하는 24개 핵심 표준계정과 승인·검토 대상 연결입니다.
- `account_mapping_prioritized.csv`: 668개 전체 매핑 후보에 검토 우선순위와 자동 매핑 후보 여부를 붙인 파일입니다.
- `account_mapping_review_prioritized.csv`: 동일 ID의 여러 명칭처럼 충돌이 확인된 371개 행만 모아 우선순위를 붙인 파일입니다.

우선순위 값은 실제 파일에서 `high`, `medium`, `low`로 사용됩니다. high는 재무제표 구분 혼동 가능성 등 먼저 확인할 항목, medium은 계정명 변형이나 제한된 기업 관측처럼 검토가 필요한 항목, low는 상대적으로 위험이 낮은 항목입니다. `auto_mapping_candidate=True`라도 최종 승인을 뜻하지는 않습니다.

## CAPEX의 direct와 derived

- `direct`: DART에 `유형자산의 취득` 총액 계정이 있어 그 값을 우선 사용합니다. 원익IPS 2024년 FY는 21,432,773,671원이며 `rcept_no=20250325000491`입니다.
- `derived`: 직접 총액 계정이 없어 토지·건물·기계장치·건설중인자산 등 승인된 세부 취득 계정을 합산합니다. 주성엔지니어링 2024년 H1 누적 CAPEX는 11,066,442,727원이며 2분기 단독값은 H1-Q1로 계산한 5,023,179,778원입니다.

## account_mapping_core.csv

### 이 파일의 목적

MVP 분석에서 실제 사용하는 24개 핵심 표준계정과 승인된 원본 계정 연결을 정리합니다. 실제 행은 78개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| standard_account_nm | 기업마다 다른 계정 표현을 프로젝트 내부의 같은 경제적 의미로 통일한 이름입니다. | 매출액 |
| original_account_nm | 표준화하기 전 DART 원본 계정명입니다. | 매출액 |
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | ifrs-full_Revenue |
| sj_div | 계정이 속한 재무제표 종류입니다. | CIS |
| fs_div | 연결재무제표와 별도재무제표를 구분합니다. | CFS |
| mapping_status | 계정 매핑이 생성되거나 승인된 방식을 나타냅니다. | approved |
| mapping_confidence | 핵심 매핑의 신뢰 수준입니다. | high |
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | False |

### 실제 예시 행 1개

```yaml
standard_account_nm: 매출액
original_account_nm: 매출액
account_id: ifrs-full_Revenue
sj_div: CIS
fs_div: CFS
mapping_status: approved
mapping_confidence: high
review_required: False
```

### 이 한 행을 사람 말로 해석하면

DART의 '매출액' 계정을 프로젝트에서는 '매출액'으로 사용하며, approved 상태이고 추가 검토 여부는 False라는 뜻입니다.

### 이 파일을 언제 보면 되는가

서비스의 24개 핵심 표준계정이 어떤 원본 계정과 연결되는지 확인할 때 봅니다.

## account_mapping_draft.csv

### 이 파일의 목적

전체 DART 계정에서 자동 생성한 표준화 후보와 관측 범위를 보관합니다. 실제 행은 668개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| standard_account_name | 계정 매핑 후보가 가리키는 프로젝트 표준 계정명입니다. | 계약부채 |
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | dart_CurrentFirmCommitmentLiabilities |
| sj_div | 계정이 속한 재무제표 종류입니다. | BS |
| mapping_status | 계정 매핑이 생성되거나 승인된 방식을 나타냅니다. | auto_account_id |
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | False |
| source_account_names | 계산 또는 합산에 사용한 원천 계정명 목록입니다. | 계약부채 |

### 실제 예시 행 1개

```yaml
standard_account_name: 계약부채
account_id: dart_CurrentFirmCommitmentLiabilities
sj_div: BS
mapping_status: auto_account_id
review_required: False
source_account_names: 계약부채
```

### 이 한 행을 사람 말로 해석하면

dart_CurrentFirmCommitmentLiabilities 계정이 '계약부채' 후보로 정리됐고, 1개 기업에서 7건 관측됐다는 뜻입니다.

### 이 파일을 언제 보면 되는가

계정 표준화 규칙과 후보·검토 상태를 확인할 때 봅니다.

## account_mapping_prioritized.csv

### 이 파일의 목적

전체 매핑 후보에 검토 우선순위와 자동 매핑 가능 여부를 추가합니다. 실제 행은 668개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| standard_account_name | 계정 매핑 후보가 가리키는 프로젝트 표준 계정명입니다. | 계약부채 |
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | dart_CurrentFirmCommitmentLiabilities |
| sj_div | 계정이 속한 재무제표 종류입니다. | BS |
| mapping_status | 계정 매핑이 생성되거나 승인된 방식을 나타냅니다. | auto_account_id |
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | False |
| review_priority | 사람 검토가 필요한 순서를 나타냅니다. | high |
| source_account_names | 계산 또는 합산에 사용한 원천 계정명 목록입니다. | 계약부채 |

### 실제 예시 행 1개

```yaml
standard_account_name: 계약부채
account_id: dart_CurrentFirmCommitmentLiabilities
sj_div: BS
mapping_status: auto_account_id
review_required: False
review_priority: high
source_account_names: 계약부채
```

### 이 한 행을 사람 말로 해석하면

dart_CurrentFirmCommitmentLiabilities 계정이 '계약부채' 후보로 정리됐고, 1개 기업에서 7건 관측됐다는 뜻입니다.

### 이 파일을 언제 보면 되는가

전체 계정 후보 중 무엇부터 검토할지 정할 때 봅니다.

## account_mapping_review.csv

### 이 파일의 목적

동일 ID의 여러 명칭 등 사람이 확인해야 할 계정 충돌을 모읍니다. 실제 행은 371개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | dart_AcquisitionOfTreasuryShares |
| account_nm | DART 원문에 표시된 계정명입니다. | 자기주식의 취득 \| 자기주식의 취득으로 인한 현금의 유출 |
| sj_div | 계정이 속한 재무제표 종류입니다. | CF |
| fs_div | 연결재무제표와 별도재무제표를 구분합니다. | CFS |
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | True |
| issue_type | 매핑 검토가 필요한 문제의 유형입니다. | same_id_multiple_names |

### 실제 예시 행 1개

```yaml
account_id: dart_AcquisitionOfTreasuryShares
account_nm: 자기주식의 취득 | 자기주식의 취득으로 인한 현금의 유출
sj_div: CF
fs_div: CFS
review_required: True
issue_type: same_id_multiple_names
```

### 이 한 행을 사람 말로 해석하면

same_id_multiple_names 문제로 dart_AcquisitionOfTreasuryShares 계정의 명칭이 여러 형태로 관측되어 사람이 확인해야 한다는 뜻입니다.

### 이 파일을 언제 보면 되는가

계정 표준화 규칙과 후보·검토 상태를 확인할 때 봅니다.

## account_mapping_review_prioritized.csv

### 이 파일의 목적

검토 대상 충돌을 high·medium·low 우선순위로 정렬할 수 있게 보강합니다. 실제 행은 371개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | dart_AcquisitionOfTreasuryShares |
| account_nm | DART 원문에 표시된 계정명입니다. | 자기주식의 취득 \| 자기주식의 취득으로 인한 현금의 유출 |
| sj_div | 계정이 속한 재무제표 종류입니다. | CF |
| fs_div | 연결재무제표와 별도재무제표를 구분합니다. | CFS |
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | True |
| review_priority | 사람 검토가 필요한 순서를 나타냅니다. | high |
| issue_type | 매핑 검토가 필요한 문제의 유형입니다. | same_id_multiple_names |

### 실제 예시 행 1개

```yaml
account_id: dart_AcquisitionOfTreasuryShares
account_nm: 자기주식의 취득 | 자기주식의 취득으로 인한 현금의 유출
sj_div: CF
fs_div: CFS
review_required: True
review_priority: high
issue_type: same_id_multiple_names
```

### 이 한 행을 사람 말로 해석하면

same_id_multiple_names 문제로 dart_AcquisitionOfTreasuryShares 계정의 명칭이 여러 형태로 관측되어 사람이 확인해야 한다는 뜻입니다.

### 이 파일을 언제 보면 되는가

충돌하거나 애매한 계정을 우선순위대로 사람이 검토할 때 봅니다.

## capex_component_mapping_v2.csv

### 이 파일의 목적

CAPEX 직접 계정·합산 대상 세부 계정·제외 계정 규칙을 정의합니다. 실제 행은 13개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities |
| classification | CAPEX 규칙에서 direct·component·exclude 중 어느 역할인지 나타냅니다. | direct |
| include_in_narrow_capex | 해당 계정을 좁은 의미의 유형자산 취득 CAPEX에 포함하는지 표시합니다. | True |
| account_name | 계정명 값입니다. | 유형자산의 취득 |
| note | 해당 행의 산출 또는 검증 방법을 설명하는 메모입니다. | 직접 계정 최우선 |

### 실제 예시 행 1개

```yaml
account_id: ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities
classification: direct
include_in_narrow_capex: True
```

### 이 한 행을 사람 말로 해석하면

유형자산의 취득 계정은 CAPEX 규칙에서 direct로 분류되며 좁은 CAPEX 포함 여부는 True입니다.

### 이 파일을 언제 보면 되는가

계정 표준화 규칙과 후보·검토 상태를 확인할 때 봅니다.

## capex_final_validation.csv

### 이 파일의 목적

최종 Financial DB에 적재한 CAPEX direct·derived 값과 산출 근거를 검증합니다. 실제 행은 120개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | 0 |
| corp_name | 공시 대상 기업의 이름입니다. | 주성엔지니어링 |
| year | 재무정보가 속하는 사업연도입니다. | 2024 |
| report_type | 공시 보고기간 종류입니다. | H1 |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20240814003342 |
| capex_method | CAPEX를 직접 계정으로 선택했는지 세부 유형자산 취득 계정을 합산했는지 나타냅니다. | component_fallback |
| value_source | 값이 DART 직접 계정인지 세부 계정 합산 파생값인지 나타냅니다. | derived |
| value_type | 값이 시점 잔액·분기 단독·누적·연간 중 무엇인지 나타냅니다. | quarterly |

### 실제 예시 행 1개

```yaml
review_required: 0
corp_name: 주성엔지니어링
year: 2024
report_type: H1
rcept_no: 20240814003342
capex_method: component_fallback
value_source: derived
value_type: quarterly
capex_value: 5023179778
raw_value: 11066442727
calculated_value: 5023179778
is_derived: 1
```

### 이 한 행을 사람 말로 해석하면

주성엔지니어링 2024년 H1 CAPEX는 derived 방식으로 5023179778원이며, is_derived=1, is_calculated=1입니다.

### 이 파일을 언제 보면 되는가

CAPEX가 direct인지 derived인지, 어떤 원천 계정과 계산을 사용했는지 확인할 때 봅니다.

## capex_mapping_validation.csv

### 이 파일의 목적

CAPEX 매핑 로직이 기업·연도·보고서별로 어떤 값을 만들었는지 확인합니다. 실제 행은 120개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| review_required | 사람이 추가 확인해야 하는 행인지 표시합니다. | 0 |
| corp_name | 공시 대상 기업의 이름입니다. | 원익IPS |
| year | 재무정보가 속하는 사업연도입니다. | 2024 |
| report_type | 공시 보고기간 종류입니다. | FY |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20250325000491 |
| capex_method | CAPEX를 직접 계정으로 선택했는지 세부 유형자산 취득 계정을 합산했는지 나타냅니다. | direct_account |
| value_source | 값이 DART 직접 계정인지 세부 계정 합산 파생값인지 나타냅니다. | direct |
| value_type | 값이 시점 잔액·분기 단독·누적·연간 중 무엇인지 나타냅니다. | annual |

### 실제 예시 행 1개

```yaml
review_required: 0
corp_name: 원익IPS
year: 2024
report_type: FY
rcept_no: 20250325000491
capex_method: direct_account
value_source: direct
value_type: annual
capex_value: 21432773671
raw_value: 21432773671
normalized_value: 21432773671
is_derived: 0
```

### 이 한 행을 사람 말로 해석하면

원익IPS 2024년 FY CAPEX는 direct 방식으로 21432773671원이며, is_derived=0, is_calculated=0입니다.

### 이 파일을 언제 보면 되는가

계정 표준화 규칙과 후보·검토 상태를 확인할 때 봅니다.

## filing_collection_report.csv

### 이 파일의 목적

공시 원문 수집 성공 여부와 실패 사유를 공시 단위로 기록합니다. 실제 행은 60개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| corp_code | DART가 부여한 8자리 기업 식별 코드입니다. | 00126380 |
| year | 재무정보가 속하는 사업연도입니다. | 2023 |
| report_name | DART 공시에 표시된 보고서 이름입니다. | 사업보고서 |
| report_type | 공시 보고기간 종류입니다. | FY |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20240312000736 |
| status | 수집·검증·테스트 처리 결과입니다. | ok |
| reason | 처리 실패 또는 필터 적용 사유입니다. |  |

### 실제 예시 행 1개

```yaml
corp_name: 삼성전자
corp_code: 00126380
year: 2023
report_name: 사업보고서
report_type: FY
rcept_no: 20240312000736
status: ok
```

### 이 한 행을 사람 말로 해석하면

삼성전자 2023년 FY 공시 원문 수집 결과가 ok라는 뜻입니다.

### 이 파일을 언제 보면 되는가

공시 XML 수집과 파싱·청킹 상태를 확인할 때 봅니다.

## filing_quality_report.csv

### 이 파일의 목적

수집한 XML의 섹션·청크·메타데이터 품질을 공시 단위로 검사합니다. 실제 행은 60개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| corp_code | DART가 부여한 8자리 기업 식별 코드입니다. | 00126380 |
| year | 재무정보가 속하는 사업연도입니다. | 2023 |
| report_type | 공시 보고기간 종류입니다. | FY |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20240312000736 |
| status | 수집·검증·테스트 처리 결과입니다. | ok_with_filters |
| reason | 처리 실패 또는 필터 적용 사유입니다. |  |
| section_count | 섹션 수을 집계한 건수입니다. | 134 |

### 실제 예시 행 1개

```yaml
corp_name: 삼성전자
corp_code: 00126380
year: 2023
report_type: FY
rcept_no: 20240312000736
status: ok_with_filters
section_count: 134
chunk_count: 584
```

### 이 한 행을 사람 말로 해석하면

삼성전자 공시에서 134개 섹션과 584개 청크를 만들었고 품질 상태가 ok_with_filters라는 뜻입니다.

### 이 파일을 언제 보면 되는가

공시 XML 수집과 파싱·청킹 상태를 확인할 때 봅니다.

## final_db_llm_validation_summary.csv

### 이 파일의 목적

최종 DB 기반 7개 Gemini 질문의 근거·숫자·기간·기업 일치 여부를 요약합니다. 실제 행은 7개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| report_type | 공시 보고기간 종류입니다. | FY |
| question | LLM 또는 검색 품질을 검증하기 위해 사용한 질문입니다. | 삼성전자 2025년 매출액과 영업이익은 얼마인가? |
| route | 질문이 Financial·filing·integrated 중 어느 데이터 경로로 처리됐는지 나타냅니다. | financial |
| validation_status | 검증 항목의 최종 통과 여부입니다. | PASS |

### 실제 예시 행 1개

```yaml
corp_name: 삼성전자
report_type: FY
question: 삼성전자 2025년 매출액과 영업이익은 얼마인가?
route: financial
validation_status: PASS
```

### 이 한 행을 사람 말로 해석하면

질문 또는 테스트 '삼성전자 2025년 매출액과 영업이익은 얼마인가?'의 최종 검증 상태가 PASS라는 뜻입니다.

### 이 파일을 언제 보면 되는가

LLM 답변 또는 최종 서비스 통합검증이 통과했는지 확인할 때 봅니다.

## financial_core_validation.csv

### 이 파일의 목적

핵심 표준계정별 기업 커버리지와 값 결측·계산 건수를 집계합니다. 실제 행은 36개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| standard_account_nm | 기업마다 다른 계정 표현을 프로젝트 내부의 같은 경제적 의미로 통일한 이름입니다. | 감가상각비 |
| sj_div | 계정이 속한 재무제표 종류입니다. | CF |
| fs_div | 연결재무제표와 별도재무제표를 구분합니다. | CFS |
| record_count | 레코드 수을 집계한 건수입니다. | 49 |
| company_count | 기업 수을 집계한 건수입니다. | 3 |

### 실제 예시 행 1개

```yaml
standard_account_nm: 감가상각비
sj_div: CF
fs_div: CFS
```

### 이 한 행을 사람 말로 해석하면

감가상각비 계정이 3개 기업에서 49건 존재하고 결측값은 13건이라는 뜻입니다.

### 이 파일을 언제 보면 되는가

Financial DB의 핵심 계정 커버리지와 이상값·회귀변경을 확인할 때 봅니다.

## financial_final_regression.csv

### 이 파일의 목적

승격 전후 Financial DB 레코드 값을 비교해 의도하지 않은 변경을 찾습니다. 실제 행은 10,969개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| standard_account_nm | 기업마다 다른 계정 표현을 프로젝트 내부의 같은 경제적 의미로 통일한 이름입니다. | 자본잉여금 |
| sj_div | 계정이 속한 재무제표 종류입니다. | BS |
| fs_div | 연결재무제표와 별도재무제표를 구분합니다. | CFS |
| corp_name | 공시 대상 기업의 이름입니다. | SK하이닉스 |
| year | 재무정보가 속하는 사업연도입니다. | 2023 |
| report_type | 공시 보고기간 종류입니다. | FY |
| value_source | 값이 DART 직접 계정인지 세부 계정 합산 파생값인지 나타냅니다. | reported |
| value_type | 값이 시점 잔액·분기 단독·누적·연간 중 무엇인지 나타냅니다. | point_in_time |

### 실제 예시 행 1개

```yaml
standard_account_nm: 자본잉여금
sj_div: BS
fs_div: CFS
corp_name: SK하이닉스
year: 2023
report_type: FY
value_source: reported
value_type: point_in_time
validation_status: PASS
```

### 이 한 행을 사람 말로 해석하면

SK하이닉스의 자본잉여금 값은 승격 전후 차이가 0원이며 검증 결과가 PASS라는 뜻입니다.

### 이 파일을 언제 보면 되는가

Financial DB의 핵심 계정 커버리지와 이상값·회귀변경을 확인할 때 봅니다.

## financial_quality_report.csv

### 이 파일의 목적

분기·누적 관계 등 재무 데이터의 이상 가능성을 행 단위로 기록합니다. 실제 행은 61개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| account_id | DART/XBRL이 재무 계정을 식별하기 위해 사용하는 태그입니다. | ifrs-full_OtherComprehensiveIncomeThatWillNotBeReclassifiedToProfitOrLossNetOfTax |
| account_nm | DART 원문에 표시된 계정명입니다. | 후속적으로 당기손익으로 재분류되지 않는 포괄손익 |
| sj_div | 계정이 속한 재무제표 종류입니다. | CIS |
| issue_type | 매핑 검토가 필요한 문제의 유형입니다. | quarterly_exceeds_cumulative |
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| corp_code | DART가 부여한 8자리 기업 식별 코드입니다. | 00126380 |
| year | 재무정보가 속하는 사업연도입니다. | 2025 |
| report_type | 공시 보고기간 종류입니다. | H1 |

### 실제 예시 행 1개

```yaml
account_id: ifrs-full_OtherComprehensiveIncomeThatWillNotBeReclassifiedToProfitOrLossNetOfTax
account_nm: 후속적으로 당기손익으로 재분류되지 않는 포괄손익
sj_div: CIS
issue_type: quarterly_exceeds_cumulative
corp_name: 삼성전자
corp_code: 00126380
year: 2025
report_type: H1
rcept_no: 20250814003156
value_type: quarterly
```

### 이 한 행을 사람 말로 해석하면

삼성전자 2025년 H1의 후속적으로 당기손익으로 재분류되지 않는 포괄손익에서 quarterly_exceeds_cumulative 경고가 발견됐다는 뜻입니다.

### 이 파일을 언제 보면 되는가

Financial DB의 핵심 계정 커버리지와 이상값·회귀변경을 확인할 때 봅니다.

## gemini_llm_validation_summary.csv

### 이 파일의 목적

초기 5개 Gemini 질문의 기본 근거 일치 검사를 요약합니다. 실제 행은 5개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| status | 수집·검증·테스트 처리 결과입니다. | pass |
| question | LLM 또는 검색 품질을 검증하기 위해 사용한 질문입니다. | 삼성전자 2025년 매출액과 영업이익은 얼마인가? |
| route | 질문이 Financial·filing·integrated 중 어느 데이터 경로로 처리됐는지 나타냅니다. | financial |
| question_id | 질문 ID 값입니다. | 1 |
| numeric_hallucination | 답변 숫자가 제공 근거 안에 있는지 검사한 결과입니다. | True |

### 실제 예시 행 1개

```yaml
status: pass
question: 삼성전자 2025년 매출액과 영업이익은 얼마인가?
route: financial
```

### 이 한 행을 사람 말로 해석하면

질문 또는 테스트 '삼성전자 2025년 매출액과 영업이익은 얼마인가?'의 최종 검증 상태가 pass라는 뜻입니다.

### 이 파일을 언제 보면 되는가

LLM 답변 또는 최종 서비스 통합검증이 통과했는지 확인할 때 봅니다.

## gemini_llm_validation_summary_v2.csv

### 이 파일의 목적

단위 정규화 보완 후 5개 Gemini 질문의 숫자·단위 검증을 요약합니다. 실제 행은 5개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| question | LLM 또는 검색 품질을 검증하기 위해 사용한 질문입니다. | 삼성전자 2025년 매출액과 영업이익은 얼마인가? |
| route | 질문이 Financial·filing·integrated 중 어느 데이터 경로로 처리됐는지 나타냅니다. | financial |
| validation_status | 검증 항목의 최종 통과 여부입니다. | PASS |
| question_id | 질문 ID 값입니다. | 1 |
| numeric_mismatch | 답변 숫자와 코드가 제공한 숫자가 불일치하는지 표시합니다. | False |

### 실제 예시 행 1개

```yaml
question: 삼성전자 2025년 매출액과 영업이익은 얼마인가?
route: financial
validation_status: PASS
```

### 이 한 행을 사람 말로 해석하면

질문 또는 테스트 '삼성전자 2025년 매출액과 영업이익은 얼마인가?'의 최종 검증 상태가 PASS라는 뜻입니다.

### 이 파일을 언제 보면 되는가

LLM 답변 또는 최종 서비스 통합검증이 통과했는지 확인할 때 봅니다.

## post_promotion_smoke_test_summary.csv

### 이 파일의 목적

financial.db 승격 후 DB·Vector·CAPEX·Streamlit·Gemini 동작을 최종 확인합니다. 실제 행은 14개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. |  |
| year | 재무정보가 속하는 사업연도입니다. |  |
| report_type | 공시 보고기간 종류입니다. |  |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. |  |
| status | 수집·검증·테스트 처리 결과입니다. | PASS |
| test_name | 수행한 smoke test 항목 이름입니다. | financial_db_integrity |

### 실제 예시 행 1개

```yaml
status: PASS
test_name: financial_db_integrity
```

### 이 한 행을 사람 말로 해석하면

질문 또는 테스트 'financial_db_integrity'의 최종 검증 상태가 PASS라는 뜻입니다.

### 이 파일을 언제 보면 되는가

LLM 답변 또는 최종 서비스 통합검증이 통과했는지 확인할 때 봅니다.

## search_validation_report.csv

### 이 파일의 목적

대표 질의별 Vector DB 검색 상위 결과와 실제 청크 본문을 검토합니다. 실제 행은 20개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| corp_code | DART가 부여한 8자리 기업 식별 코드입니다. | 00126380 |
| year | 재무정보가 속하는 사업연도입니다. | 2025 |
| report_type | 공시 보고기간 종류입니다. | FY |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20260310002820 |
| query | 검색기에 전달한 사용자 질문 또는 검색 문장입니다. | 삼성전자의 반도체 사업 현황 |
| rank | 한 질의 안에서 검색 결과가 배치된 순서입니다. | 1 |
| section_name | 청크가 속한 공시 대분류 제목입니다. | II. 사업의 내용 |

### 실제 예시 행 1개

```yaml
corp_name: 삼성전자
corp_code: 00126380
year: 2025
report_type: FY
rcept_no: 20260310002820
query: 삼성전자의 반도체 사업 현황
rank: 1
section_name: II. 사업의 내용
score: 0.6575456261634827
```

### 이 한 행을 사람 말로 해석하면

'삼성전자의 반도체 사업 현황' 검색에서 삼성전자 공시의 II. 사업의 내용 청크가 1위로 검색됐다는 뜻입니다.

### 이 파일을 언제 보면 되는가

Vector DB 검색 결과와 검색 방식의 품질을 확인할 때 봅니다.

## vector_search_comparison.csv

### 이 파일의 목적

검색 방식별 상위 결과와 관련성 메모를 비교합니다. 실제 행은 50개입니다.

### 주요 컬럼 설명

| 컬럼명 | 의미 | 실제 예시 |
|---|---|---|
| corp_name | 공시 대상 기업의 이름입니다. | 삼성전자 |
| year | 재무정보가 속하는 사업연도입니다. | 2025 |
| report_type | 공시 보고기간 종류입니다. | FY |
| rcept_no | DART 공시 한 건을 식별하는 접수번호입니다. | 20260310002820 |
| query | 검색기에 전달한 사용자 질문 또는 검색 문장입니다. | 매출 감소 원인은 무엇인가? |
| retrieval_method | 청크 검색에 사용한 임베딩·하이브리드 방식입니다. | feature_hashing_hybrid |
| rank | 한 질의 안에서 검색 결과가 배치된 순서입니다. | 1 |
| section_name | 청크가 속한 공시 대분류 제목입니다. | IV. 이사의 경영진단 및 분석의견 |

### 실제 예시 행 1개

```yaml
corp_name: 삼성전자
year: 2025
report_type: FY
rcept_no: 20260310002820
query: 매출 감소 원인은 무엇인가?
retrieval_method: feature_hashing_hybrid
rank: 1
section_name: IV. 이사의 경영진단 및 분석의견
score: 0.6592399477958679
```

### 이 한 행을 사람 말로 해석하면

feature_hashing_hybrid 방식에서 '매출 감소 원인은 무엇인가?' 질의의 1위 결과가 IV. 이사의 경영진단 및 분석의견이고 점수는 0.6592399477958679라는 뜻입니다.

### 이 파일을 언제 보면 되는가

Vector DB 검색 결과와 검색 방식의 품질을 확인할 때 봅니다.

## 빠르게 찾기

| 궁금한 내용 | 보면 되는 파일 |
|---|---|
| 핵심 표준계정 | account_mapping_core.csv |
| 전체 계정 후보 | account_mapping_prioritized.csv |
| 검토 필요한 계정 | account_mapping_review_prioritized.csv |
| 재무 이상값 | financial_quality_report.csv |
| CAPEX 검증 | capex_final_validation.csv |
| 공시 수집·청킹 품질 | filing_quality_report.csv |
| 검색 결과 | search_validation_report.csv |
| 검색 방식 비교 | vector_search_comparison.csv |
| 최종 smoke test | post_promotion_smoke_test_summary.csv |
