# BGE-M3 Dense 운영 승격

## 결과

2026-10-06부터 공시 검색 기본값은 `bge_m3_dense`다. 기존
`feature_hashing_hybrid`는 삭제하거나 덮어쓰지 않고 legacy rollback 방식으로 보존한다.
이 승격은 과거 벤치마크의 `NEEDS_TUNING` 판단을 변경한 것이 아니라 사용자의 명시적
운영 결정이다.

| 항목 | 승격 전 | 승격 후 |
|---|---|---|
| 기본 검색 | Feature Hashing Hybrid | BGE-M3 Pure Dense |
| SQLite | `db/vector/filings.db` | `db/vector/filings_bge_m3.db` |
| FAISS | 사용하지 않음 | `db/vector/filings_bge_m3.faiss` |
| 모델 | `local-ko-en-finance-hashing-v1` | `BAAI/bge-m3` |
| 차원 | 1,536 | 1,024 |
| 검색 청크 | 10,622 | 21,797 |

## 운영 범위

- 기업: 삼성전자, SK하이닉스, 한미반도체, 주성엔지니어링, 원익IPS
- 공시: 60건(기업당 12건)
- 기간과 유형: 2023~2025년, Q1/H1/Q3/FY
- 제외: 현대자동차, NAVER, LG화학은 원문/v2 청크만 있으며 이 운영 인덱스에는 없다.

지원 기업 목록은 정상 실행 시 BGE SQLite에서 읽으며, 코드의 기대 목록과 정확히 같은지
검증한다. 불일치하면 BGE 검색을 시작하지 않는다.

## 검색 및 호환성 계약

- 모델: `BAAI/bge-m3`
- revision: `5617a9f61b028005a4858fdac845db406aefb181`
- query encode: Sentence Transformers `encode`, `normalize_embeddings=True`
- 최대 입력: 8,192 tokens
- pooling: 저장된 Sentence Transformers 모델 구성 사용
- 점수: 정규화 벡터의 inner product(코사인 유사도와 동일)
- 운영 모드: lexical 결합과 search priority를 적용하지 않는 Pure Dense
- 필터 순서: SQLite에서 기업/코드/연도/보고서/접수번호 후보를 먼저 제한한 뒤 정확 검색

SQLite는 URI `mode=ro`로 연다. 전체 FAISS 파일도 읽기만 하며 요청마다 다시 저장하지
않는다. 모델과 전체 인덱스 로딩은 프로세스 내에서 캐시한다. 각 검색은 필터된 SQLite
embedding BLOB으로 임시 `IndexFlatIP`를 구성하므로, 필터 이후 결과 부족이 발생하지 않는다.

운영 검색 전 다음을 fail-closed 방식으로 검증한다.

- SQLite와 FAISS 파일 존재 여부
- 모델명, revision, 차원, 정규화, 인덱스 유형 메타데이터
- 검색 가능 SQLite 레코드 21,797개와 FAISS 벡터 21,797개
- embedding BLOB 크기와 행별 모델/revision/차원
- `vector_id`, `chunk_id` 중복
- SQLite ID와 FAISS ID의 양방향 누락/orphan
- 실제 기업 목록과 기대한 5개 기업의 일치

## 설정과 rollback

설정은 `src/retrieval/operational_config.py`에서 중앙 관리한다. 환경변수가 없으면
`bge_m3_dense`를 사용한다.

```bash
FILING_RETRIEVER=bge_m3_dense streamlit run app/app.py
FILING_RETRIEVER=feature_hashing_hybrid streamlit run app/app.py
```

허용값은 위 두 개뿐이다. 알 수 없는 값은 오류로 처리한다. 자동 fallback은 기본적으로
꺼져 있다. 장애 시 자동으로 legacy 결과를 반환해야 하는 운영 환경에서만 다음처럼 켠다.

```bash
FILING_RETRIEVER=bge_m3_dense \
FILING_RETRIEVER_AUTO_FALLBACK=true \
streamlit run app/app.py
```

fallback이 발생하면 분석 결과의 `requested_retrieval_method`,
`effective_retrieval_method`, `retrieval_fallback_reason`과 Streamlit 화면에 실제 방식과
사유를 표시한다.

## 검증 명령

```bash
# 빠른 routing/fallback 및 결정적 fake embedding 테스트
.venv/bin/python -m unittest \
  tests.test_operational_retriever \
  tests.test_bge_m3_pipeline

# 실제 SQLite/FAISS 자산 무결성 테스트
.venv/bin/python -m unittest tests.test_bge_m3_faiss

# 실제 BGE 대표 질의 smoke test
BGE_M3_DEVICE=cpu .venv/bin/python -m src.quality.bge_m3_promotion_smoke
```

전체 재임베딩이나 FAISS 재생성은 승격 과정에 포함하지 않는다.

## 알려진 위험

- 과거 검토 정답셋에서 BGE-M3 Dense는 Feature Hashing보다 Hit@1과 MRR이 낮았다.
- BGE 모델 최초 로딩은 Feature Hashing보다 느리고 메모리 사용량이 크다.
- 넓은 필터는 후보 embedding을 SQLite에서 복원해 임시 정확 인덱스를 만들기 때문에
  데이터 규모가 크게 증가하면 필터별 캐시나 다른 FAISS 필터 전략을 검토해야 한다.
- 현대자동차, NAVER, LG화학은 재인덱싱 전까지 운영 검색 대상이 아니다.
