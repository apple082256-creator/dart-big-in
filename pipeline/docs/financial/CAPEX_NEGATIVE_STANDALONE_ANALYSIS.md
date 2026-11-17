# CAPEX negative standalone analysis

## 1. 문제 정의

CAPEX 분기 단독값은 누적 공시에서 `Q2 = H1 - Q1`, `Q3 = Q3 - H1`,
`Q4 = FY - Q3`로 계산한다. 기존 구현은 차이가 음수여도 `abs(value)`를
최종 `cash_outflow_amount`로 저장했다. 원본 현금흐름 부호를 양수 지출액으로
정규화하는 것과, 서로 다른 공시의 누적값 차이가 음수인 것은 의미가 다르므로
후자는 자동 절댓값 변환의 근거가 없다.

## 2. 대상과 계산 재현

| 기업 | 범위 | Q1 누적 | H1 누적 | Q2 계산 | 기존 최종값 |
|---|---|---:|---:|---:|---:|
| HLB 2023 | CFS | 6,163,192,619 | 3,576,017,207 | -2,587,175,412 | 2,587,175,412 |
| 에이치브이엠 2025 | OFS | 7,939,767,020 | 7,027,491,464 | -912,275,556 | 912,275,556 |

두 사례 모두 `value_source=direct`, `is_derived=0`이며 다음 동일 태그를
사용한다.

`ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`

계정명은 모두 `유형자산의 취득`, 재무제표 구분은 모두 `CF`, 통화는 모두
KRW다. Q1과 H1의 `source_account_ids`와 `source_account_names` 집합도 같다.

## 3. 원본 대조

### HLB 2023

- Q1: 접수번호 `20230515002471`, CFS, 2023-01-01~2023-03-31
- H1: 접수번호 `20230811002523`, CFS, 2023-01-01~2023-06-30
- Q1 XML: `유형자산의 취득 (6,163,192,619)`
- H1 XML: `유형자산의 취득 (3,576,017,207)`

### 에이치브이엠 2025

- Q1: 접수번호 `20250515000123`, OFS, XBRL context `CFY2025dFQA...SeparateMember`
- H1: 접수번호 `20250814002726`, OFS, XBRL context `CFY2025dHYA...SeparateMember`
- Q1 XML: `유형자산의 취득 (7,939,767,020)`
- H1 XML: `유형자산의 취득 (7,027,491,464)`

DB 값은 원본 XML과 일치한다. 수집된 슬롯에는 각각 하나의 접수번호만 있고,
단위·범위·태그·계정 세트의 불일치도 없다.

## 4. 원인 판정

두 건 모두 `G. VALID_NEGATIVE_ADJUSTMENT`로 분류한다. 이는 음수 CAPEX가
유효하다는 뜻이 아니라, 후속 공시에 표시된 누적 계정액이 앞선 공시보다
감소한 사실 자체가 원본과 일치한다는 의미다. 공시만으로 감소가 환입,
재분류, 정산 또는 앞선 분기 수치 수정 중 무엇인지는 확정할 수 없다.

- 데이터 오류: 아님
- CFS/OFS 혼용: 없음
- account ID/명칭 불일치: 없음
- 구성 계정 변경: 없음
- 기간 또는 단위 불일치: 없음
- 수집된 정정 접수번호 선택 충돌: 없음
- Q2 standalone CAPEX 확정 가능 여부: 불가능
- `review_required`: 유지

## 5. 수정 정책

1. 원본 누적 공시값의 부호가 음수이면 지출 표시 관행으로 보고 원본 부호를
   보존하면서 `cash_outflow_amount=abs(raw value)`로 정규화한다.
2. 누적값 차이가 0 이상이면 그 차이를 standalone CAPEX로 사용한다.
3. 누적값 차이가 음수이면:
   - `calculated_value`에 음수 원값을 보존한다.
   - `cash_outflow_amount=NULL`로 둔다.
   - `review_required=1`로 유지한다.
   - 숫자형 CAPEX KPI와 표준 재무 뷰에서는 해당 행을 제외한다.

이를 위해 SQLite 및 MySQL의 `cash_outflow_amount`를 nullable로 정의하고,
MySQL 적용용 마이그레이션
`infra/mysql/migrations/007_capex_negative_standalone_review.sql`을 추가했다.

## 6. 전체 영향 범위와 회귀 결과

운영 SQLite를 변경하지 않고 복사본에서 전체 CAPEX를 재생성했다.

| 항목 | 기존 | 수정 후 |
|---|---:|---:|
| 전체 CAPEX 행 | 469 | 469 |
| direct 행 | 445 | 445 |
| derived 행 | 24 | 24 |
| review_required | 2 | 2 |
| 음수 중간 계산값 | 2 | 2 |
| 음수 최종 CAPEX | 0 | 0 |
| NULL 최종 CAPEX | 0 | 2 |

변경 행은 HLB 2023 Q2와 에이치브이엠 2025 Q2 두 건뿐이다. 다른 승인
CAPEX, direct/derived 구분, 원본 `financial_facts`는 변경되지 않았다.

상세 검증 데이터는
`data/processed/quality/capex_negative_standalone_review.csv`에 있다.

## 7. 배포 상태 (2026-10-11 KST)

운영 SQLite와 중앙 MySQL에 반영했다.

- 운영 SQLite SHA-256:
  `ea656cfd6c63534f487090184e1539f502b9d0476f00e3f384f4025472aa0bdd`
- 중앙 current: `2026-10-10-healthcare-rnd-v12`
- 중앙 current index: `bge-m3-2026-10-09-dart25-h1h3-v11`
- 중앙 `cash_outflow_amount`: nullable
- 로컬/중앙 CAPEX 469행, 31개 비교 필드: 전체 일치
- 문제 두 행은 표준 재무 숫자 뷰에서 제외됨

백업:

- SQLite:
  `db/archive/financial_before_capex_negative_policy_20261011.db`
  (`8979cc618f91a30cdfd937b4417ebf45f2d0c533b05cc524cdd38976df035ea1`)
- MySQL:
  `db/archive/mysql_dart_big_in_before_capex_negative_policy_20261011.sql`
  (`66eb070d5a4fda1a4464922cd9f30fbaa7debc5140ea6073af2e6e0261401571`)
