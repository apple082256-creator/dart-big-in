# DART Beginner 프로젝트 컬럼 정의서

작성 기준일: 2026-10-02

이 문서는 `dart_beginner`의 운영 데이터 흐름 전체를 대상으로 한다. 매핑 CSV, 재무 처리 CSV, 품질검증 CSV/JSON, 공시 parsed JSON, 공시 chunk JSONL, 운영 Financial DB와 Vector DB의 실제 필드를 조사했다. 원본 OpenDART raw 파일, 백업·legacy 경로, 검증용 DB와 이 문서 자체는 범위에서 제외했다.

전체 필드의 파일별 상세 정의는 `PROJECT_COLUMN_DICTIONARY.csv`에서 확인한다. 같은 필드명이 여러 산출물에 있으면 source와 처리 단계가 다르므로 각각 별도 행으로 기록했다.

## 1. 범위 요약

- 문서화한 소스 구조: 45개
- 문서화한 소스-필드 조합: 1,225개
- 고유 필드명: 229개

| 데이터 단계 | 소스 구조 수 | 소스-필드 조합 수 |
|---|---:|---:|
| Financial DB | 8 | 100 |
| Vector DB | 2 | 17 |
| 계정 매핑 | 7 | 61 |
| 공시 청킹 | 1 | 14 |
| 공시 파싱 | 1 | 17 |
| 분석 문서 | 1 | 14 |
| 재무 처리 | 4 | 111 |
| 품질검증 | 21 | 891 |

## 2. 문서를 읽는 순서

1. `source_category`와 `source_path_pattern`으로 데이터가 어느 단계에서 생성됐는지 확인한다.
2. `record_grain`으로 한 행 또는 한 JSON 객체가 무엇을 의미하는지 확인한다.
3. `definition`, `data_type`, `allowed_values_or_rule`로 값을 해석한다.
4. `origin`, `processing`, `is_calculated`로 원본·표준화·계산값을 구분한다.
5. `relation_keys`와 `caution`을 확인한 뒤 다른 파일이나 DB와 연결한다.

## 3. 핵심 공통 필드

| 필드 | 한글명 | 의미 | 주의사항 |
|---|---|---|---|
| `corp_code` | DART 기업코드 | OpenDART가 기업을 식별하는 8자리 코드입니다. | 문자열로 취급해 앞자리 0을 보존합니다. |
| `corp_name` | 기업명 | 공시 또는 재무 레코드의 기업명입니다. | corp_code와 함께 기업이 섞이지 않았는지 확인합니다. |
| `year` | 사업연도 | 분석 대상 사업연도입니다. | 제출연도가 아니라 공시가 다루는 사업연도입니다. |
| `report_type` | 표준 보고서 유형 | 프로젝트가 사용하는 FY, H1, Q1, Q3 보고서 구분입니다. | H1은 반기보고서이며 Q2라는 별도 DART 보고서 유형은 없습니다. |
| `rcept_no` | DART 접수번호 | 재무수치와 공시 원문을 같은 공시로 연결하는 DART 접수번호입니다. | Financial DB와 Vector DB 연결 시 핵심 근거 키입니다. |
| `fs_div` | 연결·별도 구분 | 연결재무제표 CFS와 별도재무제표 OFS를 구분합니다. | CFS와 OFS 값을 섞어 비교하지 않습니다. |
| `sj_div` | 재무제표 구분 | 계정이 속한 재무제표 종류입니다. BS, IS, CIS, CF 등이 사용됩니다. | 같은 account_id라도 sj_div와 보고서 맥락을 함께 판단합니다. |
| `account_id` | 계정 ID | DART/XBRL 재무계정 식별 태그입니다. | account_id만으로 확장 태그의 의미를 단정하지 않습니다. |
| `original_account_nm` | 원본 계정명 | 표준화 전 DART 계정명입니다. | 원문 추적을 위해 보존합니다. |
| `standard_account_nm` | 표준 계정명 | 기업별 표현 차이를 프로젝트의 공통 경제적 의미로 통일한 계정명입니다. | original_account_nm 및 account_id와 함께 추적합니다. |
| `raw_value` | 원본값 | DART 원본 표현 또는 계산의 출발값을 문자열로 보존합니다. | 정규화값이나 계산값으로 덮어쓰지 않습니다. |
| `normalized_value` | 정규화값 | 코드가 숫자·부호·단위를 정규화한 값입니다. | is_calculated=1이면 최종 조회에는 calculated_value가 우선할 수 있습니다. |
| `calculated_value` | 계산값 | 누적 차감 또는 승인된 합산 규칙으로 코드가 계산한 값입니다. | calculation_method 및 source_values를 함께 제시합니다. |
| `period_type` | 기간 의미 | 값이 시점 잔액인지 기간 흐름인지 나타냅니다. | point_in_time, quarterly, cumulative, annual을 구분합니다. |
| `value_type` | 값 유형 | 동일 계정의 시점·분기 단독·누적·연간 레코드를 구분합니다. | 분기 질문에는 quarterly만 사용합니다. |
| `is_calculated` | 계산값 여부 | 직접 제공값과 프로젝트 계산값을 구분하는 0/1 또는 boolean 표시입니다. | True/1이면 calculated_value와 calculation_method를 확인합니다. |
| `calculation_method` | 계산 방법 | 누적 차감·세부계정 합산 등 계산 산식을 설명합니다. | LLM이 임의로 재계산하지 않고 코드 산식을 사용합니다. |
| `value_source` | 값 산출 유형 | CAPEX가 직접 계정값인지 세부계정 합산 파생값인지 구분합니다. | direct와 derived를 답변에서 명시합니다. |
| `is_derived` | 파생 CAPEX 여부 | 세부 유형자산 취득계정 합산으로 만든 CAPEX인지 나타냅니다. | 1/True이면 source_account_names와 source_values를 확인합니다. |
| `source_account_names` | 원천 계정명 목록 | 표준화 또는 계산에 사용한 원천 계정명 목록입니다. | JSON 또는 구분자 문자열일 수 있습니다. |
| `chunk_id` | 청크 ID | 공시 검색용 텍스트 청크의 고유 식별자입니다. | 대개 rcept_no와 문서 내 순번으로 구성됩니다. |
| `section_name` | 섹션명 | 공시 본문에서 청크가 속한 상위 section 이름입니다. | 검색 결과의 문맥 제목으로 사용합니다. |
| `chunk_text` | 청크 본문 | Vector 검색 및 LLM 근거로 전달되는 텍스트 청크입니다. | 답변 근거 사용 시 rcept_no와 section_name을 함께 제시합니다. |
| `source_file` | 원천 파일 경로 | 가공 결과가 연결되는 원본 XML 또는 중간 파일의 프로젝트 상대경로입니다. | 로컬 사용자 홈 경로가 아닌 프로젝트 상대경로를 유지합니다. |
| `embedding` | 임베딩 벡터 | 청크 의미 검색에 사용하는 float32 벡터 BLOB입니다. | 사람이 읽는 텍스트가 아니며 모델·차원이 일치해야 합니다. |
| `embedding_model` | 임베딩 모델 | 벡터를 생성한 모델 또는 알고리즘 이름입니다. | 다른 모델 벡터와 직접 비교하지 않습니다. |
| `dimensions` | 벡터 차원 수 | 임베딩 벡터의 차원 수입니다. | BLOB 해석 시 모델과 함께 검증합니다. |

## 4. 재무 숫자 선택 규칙

- `raw_value`는 DART 원본 표현 또는 계산 출발값이며 보존용이다.
- `normalized_value`는 Python 코드가 숫자·단위·부호를 정규화한 값이다.
- `is_calculated=1`이면 `calculated_value`가 누적 차감 등으로 산출된 값이며 `calculation_method`와 `source_values`를 함께 확인한다.
- `value_type=quarterly`는 해당 분기 단독값, `cumulative`는 연초부터 누적값, `point_in_time`은 기준일 잔액, `annual`은 연간값이다.
- 분기 질문에 누적값을 사용하거나 CFS와 OFS를 섞지 않는다.

## 5. CAPEX 필드 규칙

- 직접 유형자산 취득 계정이 있으면 `value_source=direct`, `is_derived=0`이다.
- 직접 계정이 없으면 승인된 세부 유형자산 취득 계정을 합산하며 `value_source=derived`, `is_derived=1`이다.
- derived 값은 `source_account_ids`, `source_account_names`, `source_values`, `source_account_count`로 재현한다.
- 누적 차감으로 만든 분기값은 `is_calculated`, `calculated_value`, `calculation_method`로 직접 공시값과 구분한다.

## 6. 공시와 Vector 필드 규칙

- parsed JSON 한 파일은 공시 한 건과 그 하위 section 구조를 의미한다.
- chunk JSONL과 `filing_chunks`의 한 행은 공시 전체가 아니라 검색용 텍스트 chunk 한 건이다.
- `original_text`는 원문 보존, `cleaned_text`는 정제 결과, `chunk_text`는 검색·LLM 근거용 텍스트다.
- `embedding`은 사람이 읽는 본문이 아니라 검색용 BLOB이다. `embedding_model`과 `dimensions`가 일치해야 한다.
- `source_file`과 `rcept_no`를 통해 chunk에서 원본 XML까지 추적한다.

## 7. 프로젝트 연결 키

| 연결 목적 | 실제 키 |
|---|---|
| 기업 식별 | `corp_code` |
| 사업연도·보고서 식별 | `year + report_type` |
| 동일 공시 근거 연결 | `rcept_no` |
| 재무계정 표준화 | `account_key + sj_div` |
| Financial DB 내부 기간 연결 | `period_id` → `periods.period_id` |
| 공시 청크 식별 | `chunk_id` |

Financial DB의 숫자와 Vector DB의 공시 문맥은 `corp_code`, `year`, `report_type`, `rcept_no`를 기준으로 분석 단계에서 연결된다. `financial_facts`와 `capex_facts`의 연도·보고서 유형은 `period_id`를 통해 `periods`에서 얻는다.

## 8. 실제 소스 구조 목록

| 단계 | 소스 | 형식 | 한 행/문서 수 | 필드 수 | 경로 패턴 |
|---|---|---|---:|---:|---|
| Financial DB | `financial.db:account_mapping` | SQLite TABLE | 668 | 8 | `db/financial/financial.db#account_mapping` |
| Financial DB | `financial.db:capex_build_metadata` | SQLite TABLE | 6 | 2 | `db/financial/financial.db#capex_build_metadata` |
| Financial DB | `financial.db:capex_facts` | SQLite TABLE | 120 | 31 | `db/financial/financial.db#capex_facts` |
| Financial DB | `financial.db:companies` | SQLite TABLE | 5 | 3 | `db/financial/financial.db#companies` |
| Financial DB | `financial.db:financial_facts` | SQLite TABLE | 10,849 | 23 | `db/financial/financial.db#financial_facts` |
| Financial DB | `financial.db:financial_facts_final` | SQLite VIEW | 10,969 | 24 | `db/financial/financial.db#financial_facts_final` |
| Financial DB | `financial.db:periods` | SQLite TABLE | 12 | 5 | `db/financial/financial.db#periods` |
| Financial DB | `financial.db:pipeline_runs` | SQLite TABLE | 1 | 4 | `db/financial/financial.db#pipeline_runs` |
| Vector DB | `filings.db:filing_chunks` | SQLite TABLE | 10,622 | 15 | `db/vector/filings.db#filing_chunks` |
| Vector DB | `filings.db:vector_metadata` | SQLite TABLE | 2 | 2 | `db/vector/filings.db#vector_metadata` |
| 계정 매핑 | `account_mapping.csv` | CSV | 0 | 0 | `data/mapping/account_mapping.csv` |
| 계정 매핑 | `account_mapping_core.csv` | CSV | 78 | 11 | `data/mapping/account_mapping_core.csv` |
| 계정 매핑 | `account_mapping_draft.csv` | CSV | 668 | 12 | `data/mapping/account_mapping_draft.csv` |
| 계정 매핑 | `account_mapping_prioritized.csv` | CSV | 668 | 15 | `data/mapping/account_mapping_prioritized.csv` |
| 계정 매핑 | `account_mapping_review.csv` | CSV | 371 | 8 | `data/mapping/account_mapping_review.csv` |
| 계정 매핑 | `account_mapping_review_prioritized.csv` | CSV | 371 | 10 | `data/mapping/account_mapping_review_prioritized.csv` |
| 계정 매핑 | `capex_component_mapping_v2.csv` | CSV | 13 | 5 | `data/mapping/capex_component_mapping_v2.csv` |
| 공시 청킹 | `filing_chunks.jsonl` | JSONL pattern | 27,036 | 14 | `data/processed/filings/chunks/**/*.jsonl` |
| 공시 파싱 | `parsed_filings.json` | JSON pattern | 96 | 17 | `data/processed/filings/parsed/**/*.json` |
| 분석 문서 | `account_standardization_comparison.csv` | CSV | 24 | 14 | `docs/analysis/account_standardization_comparison.csv` |
| 재무 처리 | `account_comparison.csv` | CSV | 8,416 | 10 | `data/processed/account_comparison.csv` |
| 재무 처리 | `financial_accounts.csv` | CSV | 8,377 | 36 | `data/processed/financial/financial_accounts.csv` |
| 재무 처리 | `financial_accounts_wide.csv` | CSV | 8,428 | 36 | `data/processed/financial/financial_accounts_wide.csv` |
| 재무 처리 | `financial_facts.csv` | CSV | 10,849 | 29 | `data/processed/financial/financial_facts.csv` |
| 품질검증 | `capex_final_validation.csv` | CSV | 120 | 21 | `data/processed/quality/capex_final_validation.csv` |
| 품질검증 | `capex_mapping_validation.csv` | CSV | 120 | 21 | `data/processed/quality/capex_mapping_validation.csv` |
| 품질검증 | `filing_collection_report.csv` | CSV | 60 | 8 | `data/processed/quality/filing_collection_report.csv` |
| 품질검증 | `filing_quality_report.csv` | CSV | 60 | 17 | `data/processed/quality/filing_quality_report.csv` |
| 품질검증 | `final_db_llm_validation.json` | JSON | 1 | 146 | `data/processed/quality/final_db_llm_validation.json` |
| 품질검증 | `final_db_llm_validation_summary.csv` | CSV | 7 | 26 | `data/processed/quality/final_db_llm_validation_summary.csv` |
| 품질검증 | `financial_core_validation.csv` | CSV | 36 | 9 | `data/processed/quality/financial_core_validation.csv` |
| 품질검증 | `financial_final_regression.csv` | CSV | 10,969 | 15 | `data/processed/quality/financial_final_regression.csv` |
| 품질검증 | `financial_quality_report.csv` | CSV | 61 | 14 | `data/processed/quality/financial_quality_report.csv` |
| 품질검증 | `gemini_failed_question_retest_v2.json` | JSON | 1 | 110 | `data/processed/quality/gemini_failed_question_retest_v2.json` |
| 품질검증 | `gemini_failed_question_retest_v2_final.json` | JSON | 1 | 113 | `data/processed/quality/gemini_failed_question_retest_v2_final.json` |
| 품질검증 | `gemini_llm_validation.json` | JSON | 1 | 85 | `data/processed/quality/gemini_llm_validation.json` |
| 품질검증 | `gemini_llm_validation_summary.csv` | CSV | 5 | 14 | `data/processed/quality/gemini_llm_validation_summary.csv` |
| 품질검증 | `gemini_llm_validation_summary_v2.csv` | CSV | 5 | 20 | `data/processed/quality/gemini_llm_validation_summary_v2.csv` |
| 품질검증 | `gemini_llm_validation_v2.json` | JSON | 1 | 122 | `data/processed/quality/gemini_llm_validation_v2.json` |
| 품질검증 | `integration_validation.json` | JSON | 5 | 31 | `data/processed/quality/integration_validation.json` |
| 품질검증 | `post_promotion_smoke_test.json` | JSON | 1 | 73 | `data/processed/quality/post_promotion_smoke_test.json` |
| 품질검증 | `post_promotion_smoke_test_summary.csv` | CSV | 14 | 8 | `data/processed/quality/post_promotion_smoke_test_summary.csv` |
| 품질검증 | `search_validation_report.csv` | CSV | 20 | 13 | `data/processed/quality/search_validation_report.csv` |
| 품질검증 | `unit_normalization_test_v2.json` | JSON | 1 | 12 | `data/processed/quality/unit_normalization_test_v2.json` |
| 품질검증 | `vector_search_comparison.csv` | CSV | 50 | 13 | `data/processed/quality/vector_search_comparison.csv` |

## 9. 문서별 역할

| 문서 | 역할 |
|---|---|
| `PROJECT_COLUMN_DICTIONARY.md` / `.csv` | 프로젝트 전체 데이터 흐름의 실제 필드 의미·출처·연결·주의사항 |
| 기존 `COLUMN_DICTIONARY.csv` | 기존 19개 주요 CSV의 파일별 컬럼 상세 정의 |
| `CSV_GUIDE.md` | 주요 CSV 파일의 목적과 읽는 방법 |
| `TABLE_DICTIONARY.md` / `.csv` | 운영 SQLite DB의 테이블·VIEW 역할과 행 단위 |

## 10. 범위에서 제외한 항목

- `data/raw/**`: 원본 보존 영역이며 API 응답별 가변 필드를 프로젝트 표준 컬럼으로 간주하지 않았다.
- `data/processed/backups/**`, `data/processed/legacy_initial_company_set_*`: 백업·레거시 중복 산출물이다.
- `financial_final.db`, `financial_capex_v2.db`, `financial_before_capex_fix.db`, `filings_e5.db`: 운영 기준이 아닌 검증·백업 DB다.
- `.env`: 읽지 않았고 문서화 대상에서도 제외했다.

## 11. 최종 검증

- 모든 CSV 컬럼은 실제 헤더에서 읽었다.
- JSON/JSONL 필드는 실제 문서와 레코드에서 재귀적으로 수집했다.
- 운영 SQLite DB의 TABLE/VIEW와 컬럼은 `sqlite_master` 및 `PRAGMA table_info`로 확인했다.
- 운영 DB 두 개의 `PRAGMA integrity_check` 결과는 `ok`였다.
- CSV의 소스-필드 조합 수와 실제 수집 결과를 다시 대조했다.
- 원본·기존 데이터 파일은 수정하지 않았다.
- 1바이트로 비어 있는 `data/mapping/account_mapping.csv`는 실제 컬럼이 없어 소스 목록에는 표시하지만 컬럼 행은 생성하지 않았다.
