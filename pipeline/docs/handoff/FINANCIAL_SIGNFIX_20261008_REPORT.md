# 금융업 비용 부호 보정 결과 (signfix-v2)

- 완료일: 2026-10-08
- 운영 데이터 버전: `2026-10-08-dart25-bank-signfix-v2` (이전: `2026-10-08-dart25-bank-xml-v1`)
- 임베딩 인덱스 버전: `bge-m3-2026-10-08-dart25-bank-signfix-v2` (FAISS·벡터 SQLite 파일은 이전과 동일)
- 결과: staging 검증 → 로컬 교체 → MySQL 적재·전체 검증 → current 승격 완료

## 원인

일부 발행사는 같은 비용 계정을 보고서에 따라 양수(금액) 또는 음수(차감 표시)로 공시한다.
부호 표기가 다른 두 값을 그대로 빼면서 두 곳에서 오류가 생겼다.

1. `materialize_financial_metric_values()`의 `수익 - 비용` 파생값: 비용이 음수일 때 더해짐
2. `period_normalizer`의 분기 환산(`FY - Q3 누적` 등): FY와 Q3 보고서의 표기가 다를 때 4분기 과대·부호 반전

오류 판정은 다음 해 보고서의 전기 비교 컬럼(같은 기간 값을 하나의 표기 규칙으로 재기재)으로 했다.
판정 근거 접수번호는 `src/db/sign_convention_corrections.py`에 행별로 기록돼 있다.

## 보정 행 (금액 단위: 원)

### `financial_metric_values`

| id | 기업 | 기간 | 지표 | 구분 | 수정 전 | 수정 후 |
|---:|---|---|---|---|---:|---:|
| 1215 | 카카오뱅크 | 2023 FY 연간 | 순이자이익 | DERIVED | 2,980,370,000,000 | 1,115,970,000,000 |
| 1233 | 카카오뱅크 | 2023 FY 연간 | 순수수료이익 | DERIVED | 537,624,000,000 | -2,030,000,000 |
| 47 | 신한지주 | 2024 FY 4분기 | 수수료비용 | DIRECT(계산 원천) | 2,738,396,000,000 | 422,588,000,000 |
| 253 | 하나금융지주 | 2023 FY 4분기 | 신용손실비용 | DIRECT(계산 원천) | 2,778,708,000,000 | 322,776,000,000 |
| 256 | 하나금융지주 | 2023 FY 4분기 | 수수료비용 | DIRECT(계산 원천) | 1,823,736,000,000 | 290,638,000,000 |

검토 표시만 한 행(값 변경 없음):

| id | 기업 | 기간 | 지표 | 값 | 상태 |
|---:|---|---|---|---:|---|
| 964 | 우리금융지주 | 2023 FY 4분기 | 신용손실비용 | -2,973,496,000,000 | approved → review_required |

FY 연간 -1,894,916백만원과 Q3 누적 +1,078,580백만원의 부호가 반대다. 2024 보고서의 비교 컬럼도 같은 불일치를 반복해 외부 근거로 확정할 수 없어 값은 그대로 두었다.

### `financial_facts` (분기 계산값 `calculated_value`)

| fact_id | 기업 | 기간 | 계정 | 수정 전 | 수정 후 |
|---:|---|---|---|---:|---:|
| 54987 | 하나금융지주 | 2023 Q4 | 기타비용 | 692,849,000,000 | 132,173,000,000 |
| 54990 | 하나금융지주 | 2023 Q4 | 보험서비스비용 | 923,659,000,000 | 118,357,000,000 |
| 54993 | 하나금융지주 | 2023 Q4 | 기타영업비용 | 4,116,301,000,000 | 836,515,000,000 |
| 54995 | 하나금융지주 | 2023 Q4 | 신용손실충당금전입액 | 2,778,708,000,000 | 322,776,000,000 |
| 55001 | 하나금융지주 | 2023 Q4 | 수수료비용 | 1,823,736,000,000 | 290,638,000,000 |
| 55004 | 하나금융지주 | 2023 Q4 | 법인세비용 | 2,239,213,000,000 | 186,499,000,000 |
| 55013 | 하나금융지주 | 2023 Q4 | 보험 및 재보험계약 이자비용 | 556,879,000,000 | 104,083,000,000 |
| 54731 | 신한지주 | 2024 Q4 | 수수료비용 | 2,738,396,000,000 | 422,588,000,000 |
| 56070 | 카카오뱅크 | 2023 Q4 | 당기손익-공정가치측정금융자산관련손실 | -6,028,000,000 | -26,000,000 |
| 9789 | 한미반도체 | 2023 Q4 | 법인세효과 | -28,862,424 | -4,225,776 |

그 외 모든 테이블·행·스키마는 바이트 단위로 동일하다(행 수 변화 0, INSERT·DELETE 0).
CAPEX, 수집 상태, 공시, 청크, 임베딩, FAISS, 원본 XML은 변경하지 않았다.

## 재발 방지 규칙

- 파생 순이익 지표: `수익 - ABS(비용)`. 비용 입력이 음수인 경우 검증 목록에 있는 슬롯만 `approved`, 나머지는 `review_required`.
- 분기 환산: 부호가 엇갈린 누적값은 검증 목록과 입력값까지 정확히 일치할 때만 부호를 맞춘다(`sign_aligned`).
  나머지는 값을 바꾸지 않고 `sign_conflict_review`로 표시한다. 세금 환급, 충당금 환입, 손익 반전은 외부 근거 없이 구분할 수 없기 때문이다.
- 금융 지표 DIRECT 행: 원천 계산값의 입력 부호가 엇갈리고 정렬 근거가 없으면 `review_required`.
- 분석기 실행 중 4분기 계산: FY와 Q3 누적의 부호가 반대면 계산하지 않는다.

예외 검증(운영 데이터 전체의 부호 엇갈림 275행):

| 분류 | 행 | 처리 |
|---|---:|---|
| 외부 근거로 확정된 표기 오류 | 10 | 정렬·보정 |
| 비교 컬럼상 실제 부호 변화 | 97 | 값 유지, 검토 표시 |
| 근거 없음 | 168 | 값 유지, 검토 표시 |

`financial_facts`에는 검토 컬럼이 없으므로 현재 저장된 265행에는 표시가 없다. 재생성할 때 `calculation_method`/`source_values`에 표시된다.
금융 지표값 중 영향받는 1행(#964)은 이번에 `review_required`로 반영했다.

## 검증

- staging과 운영 교체 후 SQLite `integrity_check` ok, FK 오류 0
- 수정된 materialize 로직으로 재계산 시 1,250행의 값·검토상태 동일, 보정·표시 행은 전 컬럼 동일
- 단위 테스트 64개 통과(신규 `tests/test_sign_convention.py` 7개 포함)
- Streamlit AppTest 예외 0, 기업 선택지 25
- MySQL `validate_dart15_xml_mysql.py`: 25개사 원천 CRC, 금융 계층 CRC, 임베딩 BLOB/vector_id, XML 399개 SHA-256, chunk→XML 147,720 연결, 중복·고아 0, KRX 5×5 → PASS 후 승격

## 해시

| 산출물 | SHA-256 |
|---|---|
| `financial.db` (보정 후) | `5fe54f67bc4eeefc29926a7a3040f18d7cc1e98fd6a1f078c6de4787b6088d26` |
| `financial.db` (보정 전) | `295d15342772d8c65bc4f3acb85361571b992c78adae2ab1c53d2bcc01530b05` |
| `filings.db` | `81bd94f79f616a622fd02c0f49b9d9e85b0e74c3783ce4832eb1ef24051a13a1` (불변) |
| `filings_bge_m3.db` | `a1b6f68e2ced38ecf193aaec625016efafb399adbfa5af4aa1024ac1cd98c9ce` (불변) |
| `filings_bge_m3.faiss` | `d8d17f64ce2f7f8a46e9af8a694ba63cc2bac0605499c31bbc3ba975c88b0292` (불변) |
| MySQL 덤프 | `042f75159112aa0551e42acdd704db666a1e45d791aa9acb9652f9e9da7d2382` |

## 백업과 복구

- 로컬: `backups/local/20261008_pre_signfix/financial.db` (+ `SHA256SUMS`, 코드 변경 패치 `signfix_code_changes.patch`)
- MySQL: `backups/mysql/20261008_pre_signfix/dart_big_in_pre_signfix.sql` (`--single-transaction --hex-blob`, 테이블 23개), 적재 전 상태 `pre_sync_state.txt`, 적재 로그 `sync_signfix_v2.log`

MySQL은 자연키 upsert라 이전 값이 별도 버전으로 남지 않는다. `data_versions`의 current 표시만 되돌려서는 값이 복구되지 않는다.

1. 로컬 복구: 백업 `financial.db`를 `db/financial/financial.db`로 복사하고 SHA-256 `295d1534…`를 확인한다.
2. MySQL 복구는 둘 중 하나로 한다.
   - 덤프 복원: `mysql.exe dart_big_in < dart_big_in_pre_signfix.sql`. 복원 후 `validate_dart15_xml_mysql.py --data-version 2026-10-08-dart25-bank-xml-v1`로 검증한다.
   - 재적재: 복구된 로컬 파일로 새 버전명을 적재한 뒤 검증하고 `--promote`한다.
3. 코드 복구: `git apply -R backups/local/20261008_pre_signfix/signfix_code_changes.patch`. 이 패치에는 이번 수정만 들어 있다.

## 코드 변경 범위

이번 수정은 아래에 한정된다. 기존 미커밋 변경과 분리된 패치는 백업 폴더에 있다. 커밋은 하지 않았다.

- `src/db/financial_metrics.py`: 파생식, 검토 상태 규칙
- `src/preprocess/period_normalizer.py`: 검증 목록 기반 부호 정렬과 충돌 표시
- `src/llm/analyzer.py`: 실행 중 4분기 계산 가드 1곳(6줄)
- 신규 `src/db/sign_convention_corrections.py`: 보정 목록·멱등 적용 스크립트, 운영 경로 직접 실행 거부
- 신규 `tests/test_sign_convention.py`

## 후속 작업

- 2023 Q1/H1 XML 보완은 이 버전(`2026-10-08-dart25-bank-signfix-v2`, `financial.db` `5fe54f67…`)을 기준으로 별도 staging에서 진행한다.
- 다른 보고서의 표기 차이로 분기 추이에 부호가 섞여 보인다(예: 하나 2023 Q3 신용손실 -425,538 / Q4 +322,776 백만원). 화면 표시 부호 통일은 별도 작업이다.
- 우리금융 2023 Q4 신용손실(#964) 등 검토 대상은 원문 XML로 확인할 수 있다.
