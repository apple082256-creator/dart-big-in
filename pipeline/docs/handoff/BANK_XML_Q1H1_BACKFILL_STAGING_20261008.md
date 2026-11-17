# 금융 5개사 2023 Q1/H1 원본 XML 보완 (staging)

- 작성일: 2026-10-08
- 기준 운영 버전: `2026-10-08-dart25-bank-signfix-v2` (`financial.db` `5fe54f67…6088d26`)
- staging: `db/staging/20261008_bank_xml_q1h1/financial.db` (`24028150725ae9130b275e5978d289e73c4ff9624851c33fae515945e848c7f1`)
- 상태: **운영 반영 완료** — 운영 데이터 버전 `2026-10-08-dart25-bank-xml-q1h1-v3`, 임베딩 인덱스 `bge-m3-2026-10-08-dart25-bank-xml-q1h1-v3`(FAISS 파일 불변)

## 대상

`financial_collection_status.missing_reason = DART_FNLTT_API_NO_DATA`인 10개 슬롯이 대상이다.
OpenDART 정형 재무 API가 CFS·OFS 모두 `013`을 반환했고, 대상은 하나·KB·신한·우리(CFS)·카카오뱅크(OFS)의 2023 Q1/H1이다.
수집 상태 기록은 사실이므로 변경하지 않았다.

## 방법

- 원본: 이미 저장된 정기보고서 XML(슬롯당 1개, MySQL `filing_source_documents`와 동일 문서). 새 다운로드나 임베딩은 없다.
- XML은 XBRL 인스턴스가 아닌 DART 문서 컨테이너이고 엄격한 XML로는 파싱되지 않는다. 그래서 관대한 HTML 파서로 섹션 코드
  `D-0-3-2-0`(연결)과 `D-0-3-4-0`(별도)의 재무상태표·포괄손익계산서 당기 컬럼만 읽는다.
- 단위와 기간 종료일은 표 머리말에서 확인한다. 확인되지 않으면 값을 만들지 않는다.
- 저장 위치는 `financial_metric_values`의 `source_kind = DOCUMENT_EXTRACTED`이다. `financial_facts`(API 계층)에는 쓰지 않는다.
- 출처 컬럼: `source_type = dart_document_xml`, `source_rcept_no`, `source_document_id`(MySQL 문서 ID와 동일), `source_chunk_id`(같은 금액이 있는 재무제표 청크, 참고용)
- `source_values` JSON 내용: XML 경로, 섹션, 표 라인, 행, 컬럼명, 원문 표기, 단위·배수, 부호 처리, 검증 결과, 검토 사유
- 코드: `src/extract/bank_statement_xml.py`(추출), `src/bank_xml_backfill_pipeline.py`(검증·적재), `src/db/financial_metrics.py`(문서 값 보존·파생)

## 검증 규칙과 상태

| 검증 | 대상 |
|---|---|
| 자산 = 부채 + 자본 | 총계 3종 |
| 순이자이익 = 이자수익 − \|이자비용\|, 순수수료이익 동일 (문서 내 항등식) | 금융지주 |
| 2024 동일 보고서 API 전기 컬럼 절댓값 일치 | 손익 항목 |
| 2023 Q3 API의 (누적 − 3개월) = H1 누적 | H1 손익 항목 |

`approved`는 검증을 하나 이상 통과한 값이다. 아래는 `review_required`로 저장했다.

| 사유 | 행 |
|---|---:|
| 대출채권·예수부채: 외부 대조가 없는 단일 출처 | 20 |
| 우리금융 대출채권: `대출채권및기타금융자산` 결합 계정 (위 20행에 포함) | (4) |
| KB Q1 순이익: 2024 보고서가 1,510,291백만원으로 재작성해 외부 대조 불일치, 다른 검증 없음 | 2 |
| KB 2023 Q2 순이익(파생): 위 Q1 값을 입력으로 사용 | 1 |

부호 처리: 문서는 비용을 `(괄호)`로 표시하는 경우가 있다. 비용 3종(이자비용·수수료비용·신용손실비용)이 같은 회사의 2023 Q3 API 누적값과 부호가 다르면,
검증을 통과한 경우에만 API 계열의 부호로 맞추고 원래 부호를 기록했다. 대상은 KB와 우리금융의 Q1·H1 비용 항목이다.
수익·순이익 항목은 부호를 바꾸지 않는다.

KB의 2024 보고서 재작성(이자비용·순이자이익·순이익)과 달리, XML 값은 기존 2023 Q3 API 계열과 정확히 일치한다(최초 보고 기준).

## 변경 내역 (staging, `financial_metric_values`만)

| 구분 | 행 |
|---|---:|
| DOCUMENT_EXTRACTED (Q1: 재무상태표 + 손익 3개월·누적, H1: 재무상태표 + 손익 누적) | 164 |
| DERIVED 2023 Q2 3개월 = H1 누적 − Q1 누적 | 38 |
| DERIVED 카카오뱅크 순이자이익·순수수료이익 (Q1 3개월·누적, H1 누적, Q2) | 8 |
| **합계 (1,250 → 1,460)** | **210** |

- 기존 1,250행과 다른 모든 테이블·스키마는 바이트 단위로 동일하다. UPDATE·DELETE는 0건이다.
- 행 ID 1453·1458은 비어 있다(upsert의 자동 증가 소비). MySQL은 자연키를 쓰므로 영향이 없다.
- `financial_facts`, CAPEX, 수집 상태, 공시, 청크, 임베딩, FAISS, 원본 XML은 변경하지 않았다.

## 검증 결과

- 126개 추출값: 프로토타입과 일치, 10개 슬롯 모두 단위(백만원)·기간 확인, 추출 이슈 0
- 같은 staging에 재실행: 내용 해시 동일(멱등)
- 전체 `materialize` 재계산: 1,460행 키·값·검토상태 동일, 문서 추출 164행 보존
  (기존 카카오뱅크 DERIVED 34행의 계산식 표기만 신형식으로 바뀌며 값은 동일)
- SQLite integrity ok, FK 오류 0, 자연키 중복 0, 같은 슬롯에 DIRECT와 문서 값 공존 0
- 원본 문서 10개 모두 MySQL `filing_source_documents`에 존재하고 접수번호 일치. 근거 청크 46개 모두 BGE DB에 존재하며 MySQL 매핑 문서와 일치
- 분석기(staging 경로): 2023 Q1~Q4 순이자이익 연속 표시, 출처(`document_extracted`/`derived`)와 검토 상태 함께 제공
- 단위 테스트 69개 통과(신규 `tests/test_bank_xml_backfill.py` 5개)
- 운영 파일 해시 불변

## 운영 반영 절차 (2026-10-08 수행)

1. 로컬 백업: `backups/local/20261008_pre_bank_xml_q1h1/`에 운영 `financial.db`(`5fe54f67…`)와 SHA-256
2. staging 파일을 원자적으로 교체한 뒤 integrity, 테스트, AppTest
3. MySQL 전체 덤프: `backups/mysql/20261008_pre_bank_xml_q1h1/`
4. 신규 버전 `2026-10-08-dart25-bank-xml-q1h1-v3` 적재(`--defer-promotion`) → 전체 검증 → `--promote`
   - MySQL 변경: `financial_metric_values` +210행, 버전 메타데이터. 임베딩·XML 재적재는 불필요
5. 실패 시 승격하지 않고 로컬 백업과 덤프로 복구

## 남은 사항

- 화면에서 `value_source`가 기존 "CAPEX 산출" 컬럼에 표시된다. 문서 추출값임을 명확히 보이려면 UI 라벨 보정이 필요하다.
- 카카오뱅크 2023 Q4 순이자이익은 기존 운영 데이터에도 없다(FY 4분기 파생행 누락, 이번 범위 밖).
- NIM·BIS·CET1·NPL 등 비율은 이번 범위가 아니며 기존처럼 공시 검색으로 대체한다.

## 운영 반영 결과

- 로컬: `financial.db`를 staging 파일로 원자 교체했다(`24028150…48c7f1`). integrity ok, FK 0, 테스트 69개 통과, AppTest 예외 0·기업 25
- MySQL: `2026-10-08-dart25-bank-xml-q1h1-v3`로 적재했다. 전체 검증 PASS 2회(승격 전, 승격 시) 후 current로 승격
  - `financial_metric_values` 1,250 → 1,460행. 신규 210행이 로컬과 값·검토상태·문서·청크 ID까지 일치
  - 문서 추출 164행의 `source_document_id`와 `source_chunk_id`가 모두 활성 원본 XML과 청크로 연결된다
  - 25개사 원천 CRC, 임베딩 BLOB, XML 399개 SHA-256, chunk→XML 147,720 연결, KRX 5×5 일치
- 변경되지 않음: `financial_facts`, CAPEX, 수집 상태, 공시, 청크, 임베딩, FAISS, 원본 XML, 벡터 SQLite

## 백업과 복구

- 로컬: `backups/local/20261008_pre_bank_xml_q1h1/financial.db` (`5fe54f67…`, `SHA256SUMS`, staging 보고서 사본)
- MySQL: `backups/mysql/20261008_pre_bank_xml_q1h1/dart_big_in_pre_bank_xml_q1h1.sql` (`a21355c0…`, 테이블 23개, `--single-transaction --hex-blob`), 적재 로그 `sync_q1h1_v3.log`

복구 순서:
1. 로컬 백업 파일을 운영 경로로 복사하고 SHA-256을 확인한다.
2. MySQL 덤프를 복원하거나, 복구한 로컬 파일로 새 버전을 적재한다.
3. 검증 스크립트를 실행하고 `--promote`한다.

이전 버전 `2026-10-08-dart25-bank-signfix-v2`의 행은 MySQL에 그대로 남아 있지 않다(자연키 upsert). current 표시만 되돌리면 복구되지 않는다.
