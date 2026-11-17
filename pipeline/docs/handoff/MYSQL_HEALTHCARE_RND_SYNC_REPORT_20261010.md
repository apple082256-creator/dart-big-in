# 중앙 MySQL 헬스케어 R&D KPI 동기화 보고서

> 후속 조치: 문서 ID 표준화와 재무/임베딩 버전 축 분리는
> `HEALTHCARE_RND_DOCUMENT_LINK_FIX_20261010.md`를 따른다. 현재 버전은
> `2026-10-10-healthcare-rnd-docid-v13`이다.

- 동기화 완료: 2026-10-10 17:41 KST
- 중앙 서버: `KHP-SD1-03:3306`
- 데이터베이스: `dart_big_in`
- 이전 current: `2026-10-09-dart25-h1h3-v11`
- 신규 current: `2026-10-10-healthcare-rnd-v12`
- 로컬 SQLite SHA-256: `ff9a5bf5a0bf68b623fc3ee34c0f652d4e98e8a471c2101c54916083f6bb735c`

## 백업

- 파일: `db/archive/mysql_dart_big_in_before_healthcare_rnd_20261010.sql`
- 크기: 964,458 bytes
- SHA-256: `fe68016fc9c6ce4d9dc95b4a1e7f4dce1ff1dbe444da3db3503ac92c44a6fa2a`
- 포함 테이블: `data_versions`, `company_financial_profiles`,
  `financial_metric_definitions`, `financial_metric_mappings`,
  `financial_metric_values`, `sync_runs`

덤프 생성 후 여섯 테이블의 스키마·데이터 구문 포함 여부를 확인했다.

## 스키마 호환성

문서 추출 KPI는 `source_document_id`에 최대 71자의 XML 경로를 저장한다.
기존 중앙 컬럼 `CHAR(64)`는 손실 위험이 있어 다음 멱등 마이그레이션으로
`VARCHAR(255)`로 확장했다.

`infra/mysql/migrations/006_expand_metric_source_document_id.sql`

초기 설치 스키마와 handoff 스키마도 동일하게 갱신했다. 다른 enum 및 문자열
컬럼은 신규 값의 최대 길이를 수용한다.

## Dry run 및 적재

Dry run에서 축소 차단 없이 다음 원천/중앙 활성 행 수를 확인했다.

| 테이블 | 원천 | 반영 전 중앙 | 반영 후 중앙 |
|---|---:|---:|---:|
| company_financial_profiles | 11 | 6 | 11 |
| financial_metric_definitions | 42 | 29 | 42 |
| financial_metric_mappings | 94 | 42 | 94 |
| financial_metric_values | 1,677 | 1,557 | 1,677 |
| financial_facts(active) | 63,931 | 63,931 | 63,931 |
| capex_facts(active) | 469 | 469 | 469 |
| chunk_metadata(active) | 147,720 | 147,720 | 147,720 |

`--defer-promotion`으로 17개 테이블을 적재했고 실패한 `sync_runs`는 0건이다.
기본 대조 검증 PASS 후 별도 전체 검증을 수행했다.

## KPI 검증

- `RND_EXPENSE`: 60/60, approved 60
- `RND_TO_REVENUE`: 60/60, approved 60
- `review_required`: 0
- 누락: 0
- 자연키 중복: 0
- HEALTHCARE 프로필: 5개
- 각 기업: 지표별 12/12 기간

로컬 SQLite와 중앙 MySQL을 20개 주요 컬럼으로 비교한 결과:

- R&D 120행: 전 컬럼 일치
- 기존 비R&D 1,557행: 전 컬럼 일치
- 출처 검증 120/120: `DOCUMENT_EXTRACTED`, 접수번호, 문서 경로,
  청크 ID, 계산식, 유효 JSON, 단위, 범위, 원문 위치 존재

## 표본

| 기업/기간 | RND_EXPENSE | RND_TO_REVENUE | 범위 | 접수번호 |
|---|---:|---:|---|---|
| 유한양행 2024 Q3 | 201,130,000,000 KRW | 1,280 BPS | CFS/consolidated | 20241114000859 |
| 유한양행 2025 Q3 | 159,551,000,000 KRW | 970 BPS | CFS/consolidated | 20251114001916 |
| 셀트리온 2025 FY | 482,424,987,000 KRW | 1,159 BPS | CFS/consolidated | 20260316001415 |
| 삼성바이오로직스 2025 FY | 167,400,000,000 KRW | 370 BPS | CFS/consolidated | 20260515001658 |
| 알테오젠 2025 FY | 60,763,471,000 KRW | 2,815 BPS | CFS/consolidated | 20260323001596 |
| HLB 2025 FY | 59,945,714,000 KRW | 7,122 BPS | CFS/consolidated | 20260323001673 |

모든 표본은 `approved`다.

## 승격과 서비스 조회

조건부 트랜잭션으로 R&D 120행, 전체 지표 1,677행, 프로필 5개 및 실패
sync run 0건을 재확인한 후 v12를 `validated=1`, `is_current=1`로 승격했다.
v11은 `validated=1`, `is_current=0`이다. current 버전은 정확히 하나다.

이번 변경과 무관한 embedding current는
`bge-m3-2026-10-09-dart25-h1h3-v11`로 유지했다.

중앙 SQL 기준으로 2025 FY 5개사의 연구개발비와 비율, 범위, 접수번호,
검토 상태 및 공시 구성요소가 반환된다. 셀트리온 경상연구개발비 질문은
`RND_EXPENSE=482,424,987,000 KRW`, `RND_TO_REVENUE=1,159 BPS`를 반환한다.

## 테스트

- Python unittest: 8개 통과
- Python 컴파일 검사: 통과
- `git diff --check`: 통과
- 동기화 도구 기본 합계·체크섬 검증: PASS
- 로컬/중앙 전체 KPI 컬럼 대조: PASS

남은 예외는 없다.

## 롤백

1. 중앙 쓰기 작업을 중지한다.
2. 위 SQL 덤프의 SHA-256을 다시 확인한다.
3. `mysql` 클라이언트로 덤프를 `dart_big_in`에 복원한다. 덤프는 대상
   여섯 테이블의 반영 전 스키마와 데이터를 포함한다.
4. `financial_metric_values=1,557`, R&D KPI 0행, HEALTHCARE 프로필 0개,
   v11 current를 확인한다.
5. 필요하면 `source_document_id` 컬럼만 기존 `CHAR(64)`로 되돌린다.

벡터 DB, FAISS 및 원본 XML은 이번 작업에서 수정하지 않았다.
