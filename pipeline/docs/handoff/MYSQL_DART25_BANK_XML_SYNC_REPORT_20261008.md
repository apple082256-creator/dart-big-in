# DART 25개사·은행 금융지표·원본 XML 동기화 결과

- 완료일: 2026-10-08
- 운영 데이터 버전: `2026-10-08-dart25-bank-xml-v1`
- 임베딩 인덱스 버전: `bge-m3-2026-10-08-dart25-bank-xml-v1`
- 결과: 로컬 SQLite/FAISS와 중앙 MySQL 검증 통과 후 운영본 승격 완료

## 추가 기업

| 기업 | 종목코드 | DART corp_code | 유형 | 재무 행 | 공시 | BGE 청크/임베딩 | 원본 XML |
|---|---:|---:|---|---:|---:|---:|---:|
| 하나금융지주 | 086790 | 00547583 | BANK_HOLDING | 3,023 | 12 | 12,900 | 14 |
| KB금융 | 105560 | 00688996 | BANK_HOLDING | 2,607 | 12 | 13,261 | 12 |
| 신한지주 | 055550 | 00382199 | BANK_HOLDING | 2,843 | 12 | 12,031 | 14 |
| 우리금융지주 | 316140 | 01350869 | BANK_HOLDING | 2,525 | 12 | 9,464 | 16 |
| 카카오뱅크 | 323410 | 01133217 | INTERNET_BANK | 1,610 | 12 | 2,967 | 14 |
| 합계 |  |  |  | 12,608 | 60 | 50,623 | 70 |

수집 기간은 2023~2025년의 Q1/H1/Q3/FY이다. 금융지주 4개사는 연결재무제표(CFS), 카카오뱅크는 별도재무제표(OFS)를 우선 범위로 저장했다.

## 최종 운영 수치

| 항목 | 수치 |
|---|---:|
| 기업 | 25 |
| 재무 원천 행 | 56,665 |
| CAPEX 행 | 469 |
| 공시 | 295 |
| BGE-M3 청크 | 147,720 |
| BGE-M3 임베딩 | 147,720 |
| FAISS 벡터 | 147,720 |
| 원본 XML 문서 | 399 |
| chunk → XML 매핑 | 147,720 |

신규 `vector_id`는 기존 최댓값 다음인 97,097부터 시작해 147,719까지 연속으로 추가했다. 기존 0~97,096의 메타데이터와 FAISS 벡터는 바이트 단위로 동일하다.

## 금융업 전용 계층

기존 제조업 재무 구조를 보존하면서 다음 테이블을 추가했다.

- `company_financial_profiles`: 기업 유형과 재무 범위
- `financial_collection_status`: 기업·기간별 수집 상태와 누락 사유
- `financial_metric_definitions`: 금융 표준지표 정의
- `financial_metric_mappings`: DART 계정과 금융 표준지표 매핑
- `financial_metric_values`: 직접값 및 파생값과 출처

최종 수치는 프로필 5행, 수집 상태 60행, 지표 정의 28행, 매핑 42행, 금융 지표값 1,250행이다. 지표값은 직접값 1,214행과 파생값 36행으로 구성된다. NIM, BIS/CET1 등 정형 재무제표만으로 신뢰성 있게 산출할 수 없는 비율은 값을 임의 생성하지 않고 공시 원문 검색으로 fallback한다.

2023년 Q1/H1은 다섯 회사 모두 DART 정형 재무 API에서 데이터가 없어 총 10개 슬롯을 `DART_FNLTT_API_NO_DATA`로 기록했다. 해당 기간 공시 원문은 모두 적재되어 검색할 수 있다.

KB금융 2025 FY는 최신 정정 접수번호 `20260619000667`의 정형 재무 데이터는 존재하지만 해당 DART 문서 다운로드가 불가능해, 원본 XML은 다운로드 가능한 직전 정정 접수번호 `20260324000835`를 사용했다. 이 차이는 coverage에 검토 대상으로 명시했다.

## KRX 그룹

`KRX_BANK`는 5개사를 연결한 내부 비교 그룹이다. 기준일은 2026-10-07이다. 공식 KRX 지수 코드와 공식 비중을 검증하지 않았으므로 `official_index_code`와 모든 `weight`는 `NULL`로 저장했다.

최종 그룹은 `KRX_SEMI`, `KRX_HEALTH`, `KRX_STEEL`, `KRX_AUTO`, `KRX_BANK`이며 각각 5개 기업이다.

## 원본 해시

| 산출물 | SHA-256 |
|---|---|
| `financial.db` | `295d15342772d8c65bc4f3acb85361571b992c78adae2ab1c53d2bcc01530b05` |
| `filings.db` | `81bd94f79f616a622fd02c0f49b9d9e85b0e74c3783ce4832eb1ef24051a13a1` |
| `filings_bge_m3.db` | `a1b6f68e2ced38ecf193aaec625016efafb399adbfa5af4aa1024ac1cd98c9ce` |
| `filings_bge_m3.faiss` | `d8d17f64ce2f7f8a46e9af8a694ba63cc2bac0605499c31bbc3ba975c88b0292` |

승격 전 로컬 백업은 `backups/local/20261007_pre_dart25_bank/`, MySQL 덤프는 `backups/mysql/20261007_pre_dart25_bank_xml/dart_big_in_pre_dart25_bank_xml.sql`에 보존되어 있다.

## 검증 결과

- MySQL 전체 검증: PASS (`25개사·XML·청크·임베딩·KRX 로컬 원천 일치`)
- SQLite/FAISS 전체 단위 테스트: 57개 PASS
- Streamlit AppTest: 예외 0, 기업 선택지 25개
- 금융 지표 조회: 하나금융지주 CFS 직접값, 카카오뱅크 OFS 파생값 확인
- BGE-M3 실검색: 하나금융지주 2025 FY 결과 정상 반환
- chunk ID 중복: 0
- vector ID 중복: 0
- 임베딩 고아행: 0
- XML 매핑 고아행: 0
- 기존 20개사 재무·공시·청크 내용 변경: 0

기존 데이터의 내용과 식별자는 유지했으며, 운영 승격에 따라 버전·활성 상태 메타데이터만 최신 버전으로 갱신했다.
