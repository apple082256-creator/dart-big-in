# 금융업 전용 재무 계층

은행·금융지주 데이터는 기존 `financial_facts`와 일반산업 계정 매핑을 보존하면서 아래 테이블을 추가해 관리한다. 원본 DART 사실값은 `financial_facts`에 그대로 남고, 서비스용 금융 지표는 출처를 추적할 수 있는 별도 계층으로 제공된다.

## 테이블

### `company_financial_profiles`

- 회사별 `BANK_HOLDING` 또는 `INTERNET_BANK` 프로필
- 우선 재무제표(`preferred_fs_div`)와 대체 재무제표(`fallback_fs_div`)
- 금융사에는 제조업 CAPEX를 적용하지 않음을 `capex_applicable=0`으로 명시

### `financial_collection_status`

- 기업·연도·보고서 슬롯별 CFS/OFS 조회 결과
- 실제 선택한 재무제표와 접수번호
- DART 재무 API가 자료를 반환하지 않은 슬롯은 `missing_reason`으로 명시

### `financial_metric_definitions`

- 총자산·총부채·자기자본·당기순이익
- 이자수익·이자비용·순이자이익
- 수수료수익·수수료비용·순수수료이익
- 대출채권·예수부채·신용손실비용
- NIM·BIS·CET1·NPL 등 문서 추출 대상 비율 지표

### `financial_metric_mappings`

- 금융 지표와 DART/XBRL 태그의 명시적 매핑
- 산업 프로필, 회사 예외, CFS/OFS 범위, 우선순위와 검토 상태 포함
- 일반산업 `account_mapping`은 변경하지 않음

### `financial_metric_values`

- 기업·기간·지표별 직접값 또는 파생값
- `source_kind`로 `DIRECT`, `DERIVED`, `DOCUMENT_EXTRACTED` 구분
- 접수번호, 원본 계정, 원천 fact key, 계산식과 구성값 보존
- 직접 공시값이 있으면 파생값보다 우선

## 금융 5개사 범위

| 회사 | 종목코드 | DART corp_code | 프로필 | 우선 재무제표 |
|---|---:|---:|---|---|
| 하나금융지주 | 086790 | 00547583 | BANK_HOLDING | CFS |
| KB금융 | 105560 | 00688996 | BANK_HOLDING | CFS |
| 신한지주 | 055550 | 00382199 | BANK_HOLDING | CFS |
| 우리금융지주 | 316140 | 01350869 | BANK_HOLDING | CFS |
| 카카오뱅크 | 323410 | 01133217 | INTERNET_BANK | OFS |

`KRX_BANK`는 이 다섯 회사를 비교하기 위한 내부 서비스 그룹이다. 공식 KRX 지수 코드와 공식 구성비를 확인한 상태가 아니므로 `official_index_code`와 구성종목 `weight`는 `NULL`로 유지한다.

## 원문·벡터 연결

금융사 공시도 기존 구조를 그대로 사용한다.

`chunk_metadata.chunk_id` → `chunk_source_documents.document_id` → `filing_source_documents.raw_xml`

`raw_xml`은 태그를 제거하지 않은 원본 바이트이며 SHA-256과 바이트 크기를 함께 검증한다. 검색용 `chunk_text`와 화면의 원문 문서는 서로 다른 저장 목적을 가진다.
