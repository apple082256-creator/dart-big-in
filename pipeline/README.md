# DART 재무계정 표준화 파이프라인

OpenDART에서 5개 기업의 연결재무제표를 수집하고, 기업·연도·분기별 계정명과
`account_id`를 비교해 표준화 매핑 초안을 만든 뒤 SQLite Financial DB에 저장합니다.
원본 API 응답은 수정하지 않고 `data/raw`에 보존합니다.

## 프로젝트 구성

이 레포는 데이터 계층과 사용자 서비스를 함께 담습니다. 둘은 MySQL로만 연결됩니다.

```
src/, infra/mysql/  수집·정제·표준화·임베딩 → MySQL 적재 (데이터 계층)
                          │  읽기 전용 dart_backend 계정, 현재 검증 버전(data_versions.is_current)만 조회
                          ▼
service/            백엔드(app.py, snapdart/, dart_remote/) + 프론트(web/) + 공시 신호 워드클라우드
app/app.py          데이터팀 내부 검증 뷰어(Streamlit, 로컬 SQLite·FAISS 직접 조회)
```

| 경로 | 역할 | 기본 포트 |
| --- | --- | --- |
| `service/` | 산업·기업 화면, Gemini 인사이트, 워드클라우드, 원문 출처 창 | 8501 |
| `app/app.py` | 재무·공시 검색 결과와 LLM 근거 확인용 내부 도구 | 8502 (`.streamlit/config.toml`) |

### 사용자 서비스 실행 (WSL/Linux)

`service/.env`에 MySQL(`dart_backend`), `GEMINI_API_KEY`, `KRX_API`, `OPENDART_API_KEY`,
`SEARCH_BACKEND=numpy`를 둡니다. Linux에서는 `SEARCH_BACKEND` 기본값이 `faiss`이므로 반드시 지정합니다.
의존성은 루트 `requirements.txt`에 합쳐져 있어 루트 `.venv`를 그대로 씁니다.

```bash
cd service
../.venv/bin/python -X utf8 -m snapdart.market                     # 주가·KRX 지수 수집
../.venv/bin/python -X utf8 -m snapdart.analyze --llm --sector 반도체  # 보고서 생성(Gemini 호출)
../.venv/bin/python -X utf8 app.py --no-market-refresh             # http://127.0.0.1:8501
../.venv/bin/python -B -X utf8 -m unittest discover -s tests -p "test_*.py"
```

`service/deploy/`(클라우드 배포 패키지)는 별도 보관 대상이라 레포에 없고, 관련 일치 검사 테스트는 자동으로 건너뜁니다.
서비스 내부 설계와 Windows 실행법은 [service/README.md](service/README.md)와
[service/docs/4차구상/README.md](service/docs/4차구상/README.md)를 참고하세요.

## 기본 분석 기업

- 삼성전자, SK하이닉스, 한미반도체, 주성엔지니어링, 원익IPS
- 기본 수집 기간: 2023~2025년
- 보고서: 1분기, 반기, 3분기, 사업보고서

## 설치와 실행

```bash
cd /Users/minseongyi/project/coding/my-project/dart_beginner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 기존 .env가 있으면 덮어쓰지 마세요
python -m src.pipeline
```

공시 본문은 재무 파이프라인이 확보한 정확한 `rcept_no`를 이용해 별도로 수집합니다.

```bash
python -m src.filing_pipeline
```

연도나 기업을 바꿔 실행할 수도 있습니다.

```bash
python -m src.pipeline --years 2024 2025 --companies 삼성전자 SK하이닉스
```

## 생성 결과

```text
data/raw/corp_codes/                 DART 기업코드 원본 ZIP/XML
data/raw/financial/                  API 원본 JSON (재실행 시 재사용)
data/raw/filings/zip/                document.xml 응답 ZIP 원본
data/raw/filings/xml/                ZIP에서 추출한 공시 원문 XML
data/raw/filings/search/             list.json 검색 응답 원본 JSON
data/processed/financial/financial_accounts_wide.csv 원본 필드 기반 정리 CSV
data/processed/financial/financial_facts.csv 기간 의미별 long-form CSV
data/processed/filings/parsed/       섹션 단위 파싱 JSON
data/processed/filings/chunks/       Vector DB 입력용 JSONL 청크
data/processed/quality/filing_quality_report.csv 문서별 품질검증 결과
data/processed/quality/search_validation_report.csv 검색 상위 결과 검증
data/processed/account_comparison.csv 기업/연도/분기별 계정 비교표
data/mapping/account_mapping_draft.csv 표준화 매핑 초안
data/mapping/account_mapping_prioritized.csv 전체 매핑 검토 우선순위
data/mapping/account_mapping_core.csv MVP 핵심 24개 표준계정 매핑
data/mapping/capex_component_mapping_v2.csv CAPEX 직접/세부/제외 계정 규칙
data/mapping/account_mapping_review_prioritized.csv 충돌 검토 우선순위
db/financial/financial.db            현재 운영/서비스용 기준 Financial DB
db/financial/financial_final.db      승격 직전 검증 완료 스냅샷
db/financial/financial_capex_v2.db   CAPEX 로직 검증용 과도기 DB
db/archive/financial_before_capex_fix.db 승격 전 Financial DB 백업
db/vector/filings.db                 Feature Hashing legacy/rollback Vector DB
db/vector/filings_e5.db              Multilingual E5 비교 Vector DB
db/vector/filings_bge_m3.db          BGE-M3 Dense 운영 메타데이터·임베딩 DB
db/vector/filings_bge_m3.faiss       BGE-M3 Dense 운영 FAISS IndexFlatIP 인덱스
data/processed/filings/chunks_v2/    BGE-M3용 600토큰·100토큰 중복 청크
```

매핑 초안의 `mapping_status`가 `review_required`인 행을 검토한 뒤
`standard_account_name`을 수정하면 됩니다. `account_id`가 있는 계정은 ID를 우선
키로 사용하고, ID가 없는 계정만 정규화한 계정명을 키로 사용합니다.

## 프로젝트 문서

프로젝트 설명과 데이터 정의서는 `docs/`에서 관리합니다. 루트 `README.md`는 설치,
실행, 전체 데이터 흐름을 안내하고, 세부 분석 및 컬럼 정의는 아래 문서에서 확인합니다.

### 계정 표준화 분석

- [`docs/analysis/ACCOUNT_STANDARDIZATION_COMPARISON.md`](docs/analysis/ACCOUNT_STANDARDIZATION_COMPARISON.md): 팀원 6개 대표 계정과 프로젝트 24개 핵심 표준계정의 비교 결과
- [`docs/analysis/account_standardization_comparison.csv`](docs/analysis/account_standardization_comparison.csv): 계정별 일치 여부, 기업 커버리지 및 추가 계정 필요성을 정리한 비교 데이터

### 데이터 카탈로그

- [`docs/data_catalog/CSV_GUIDE.md`](docs/data_catalog/CSV_GUIDE.md): 주요 CSV 파일의 목적, 주요 컬럼, 실제 예시 및 사용 시점
- [`docs/data_catalog/COLUMN_DICTIONARY.csv`](docs/data_catalog/COLUMN_DICTIONARY.csv): 주요 CSV 19개의 컬럼별 정의
- [`docs/data_catalog/PROJECT_COLUMN_DICTIONARY.md`](docs/data_catalog/PROJECT_COLUMN_DICTIONARY.md): Financial DB, Vector DB, CSV, JSON 및 JSONL을 포함한 프로젝트 전체 데이터 구조 설명
- [`docs/data_catalog/PROJECT_COLUMN_DICTIONARY.csv`](docs/data_catalog/PROJECT_COLUMN_DICTIONARY.csv): 프로젝트 전체 소스와 필드의 형식, 출처, 처리 방식, 연결 키 및 주의사항

### Vector 검색 v2

- [`docs/vector/BGE_M3_FAISS_DESIGN.md`](docs/vector/BGE_M3_FAISS_DESIGN.md): BGE-M3, FAISS, SQLite 필터 및 토큰 청킹 v2 설계
- [`docs/vector/VECTOR_RETRIEVAL_BENCHMARK_V2.md`](docs/vector/VECTOR_RETRIEVAL_BENCHMARK_V2.md): 50개 정답 질문 기반 검색 품질과 승격 판단
- [`docs/vector/BGE_M3_DENSE_PROMOTION.md`](docs/vector/BGE_M3_DENSE_PROMOTION.md): 현재 운영 기본값, 무결성 계약 및 rollback 절차

현재 문서 구조는 다음과 같습니다.

```text
docs/
├── analysis/
│   ├── ACCOUNT_STANDARDIZATION_COMPARISON.md
│   └── account_standardization_comparison.csv
├── data_catalog/
│   ├── CSV_GUIDE.md
│   ├── COLUMN_DICTIONARY.csv
│   ├── PROJECT_COLUMN_DICTIONARY.md
│   └── PROJECT_COLUMN_DICTIONARY.csv
└── vector/
    ├── BGE_M3_FAISS_DESIGN.md
    └── VECTOR_RETRIEVAL_BENCHMARK_V2.md
```

## DB 구조

- `companies`: 기업 마스터
- `periods`: 연도/분기/보고서 마스터
- `account_mapping`: 표준화 매핑
- `financial_facts`: 재무 계정 원장
- `capex_facts`: 직접 또는 파생한 `유형자산취득(CAPEX)` 원장
- `capex_build_metadata`: 생성 원천·해시와 생성 건수
- `pipeline_runs`: 실행 이력

`src/retrieval`, `src/llm`은 이후 Vector DB 및 LLM 분석을 붙일 경계로 유지했습니다.
현재 파이프라인은 수집/정제/매핑/관계형 DB 적재까지만 책임집니다.

## 기간 의미 표준화

`financial_facts`는 계정 하나를 기간 의미에 따라 여러 레코드로 저장할 수 있습니다.

- `BS`: `point_in_time`. 보고기간 말 현재의 잔액이며 `period_start`는 비웁니다.
- `IS`, `CIS`: 분기 보고서의 `thstrm_amount`는 `quarterly`,
  `thstrm_add_amount`는 `cumulative`로 각각 저장합니다. 사업보고서 값은 `annual`입니다.
- `CF`: 분기·반기·3분기 값은 연초 이후 `cumulative`, 사업보고서는 `annual`입니다.
  분기 단독값은 누적값 차감으로 별도 계산합니다.
- `SCE`: 자본변동표는 기간 흐름이지만 단순 분기 차감 시 의미가 왜곡될 수 있어
  `cumulative` 또는 `annual` 원본만 보존합니다.

계산된 분기값은 원본값을 바꾸지 않습니다. `is_calculated=1`로 구분하고,
`calculated_value`와 `calculation_method`에 계산값과 사용한 누적값을 기록합니다.
직접 제공된 값은 `raw_value`와 파싱된 `normalized_value`에 저장됩니다.

```text
H1 quarterly = H1 cumulative - Q1 cumulative
Q3 quarterly = Q3 cumulative - H1 cumulative
FY quarterly = FY annual - Q3 cumulative
```

`period_start`와 `period_end`는 현재 대상 기업의 12월 결산을 기준으로 보고서 유형에서
추론한 달력 날짜입니다. 원본 DART JSON은 `data/raw/financial`에서 변경 없이 유지됩니다.

## CAPEX 표준계정

좁은 의미의 CAPEX는 현금흐름표(`sj_div=CF`)에 있는 유형자산 취득 현금유출만
`유형자산취득(CAPEX)`로 표준화합니다. 같은 기업·연도·보고서에
`ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`
(`유형자산의 취득`)이 있으면 그 직접 계정만 사용합니다. 직접 계정이 없을 때만
토지, 건물, 구축물, 기계장치, 차량운반구, 공구와기구, 비품, 건설중인자산의 취득을
합산합니다. 무형자산, 소프트웨어, 회원권, 투자부동산, 영업권은 제외하며 주석 수치는
합산하지 않습니다.

`raw_value`와 `normalized_value`에는 원본 부호를 유지하고, 서비스용 투자 지출 규모는
양수인 `cash_outflow_amount`로 제공합니다. 직접/파생 여부는 `value_source`, 파생에
사용한 계정은 `source_account_ids`, `source_account_names`, `source_values`로 추적할 수
있습니다. 누적 합계를 먼저 만든 뒤 `H1-Q1`, `Q3-H1`, `FY-Q3`으로 분기 단독값을
계산합니다. 본 파이프라인은 정규화된 재무 원장을 임시 DB에 먼저 생성하고 무결성을
확인한 뒤 운영 기준인 `financial.db`를 원자적으로 교체합니다. DB에는 `capex_facts`와
통합 조회 뷰 `financial_facts_final`이 함께 생성됩니다. `financial_final.db`는 승격 직전
검증 스냅샷, `financial_capex_v2.db`는 이전 CAPEX 검증용 과도기 DB로 보존합니다.

재무 파이프라인은 운영 기준 DB를 처음부터 다시 생성합니다. 저장된 처리 결과로 API 호출
없이 검증 스냅샷을 재현하거나, 이전 v2 비교 DB를 다시 만들려면 다음을 실행합니다.

```bash
# financial_final.db 검증 스냅샷 재생성
python -m src.db.final_db
python -m unittest -v tests.test_capex_derivation

# 선택 사항: 이전 CAPEX v2 비교 DB 재생성
python -m src.db.capex_db
```

조회기와 Streamlit은 설정의 `FINANCIAL_DB_PATH`, 즉 `db/financial/financial.db`만 기본
Financial DB로 사용합니다. `HY`, `H1`은 모두 반기 보고서로 받습니다.
KRX 반도체·철강·자동차·헬스케어·은행은 별도 DB로 복제하지 않고 기업 중심 DB 위의
다대다 연결 계층으로 관리합니다. 구성 이력, `stock_code` → `corp_code` 매핑,
복수 기업 Financial/BGE 검색 정책은
[`docs/data/KRX_INDEX_INTEGRATION.md`](docs/data/KRX_INDEX_INTEGRATION.md)를 참고하세요.
금융업 전용 지표 계층은
[`docs/handoff/FINANCIAL_INDUSTRY_SCHEMA.md`](docs/handoff/FINANCIAL_INDUSTRY_SCHEMA.md)를 참고하세요.
CAPEX 조회 결과의 `normalized_value`와 `cash_outflow_amount`는 서비스용 양수 금액이고,
원본 부호를 반영한 합계는 `signed_normalized_value`에서 확인합니다.

```python
from src.retrieval.financial_retriever import get_financial_context

capex = get_financial_context(
    corp_name="주성엔지니어링",
    year=2024,
    report_type="HY",
    account_name="유형자산취득(CAPEX)",
)
```

기업·연도·보고서별 직접/파생 방식과 값은
`data/processed/quality/capex_mapping_validation.csv`에서 확인합니다. 현재 5개 기업은
삼성전자·SK하이닉스·한미반도체·원익IPS가 `direct`, 주성엔지니어링이 `derived`입니다.

## Financial DB와 Vector DB의 원천 구분

현재 서비스의 기본 DB 경로는 다음과 같습니다.

```text
Financial DB: db/financial/financial.db
Vector DB:    db/vector/filings_bge_m3.db + db/vector/filings_bge_m3.faiss
```

Financial DB는 핵심 표준계정, 분기/누적 구분, CFS/OFS 구분, `rcept_no` 연결과
CAPEX direct/derived 표준화를 포함합니다.

두 데이터 흐름은 접수번호(`rcept_no`)로 연결하지만 원천은 서로 다릅니다.

```text
OpenDART fnlttSinglAcntAll.json
  -> data/raw/financial/*.json
  -> 기간/계정 표준화
  -> Financial DB

OpenDART document.xml (실제 응답은 ZIP binary)
  -> data/raw/filings/zip/*.zip
  -> data/raw/filings/xml/*.xml
  -> 섹션 파싱 JSON
  -> 출처 메타데이터가 포함된 JSONL 청크
  -> Vector DB (후속 단계)
```

사용하는 OpenDART endpoint는 다음과 같습니다.

- `GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json`: 구조화 재무제표 JSON
- `GET https://opendart.fss.or.kr/api/list.json`: 공시 목록 검색 JSON
- `GET https://opendart.fss.or.kr/api/document.xml`: 접수번호별 공시 원문 ZIP
- `GET https://opendart.fss.or.kr/api/corpCode.xml`: 기업코드 ZIP/XML

현재 공시 파이프라인은 재무 응답에 포함된 정확한 접수번호를 우선 사용하므로 같은
보고서의 원문과 재무수치를 직접 연결합니다. `list.json` 메서드는 재무 API에 없는
공시를 추가로 찾을 때 사용할 수 있으며 검색 응답도 원본 영역에 저장합니다.

파싱 결과와 청크에는 `corp_name`, `corp_code`, `report_name`, `report_type`, `year`,
`rcept_no`, `section_name`, `source_file`, `original_text`, `cleaned_text`가 포함됩니다.
`source_file`로 언제든 보존된 XML 원문까지 추적할 수 있습니다.

전체 수집 전에 일부만 검증하려면 다음처럼 실행할 수 있습니다.

```bash
python -m src.filing_pipeline --companies 삼성전자 --years 2025 --limit 1
```

## 전체 데이터셋 구축 순서

```bash
# 1. 구조화 재무정보 수집 및 기본 financial.db 구축
python -m src.pipeline

# 저장된 처리 결과로 승격 전 검증 스냅샷만 재생성할 때
python -m src.db.final_db

# 2. 동일 rcept_no의 공시 원문 ZIP/XML 수집, 파싱, 청킹, 품질보고서 생성
python -m src.filing_pipeline

# 3. 품질 기준을 통과한 청크 임베딩 및 Vector DB 구축
python -m src.vector_pipeline

# 4. 대표 검색 질문의 상위 5개 결과 저장
python -m src.quality.search_validation

# 5. 계정 우선순위·핵심 매핑과 Financial DB 품질검증
python -m src.quality.account_mapping_quality
python -m src.quality.financial_quality
python -m unittest -v tests.test_capex_derivation

# 6. 선택 사항: 별도 transformer DB와 검색 비교 보고서
HF_HOME="$PWD/db/vector/model_cache" python -m src.retrieval.transformer_retriever
HF_HOME="$PWD/db/vector/model_cache" python -m src.quality.vector_comparison

# 7. 통합 라우팅·출처 검증
python -m src.quality.integration_validation

# 8. Gemini 실제 답변 5건 및 근거 일치 자동검증 (명시적으로 외부 호출할 때만 실행)
python -m src.quality.llm_validation

# 9. 결정적 원화 단위 환산 테스트와 v2 Gemini 재검증
python -m unittest -v tests.test_financial_units
python -m src.quality.unit_normalization_validation
python -m src.quality.llm_validation_v2

# 10. 기본 DB 승격 후 Financial/Vector/CAPEX/Streamlit/Gemini 종합 점검
python -m src.quality.post_promotion_smoke_test
```

현재 운영 공시 검색은 `BAAI/bge-m3` 1,024차원 정규화 임베딩을 사용하는 Pure Dense
방식입니다. 운영 자산은 `filings_bge_m3.db`와 `filings_bge_m3.faiss`이며, 5개 기업·
60개 공시의 검색 가능 청크 21,797개를 포함합니다. SQLite 메타데이터 필터를 먼저
적용하고 허용 후보에 대해 FAISS `IndexFlatIP` 정확 검색을 수행합니다.

기존 `local-ko-en-finance-hashing-v1` 1,536차원 Feature Hashing Hybrid DB는 삭제하지
않고 명시적 rollback 경로로 보존합니다. `filings_e5.db`는 비교 자산입니다. 과거 검색
벤치마크에서 BGE-M3가 모든 지표를 개선한 것은 아니며, 이번 기본값 변경은 2026-10-06
사용자 운영 결정에 따른 승격입니다.

```bash
# 운영 기본값
FILING_RETRIEVER=bge_m3_dense streamlit run app/app.py

# 즉시 legacy rollback
FILING_RETRIEVER=feature_hashing_hybrid streamlit run app/app.py

# 자동 fallback은 기본 OFF이며 필요한 경우에만 명시적으로 활성화
FILING_RETRIEVER_AUTO_FALLBACK=true streamlit run app/app.py
```

지원하지 않는 설정값은 legacy로 조용히 대체하지 않고 오류를 발생시킵니다. 자세한 승격
기록은 [`docs/vector/BGE_M3_DENSE_PROMOTION.md`](docs/vector/BGE_M3_DENSE_PROMOTION.md)에
있습니다.

품질보고서는 다음 경로에 생성됩니다.

- `data/processed/quality/financial_quality_report.csv`
- `data/processed/quality/financial_core_validation.csv`
- `data/processed/quality/capex_mapping_validation.csv`
- `data/processed/quality/capex_final_validation.csv`
- `data/processed/quality/financial_final_regression.csv`
- `data/processed/quality/final_db_llm_validation.json`
- `data/processed/quality/final_db_llm_validation_summary.csv`
- `data/processed/quality/vector_search_comparison.csv`
- `data/processed/quality/integration_validation.json`
- `data/processed/quality/gemini_llm_validation.json`
- `data/processed/quality/gemini_llm_validation_summary.csv`
- `data/processed/quality/unit_normalization_test_v2.json`
- `data/processed/quality/gemini_llm_validation_v2.json`
- `data/processed/quality/gemini_llm_validation_summary_v2.csv`
- `data/processed/quality/post_promotion_smoke_test.json`
- `data/processed/quality/post_promotion_smoke_test_summary.csv`
- `data/processed/quality/bge_m3_dense_promotion_report.json`

LLM 통합 단계에서는 다음 함수를 사용합니다.

```python
from src.retrieval.financial_retriever import get_financial_context
from src.retrieval.operational_retriever import retrieve_filing_context

financial = get_financial_context("삼성전자", 2025, "FY")
filing_result = retrieve_filing_context(
    "반도체 사업 위험요인", corp_name="삼성전자", year=2025,
    report_type="FY", top_k=5,
)
filings = filing_result.rows
```

두 결과 모두 `corp_code`, `year`, `report_type`, `rcept_no`로 동일 보고서를 연결할 수
있습니다. Financial DB는 금액의 `currency`, 원본값, 정규화값, 계산값과 계산 입력값을
구분하며 결측값을 0으로 바꾸지 않습니다.

## 통합 질문 라우팅과 LLM 연결

`src.llm.analyzer.analyze_company()`는 숫자 질문을 Financial DB, 설명 질문을 Vector DB,
원인·재무효과 질문을 양쪽 DB로 라우팅합니다. 결과에는 `financial_sources`와
`filing_sources`가 항상 포함됩니다. 기본 실행은 외부 전송 없이 근거 미리보기를 만들며,
실제 LLM 호출은 호출자가 명시적으로 제공한 `llm_client(prompt)` 콜백으로만 수행합니다.

`src.quality.llm_validation`은 `.env`의 `GEMINI_API_KEY`를 인증 헤더에만 사용합니다.
요청 본문에는 공개 DART 재무수치, 공개 공시 청크, 공개 접수번호와 질문만 포함되며,
API 키·로컬 경로·시스템 정보가 감지되면 호출 전에 차단합니다. 검증 결과에는 질문별
실제 재무 근거, 청크 본문, `rcept_no`, Gemini 답변과 숫자 환각·근거 불일치·연도 혼합·
다른 기업 혼입 검사 결과가 함께 저장됩니다. 기존 DB와 원본 파일은 읽기만 합니다.

## 금액 단위 정규화

`src.financial_units.normalize_financial_value()`가 `원`, `천원`, `백만원`, `억원`,
`조원`을 LLM 호출 전에 KRW로 결정적으로 환산합니다. 원본값·원본단위와 함께
`normalized_value`, `normalized_unit`, `unit_conversion_applied`, `conversion_formula`,
원화·억원·조원 표시 문자열을 유지합니다. 검색된 공시 청크도 명시된 단위와 주요
시설투자 표를 파싱하여 같은 구조로 프롬프트에 제공합니다.

LLM은 제공된 `normalized_value`와 `display_*` 문자열을 그대로 인용하며 자체 단위
변환을 하지 않습니다. v2 검증기는 답변 금액을 근거 값과 대조하고 10배, 100배,
1,000배, 100,000,000배 차이를 단위 환산 오류로 기록합니다. Streamlit의 재무·공시
출처 상세 화면에서는 원본 단위와 코드 환산 원화/조원 표시를 함께 확인할 수 있습니다.

```python
from src.llm.analyzer import analyze_company

result = analyze_company(
    "삼성전자 2025년 실적 변화와 그 원인을 설명해줘.",
    corp_name="삼성전자", year=2025, report_type="FY",
)
```

## Streamlit 실행

운영 기준 DB를 다시 구축한 뒤 앱을 실행하는 기본 순서는 다음과 같습니다.

```bash
cd /Users/minseongyi/project/coding/my-project/dart_beginner
source .venv/bin/activate
python -m src.pipeline
streamlit run app/app.py
```

화면에서 기업·연도·보고서·검색 방식을 선택할 수 있으며 라우팅 결과, 재무 출처,
공시 출처와 LLM 전달용 근거 프롬프트를 확인할 수 있습니다.
이 화면은 데이터팀 내부 검증용이며 기본 포트는 8502입니다. 사용자용 화면은 `service/`입니다.

은행·금융지주 5개사를 포함한 25개사 운영본의 적재 범위, 해시와 MySQL 검증 결과는
[DART 25개사·은행 금융지표·원본 XML 동기화 결과](docs/handoff/MYSQL_DART25_BANK_XML_SYNC_REPORT_20261008.md)를
참고하세요. 금융업 전용 지표 구조는
[금융업 전용 재무 계층](docs/handoff/FINANCIAL_INDUSTRY_SCHEMA.md)에 정리되어 있습니다.

## BGE-M3 + FAISS 운영 자산과 과거 평가

기존 Feature Hashing DB를 덮어쓰지 않고 별도 BGE 자산을 사용합니다. 설계와 제한 사항은
[BGE-M3 FAISS 설계](docs/vector/BGE_M3_FAISS_DESIGN.md), 실행과 평가 정책은
[검색 평가 v2](docs/vector/VECTOR_RETRIEVAL_BENCHMARK_V2.md)를 참고하세요.

```bash
source .venv/bin/activate
python -m src.filing_pipeline_v2
python -m src.retrieval.bge_m3_indexer --batch-size 16
python -m src.quality.bge_m3_faiss_validation
python -m src.quality.vector_retrieval_goldset_v2
python -m src.quality.vector_retrieval_benchmark_v2
# 원문 대조 검수 정답셋으로 재평가 (결과는 *_reviewed 파일로 별도 저장)
python -m src.quality.vector_retrieval_goldset_v2_reviewed
python -m src.quality.vector_retrieval_benchmark_v2 --goldset data/processed/quality/vector_retrieval_goldset_v2_reviewed.json --tag reviewed
```

이미 인덱스 생성 작업이 실행 중이면 다른 터미널에서
`python -m src.quality.vector_v2_followup`을 실행해 완료 후 무결성 검사와 검색 비교를
연결할 수 있습니다. 중복 인덱스 빌드를 동시에 실행하지 마세요. 자동 초안 평가셋은
보존만 하고, 판단은 원문 대조 검수 정답셋(50문항·13개 유형) 결과로 합니다. 검수 결과
BGE-M3 하이브리드는 Hit@5 0.66(당시 현행 0.64)이지만 Hit@1·MRR이 당시 현행보다 낮아
과거 평가 판단은 `NEEDS_TUNING`이었습니다. 이 수치와 판단은 보존합니다. 이후 사용자
운영 결정으로 2026-10-06부터 Streamlit과 분석기의 기본값은 **BGE-M3 Pure Dense**이며,
Feature Hashing Hybrid는 명시적 legacy/rollback 방식으로 유지됩니다.
