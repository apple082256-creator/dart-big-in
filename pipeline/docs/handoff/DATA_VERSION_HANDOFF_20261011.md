# 데이터 버전 인계 (2026-10-11)

## 1. 현재 버전

| 축 | 현재 값 | 위치 | 검증 |
|---|---|---|---|
| Financial data version | `2026-10-11-dart25-2026q1h1-v14` | MySQL `data_versions` (`validated=1`, `is_current=1`, 1행) | SQLite↔MySQL 전체 대조 PASS |
| Vector/Embedding version | `bge-m3-2026-10-11-dart25-2026q1h1-v14` | MySQL `embedding_metadata` (`is_current=1`, 1행) | 벡터 170,534개 바이트 체크섬 일치 |
| FAISS version | 같은 `index_version`. 파일 `db/vector/filings_bge_m3.faiss`, SHA-256 `63ebe30e3168a5e2000e39980c453d1077e68e9d4bbbea82e2de5429c100c922` | `embedding_metadata.faiss_sha256` | ntotal 170,534 = SQLite 검색 가능 행, ID 집합 일치 |

임베딩 계약(변경 없음): `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`, 1,024차원, L2 정규화,
`IndexIDMap2(IndexFlatIP)`, 청크 규칙 `bge-m3-token-v2`. 기존 147,720개 벡터는 다시 만들지 않았고(바이트 단위로 동일),
2026 신규 22,814개만 추가했다.

**Financial version과 Embedding version은 독립적이며, 문자열 일치 여부를 정합성 기준으로 사용하면 안 된다.**

- 이번에는 두 문자열이 비슷하지만 우연이다. 재무만 바뀐 v12·v13 때는 인덱스 버전이 `bge-m3-2026-10-09-dart25-h1h3-v11`로 유지됐다.
- 정합성은 실제 자산으로 판정한다.
  - `data_versions.vector_sqlite_sha256` / `faiss_sha256` = 실제 파일 해시
  - `embedding_metadata.faiss_ntotal` = `searchable_vector_count` = `chunk_metadata` 활성 검색 가능 행 수
  - `chunk_metadata.vector_id` 집합 = FAISS ID 집합
- 벡터 캐시 키는 `embedding_metadata.index_version`으로 한다.

## 2. 버전 이력 (MySQL `data_versions`, 최근 3개)

| data_version | validated | is_current | index_version | 비고 |
|---|---|---|---|---|
| `2026-10-11-dart25-2026q1h1-v14` | 1 | **1** | `bge-m3-2026-10-11-dart25-2026q1h1-v14` | 이번 승격 |
| `2026-10-10-healthcare-rnd-docid-v13` | 1 | 0 | `bge-m3-2026-10-09-dart25-h1h3-v11` | current였던 적 없음(이전 문서 일부가 current로 잘못 적음) |
| `2026-10-10-healthcare-rnd-v12` | 1 | 0 | `bge-m3-2026-10-09-dart25-h1h3-v11` | v14 직전 current. 롤백 기준 |

## 3. 현재 데이터 규모

| 항목 | 로컬 SQLite/FAISS | MySQL(활성) | 승격 전 | 2026 Q1/H1 증가 |
|---|---:|---:|---:|---:|
| 기업 | 25 | 25 | 25 | 0 |
| 기간 | 14 | 14 | 12 | +2 (2026 Q1, H1) |
| financial_facts | 75,304 | 75,304 | 63,931 | **+11,373** |
| capex_facts | 549 | 549 | 469 | **+80** |
| KPI (`financial_metric_values`) | 2,665 | 2,665 | 1,677 | **+988** |
| financial_collection_status | 110 | 110 | 60 | +50 |
| account_mapping | 2,174 | 2,174 | 2,114 | +60 |
| 공시(`filings`) | - | 345 | 295 | +50 |
| 원문 문서(`filing_source_documents`) | XML 파일 | 449 | 399 | **+50** |
| 청크 | 170,534 | 170,534 | 147,720 | **+22,814** |
| 임베딩 | 170,534 | 170,534 | 147,720 | **+22,814** |
| 청크→문서 매핑 | - | 170,534 | 147,720 | +22,814 |
| FAISS ntotal | 170,534 | - | 147,720 | +22,814 |
| coverage | - | 350 | 300 | +50 (2026 50/50 PASS) |

2026 증가분 세부:

- 슬롯 50/50(25개사 × Q1·H1). 2026 Q3/FY는 모든 계층에서 0건.
- KPI 988 = 일반 기업(반도체·자동차·철강 15개사) + 헬스케어 일반 KPI 및 R&D 20 + 금융 KPI. 모두 `approved`.
- CAPEX 80 = 일반 20개사 × Q1/H1 × 누적·분기. 은행 0. `review_required` 0, 음수 0.
- 청크 Q1 9,900 / H1 12,914.

## 4. 파일 해시 (승격 후 운영)

| 파일 | SHA-256 | 크기(bytes) |
|---|---|---:|
| `db/financial/financial.db` | `0ed3ac22953aec1cf25bee9890325e3f122d1c9249a0adffdc8007fe84966a92` | 47,337,472 |
| `db/vector/filings_bge_m3.db` | `608bcdad6865710f810b1bb125b4e8e7090674a62a7e0182c39737832f53d6d8` | 1,536,118,784 |
| `db/vector/filings_bge_m3.faiss` | `63ebe30e3168a5e2000e39980c453d1077e68e9d4bbbea82e2de5429c100c922` | 699,871,626 |

세 해시는 MySQL `data_versions`(v14)와 `embedding_metadata`(v14)에 기록된 값과 같다.

## 5. 다음 배포 때 지킬 것

- 재무만 바뀌면 새 `data_version`만 만든다. 인덱스 버전·FAISS는 그대로 둔다(`sync_sqlite_to_mysql.py`가 매 실행마다
  `bge-m3-{data_version}` 인덱스 행을 만들므로, 임베딩이 바뀌지 않았으면 current 인덱스를 기존 값으로 유지할 것).
- 임베딩이 바뀌면 `sync_embeddings_to_mysql.py --data-version …` 후 `embedding_metadata.is_current`를 옮긴다.
- 두 current는 각각 정확히 1개여야 한다.
