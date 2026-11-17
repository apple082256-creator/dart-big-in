# KRX 지수 연결 계층

## 결정

기업 데이터는 계속 `corp_code`를 기준으로 한 번만 저장한다. KRX 지수별 DB,
Vector DB, FAISS shard는 만들지 않는다. 지수는 `krx_index_constituents`를 통해
기업을 묶는 논리 계층이다.

```text
krx_indices
    ↓
krx_index_constituents
    ↓
companies(corp_code)
    ├─ financial_facts
    ├─ capex_facts
    ├─ filings
    └─ chunk_metadata → 통합 BGE-M3 / FAISS
```

이 구조는 한 기업이 여러 지수에 들어갈 수 있는 다대다 관계를 보존하고,
같은 재무·공시·임베딩을 지수마다 복제하는 문제를 방지한다.

## 1차 등록 지수

| 내부 `index_code` | 지수명 | 화면명 | 공식 코드 상태 |
|---|---|---|---|
| `KRX_SEMI` | KRX 반도체 | 반도체 | 확인 필요 |
| `KRX_STEEL` | KRX 철강 | 철강 | 확인 필요 |
| `KRX_AUTO` | KRX 자동차 | 자동차 | 확인 필요 |
| `KRX_HEALTH` | KRX 헬스케어 | 헬스케어 | 확인 필요 |
| `KRX_BANK` | KRX 은행 | 은행·금융지주 | 확인 필요 |

위 코드는 애플리케이션 내부의 안정 식별자다. 공식 KRX 지수 코드를 의미하지
않는다. 공식 코드가 검증되면 `official_index_code`에 별도로 저장한다. 확인되지
않은 공식 코드·구성종목·비중은 추정해 넣지 않는다.

## 테이블

### `krx_indices`

지수명, 화면명, 공식 코드, 원본 기준일과 URL을 관리한다. 공식 구성종목을
적재하기 전에는 `source_as_of`가 `NULL`일 수 있다.

### `krx_index_constituents`

지수와 기업의 연결만 저장한다. 기본키는
`(index_code, corp_code, effective_from)`이다. 현재 구성은 `is_current=1`, 과거
구성은 `effective_to`와 `is_current=0`으로 남긴다. 유효기간은
`[effective_from, effective_to)`로 해석한다.

구성 변경 시 과거 행을 삭제하지 않는다.

1. 빠진 구성종목의 `effective_to`를 새 구성의 시작일로 지정한다.
2. 빠진 행의 `is_current`를 0으로 바꾼다.
3. 새 구성종목을 새 `effective_from`으로 추가한다.
4. 계속 포함된 기업은 원본 기준일과 비중만 갱신한다.

## 공식 자료 매핑

KRX 자료가 제공하는 `stock_code`를 DART `corp_code`에 직접 대입하지 않는다.

```text
KRX stock_code
    ↓ exact match
companies.stock_code
    ↓
companies.corp_code
```

`resolve_constituents_by_stock_code()`는 이 경로만 사용한다. 회사명 문자열은
동일·유사 사명 문제 때문에 fallback 키로 사용하지 않는다. 매핑되지 않은 행은
`stock_code_not_found`로 반환하며 사람이 검토한다.

## 조회 API

```python
from src.db.krx_index_db import get_companies_by_index, get_indices_by_company

current = get_companies_by_index("KRX_SEMI")
historical = get_companies_by_index("KRX_SEMI", as_of="2025-03-01")
reverse = get_indices_by_company("00126380")
```

재무 복수 기업 조회:

```python
from src.retrieval.financial_retriever import get_financial_context_by_corp_codes

rows = get_financial_context_by_corp_codes(
    ["00126380", "00164779"], 2025, "FY", account_name="매출액"
)
```

BGE-M3 Dense 복수 기업 검색:

```python
from src.retrieval.operational_retriever import retrieve_filing_context

result = retrieve_filing_context(
    "설비투자 계획",
    corp_codes=["00126380", "00164779"],
    year=2025,
)
```

BGE와 Legacy retriever 모두 SQLite metadata에서 해당 기업의 청크를 먼저
선택한다. 그 후보에 대해서만 벡터 점수를 계산하므로 전체 검색 후 사후 필터링을
하지 않는다. 기존 `corp_code` 한 개와 `corp_name` 호출도 유지된다.

## 통합 FAISS 정책

지수는 시간이 지나며 구성종목이 바뀌고 기업은 여러 지수에 중복 편입될 수 있다.
지수별 FAISS를 만들면 동일 embedding 복제, 갱신 누락, 지수 정기변경 때 재생성
비용이 생긴다. 따라서 실제 성능 문제가 측정되기 전까지 통합 FAISS와
metadata 선필터 정책을 유지한다.

## 현재 연결 상태

| 내부 지수 | 현재 기업 수 | 기준일 | 비중 상태 |
|---|---:|---|---|
| `KRX_SEMI` | 5 | 2026-10-06 | `NULL` |
| `KRX_HEALTH` | 5 | 2026-10-06 | `NULL` |
| `KRX_STEEL` | 5 | 2026-10-07 | `NULL` |
| `KRX_AUTO` | 5 | 2026-10-07 | `NULL` |
| `KRX_BANK` | 5 | 2026-10-07 | `NULL` |

자동차 구성은 기아, 현대자동차, 현대모비스, 한국타이어앤테크놀로지, HL만도다.
은행·금융지주 구성은 하나금융지주, KB금융, 신한지주, 우리금융지주, 카카오뱅크다.
모든 연결은 `stock_code` exact match로 DART `corp_code`를 검증했다. 현재 내부 그룹과
기준일을 저장한 상태이며 공식 코드·비중·원본 URL은 확인 전까지 추정해 넣지 않는다.

## 확장 절차

1. 정확한 공식 지수명·코드와 구성 기준일을 확정한다.
2. 허용된 KRX 공식 원본에서 종목코드와 비중을 내려받는다.
3. `stock_code`로 DART 기업을 매핑하고 미매핑 행을 검토한다.
4. 해당 기업의 원본 데이터가 없을 때만 수집·가공·임베딩한다.
5. `replace_current_constituents()`로 구성 이력을 반영한다.
6. SQLite/FAISS 무결성 검증 후 MySQL 동기화를 실행한다.

MySQL 기존 설치에는
`infra/mysql/migrations/001_add_krx_index_layer.sql`을 한 번 적용한다. 새 설치는
초기 schema와 seed에 이미 포함되어 있다.
