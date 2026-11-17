# 헬스케어 연구개발비 KPI 운영 반영 보고서

- 반영 시각: 2026-10-10 16:29 KST
- 운영 DB: `db/financial/financial.db`
- 백업 DB: `db/archive/financial_before_healthcare_rnd_20261010.db`
- 반영 방식: 최신 운영 DB를 SQLite backup API로 백업·스테이징한 뒤 같은
  파일시스템에서 검증된 스테이징 DB를 원자적으로 교체

## 해시와 무결성

| 구분 | SHA-256 | SQLite integrity |
|---|---|---|
| 반영 전 운영 DB | `6ea2a85ffbb7556c80154fa7a7bcb5bdca1dae17eb36f8423e4a4248dbcc6faf` | ok |
| 보존 백업 DB | `8d5e947ddff45723d27474ce5f7f6e7c78cc782cc240ad93fcba3c92839c21ac` | ok |
| 반영 후 운영 DB | `ff9a5bf5a0bf68b623fc3ee34c0f652d4e98e8a471c2101c54916083f6bb735c` | ok |

SQLite backup은 논리적으로 동일한 DB를 새 파일로 기록하므로 원본과 백업의
파일 해시는 다를 수 있다. 양쪽 모두 무결성 검사에 통과했으며 백업의 KPI
행 수는 반영 전과 같은 1,557행이다.

## 반영 결과

| 항목 | 반영 전 | 반영 후 | 증감 |
|---|---:|---:|---:|
| `financial_metric_values` | 1,557 | 1,677 | +120 |
| `financial_metric_definitions` | 29 | 42 | +13 |
| `company_financial_profiles` | 6 | 11 | +5 |
| `financial_facts` | 63,931 | 63,931 | 0 |
| `capex_facts` | 469 | 469 | 0 |

정의 13건 증가는 일반산업 KPI 정의 11건과 헬스케어 R&D 정의 2건을 함께
초기화한 결과다. 프로필 5건은 셀트리온, 삼성바이오로직스, 알테오젠, HLB,
유한양행의 `HEALTHCARE` 프로필이다.

## R&D KPI 검증

- `RND_EXPENSE`: 60/60, 승인 60
- `RND_TO_REVENUE`: 60/60, 승인 60
- `review_required`: 0
- 누락: 0
- 자연키 중복: 0
- 2차 실행 전후 행 수: 1,677 → 1,677
- 대상: 5개사 × 2023~2025년 × Q1/H1/Q3/FY

## 테스트와 회귀

- Python `unittest`: 8개 통과
- Python 컴파일 검사: 통과
- `git diff --check`: 통과
- `financial_facts`: 반영 전후 63,931행으로 동일
- `capex_facts`: 반영 전후 469행으로 동일
- 금융회사 KPI 전체 표본: 반영 전후 동일
- 삼성전자·현대자동차·셀트리온 2025 FY 재무 표본: 반영 전후 동일

운영 조회에서 헬스케어 5개사 비교는 두 KPI 10행을 반환하며 모든 행이
`CFS`, `consolidated`, `approved`, `DOCUMENT_EXTRACTED` 출처를 가진다.
셀트리온 경상연구개발비 질문도 폴백 없이 `RND_EXPENSE`와 접수번호
`20260316001415`를 반환한다.

## 롤백

서비스 및 DB 쓰기를 중지한 뒤 현재 운영 DB를 별도 보존하고, 백업 DB의
무결성을 다시 확인한 다음 아래 백업을 `db/financial/financial.db`로 같은
파일시스템에서 원자 교체한다.

`db/archive/financial_before_healthcare_rnd_20261010.db`

롤백 후 기대 상태는 `financial_metric_values=1557`, R&D KPI 0행이다.
