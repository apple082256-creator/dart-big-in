# BGE-M3 공시 검색 v2

> 운영 상태 알림(2026-10-06): 검증 당시의 승격 보류 기록은 아래에 그대로 보존한다.
> 이후 사용자의 명시적 결정으로 Pure Dense(`bge_m3_dense`)가 운영 기본값으로 승격됐다.
> Feature Hashing Hybrid는 삭제하지 않고 rollback 방식으로 유지한다. 현재 운영 절차는
> `BGE_M3_DENSE_PROMOTION.md`를 참고한다.

## 보존 및 현황 (2026-10-02)

실제 작업 경로는 `/home/sd1-03/ai_campus/mini_project`이다. 전달된 macOS 경로를 실행 경로로 사용하지 않는다. `.env`, API 키, Financial DB를 읽거나 변경하지 않고 기존 공개 공시 파싱 결과에서 시작했다.

운영 `filings.db`와 비교 `filings_e5.db`는 각각 10,622행이다. 운영 임베딩은 `local-ko-en-finance-hashing-v1`, float32 1,536차원이다. 단어·단어 bigram·문자 2/3/4-gram을 signed hashing하고 L2 정규화한다. 최종 점수는 dense 35% + lexical 65%이다. E5는 `intfloat/multilingual-e5-small`, 384차원이며 passage/query 접두사를 사용한다. 입력 한도는 512토큰이다. 기존 1,800자 청크는 E5에서 잘릴 수 있어 별도 토큰 길이 검사가 필요하다.

기존 청크는 section/subsection 단위로 최대 1,800자, 중복 200자이며 ID는 `rcept_no:section_index:chunk_index`이다. DB에는 기업명·기업코드·연도·보고서·접수번호·섹션·하위섹션·XML 경로·본문·임베딩 모델·차원이 있다. 운영 DB는 추가로 content_hash와 selection_reason을 저장한다. 기존 필터는 기업명/연도/보고서이며, 이번 변경으로 검색 함수에 기업코드/접수번호 선택 인자를 추가하고 DB를 읽기 전용으로 열게 했다. 기존 점수 가중치는 유지한다.

키워드 점수는 질문에서 기업명/연도를 제거하고 한국어·영어·숫자 용어와 금융 핵심 용어를 추출한다. 본문 빈도는 최대 5회, 제목 적중은 2점 가산하며 `log((N+1)/(df+1))+1`을 곱하고 필터 후보 안의 최대값으로 정규화한다. SQLite Row에는 get 메서드가 없으므로 dict로 변환하는 오류 수정이 포함된다.

## v2 저장 구조

- 청크: `data/processed/filings/chunks_v2/`
- 메타데이터 및 재개용 벡터: `db/vector/filings_bge_m3.db`
- 검색 인덱스: `db/vector/filings_bge_m3.faiss`
- 모델 캐시: `db/vector/model_cache/bge_m3/`

[공식 모델 카드](https://huggingface.co/BAAI/bge-m3)에 따라 BGE-M3 Dense 1,024차원, 최대 8,192토큰을 사용한다. 모델은 의미 벡터를 만들고 FAISS는 그 벡터의 유사도를 검색한다. SQLite는 원문·메타데이터·FAISS ID·임베딩 BLOB을 저장한다. 모델 revision은 실제 다운로드한 `5617a9f61b028005a4858fdac845db406aefb181`로 고정했다. sentence-transformers만 사용하며 FlagEmbedding은 추가하지 않는다.

IndexIDMap2(IndexFlatIP)에 명시적 vector_id를 넣는다. float32 벡터의 차원·유한성·0벡터·L2 norm을 검사한다. ID는 SQLite 정수 기본키이며 chunk_id는 UNIQUE이다. 배치별 커밋 후 content_hash/model/revision이 같은 행은 재계산하지 않는다. FAISS는 SQLite에서 재생성하고 임시 파일을 완성한 뒤 교체한다. 중단된 첫 빌드에는 최종 FAISS가 없을 수 있다. 빌드 중인 DB는 서비스에 연결하지 않는다.

## 청킹 및 우선순위

토크나이저 기준 target 600, overlap 100을 시작값으로 사용한다. 문장/줄 경계를 우선하고 section/subsection은 섞지 않는다. ID는 `rcept_no:v2:section_index:chunk_index`이다. 파싱된 original_text와 정제 chunk_text, source_file, 기업·연도·접수번호를 보존한다. 원 XML 자체는 변경하지 않는다. 동일 본문도 섹션별 행을 보존해 출처 관계를 잃지 않는다.

현재 구현은 문장/줄을 합치는 목표 크기이며, 줄 병합 후 다시 토큰화하면 600을 소폭 넘을 수 있다. 실제 대상 5개 기업의 최대는 602토큰이다. overlap은 문장/줄 단위를 보존하기 때문에 정확히 100토큰이 아니며, 단일 긴 줄을 강제 분할할 때 중복이 부족할 수 있다. 엄격한 토큰 상한·긴 줄 중복·청크 설정 서명은 후속 보완 항목이다. 이 제한을 숨기고 엄격한 600/100 보장을 주장하지 않는다.

8개 기업의 기존 파싱 파일 96개에서 41,190개 청크를 생성했다. 인덱싱과 평가에는 운영과 동일한 5개 기업만 사용한다. 해당 기업의 21,797개 가운데 narrative 9,268, financial_statement 3,509, table 3,191, governance 2,734, toc 2,593, short_statement 502개이다. search_priority < 0.7은 9,020개이며 삭제 없이 모두 검색 대상으로 둔다.

## 필터와 하이브리드 검색

SQLite에서 corp_name/corp_code/year/report_type/rcept_no에 대해 정확한 일치 조건으로 허용 ID를 먼저 얻는다. FAISS에서 허용 ID 벡터만 복원한 임시 IndexFlatIP를 검색한다. 전체 Top 5 뒤 필터링하는 방식이 아니므로 후보 부족과 기업/연도 혼입을 막는다. 모든 허용 후보를 점수화하므로 키워드만 강한 후보도 하이브리드 결과에 포함될 수 있다.

하이브리드 시작값은 `0.60*dense + 0.40*lexical`에 우선순위 계수 `0.90+0.10*search_priority`를 곱한다. Dense 모드에도 현재 우선순위 계수가 적용된다. 가중치는 미승인 실험값이다. 결과에 dense_score, lexical_score, final_score, rank, retrieval_method와 원문 출처를 넣는다. 모델 사이의 점수 크기를 비교하지 않는다.

## 확장 및 미완료 위험

100,000개 × 1,024 × float32의 벡터 원본은 약 390.6MiB이다. SQLite BLOB, FAISS 복사본, 필터 후보 복원과 임시 인덱스 때문에 실제 메모리는 더 크다. 허용 후보 전체 복원은 넓은 필터에서 느려질 수 있으므로 실제 측정 후 필터별 인덱스 캐시 또는 FAISS 허용 ID 검색을 검토한다. IVF/HNSW는 아직 도입하지 않는다. CPU 전체 임베딩은 오래 걸리며 배치 커밋으로 재개한다.

이 실행 환경에는 RTX 3060 6GB와 CUDA 12.3 지원 드라이버가 있다. 최초 설치된 torch 2.14.1+cu130은 드라이버와 호환되지 않아 CPU로 실행되었다. GPU 전환을 위해 [PyTorch 공식 배포 목록](https://pytorch.org/get-started/previous-versions/)의 torch 2.7.1 CUDA 11.8 빌드를 사용한다. GPU 접근은 이 환경의 샌드박스 밖 실행이 필요하다. `BGE_M3_DEVICE=cuda`로 요청하면 CUDA 미접근 시 CPU로 전환하지 않고 실패한다. 560개까지 저장된 CPU 임베딩은 같은 모델/revision의 float32 출력으로 유지하고 재개한다.

본문·모델·revision이 바뀌면 재임베딩한다. 현재 비교 키는 본문 hash/model/revision으로 제목만 바뀌는 경우와 청킹 파라미터 변경을 완전히 식별하지 못한다. 청킹 정책을 변경할 때는 새 chunk_version과 별도 산출 경로를 사용해야 한다. 기존 입력에서 사라진 행 정리, 실패 시 빌드 보고서, 데이터셋 지문과 검증 보고서의 결합도 추가 보완이 필요하다. 따라서 현재 기본 검색 승격은 보류한다.
---

## 현재 운영 설계 (2026-10-05)

## 목적

기존 운영 검색을 유지하면서 공시 청크가 약 10만 건으로 증가하는 상황을 대비해
`BAAI/bge-m3` Dense 임베딩, FAISS 정확 검색, 기존 키워드 점수, SQLite 메타데이터
필터를 결합한 별도 v2 검색 구조를 검증한다. 무결성 및 E2E 검증 이후 Pure Dense를
운영 기본값으로 사용한다.

## 기존 운영 구조

- DB: `db/vector/filings.db`
- 모델: `local-ko-en-finance-hashing-v1`
- 차원: 1,536
- 청크: 10,622개
- 점수: feature hashing 35% + 키워드 65%
- 필터: `corp_name/corp_code`, `year`, `report_type`, `rcept_no`
- 청킹: 최대 1,800자, 200자 중복

기존 E5 비교 DB인 `db/vector/filings_e5.db`는 같은 10,622개 청크를
`intfloat/multilingual-e5-small` 384차원으로 저장한다. 기존 비교는 5개 질문의 검색
결과 섹션명에 기대 문자열이 포함됐는지 확인했으므로 실제 정답 청크 기반 평가는
아니었다.

## v2 구조

```text
parsed JSON
  -> 토큰 기반 chunks_v2 JSONL
  -> BGE-M3 Dense 1024차원 정규화 임베딩
  -> SQLite 메타데이터·임베딩 BLOB
  -> FAISS IndexIDMap2(IndexFlatIP)
```

주요 경로:

```text
data/processed/filings/chunks_v2/
db/vector/model_cache/bge_m3/
db/vector/filings_bge_m3.db
db/vector/filings_bge_m3.faiss
```

## BGE-M3와 FAISS 역할

- BGE-M3는 질문과 공시 청크를 의미 벡터로 변환한다.
- FAISS는 정규화된 벡터에 내적을 적용해 코사인 유사도와 같은 정확 검색을 수행한다.
- SQLite는 본문, 출처, 기업, 연도, 보고서, 접수번호와 FAISS `vector_id`를 관리한다.
- Financial DB는 수정하지 않으며 공시 문맥 검색과 재무 숫자 조회의 역할을 분리한다.

모델 사양은 `BAAI/bge-m3`, 1,024차원, 최대 8,192토큰이다. 이번 버전은 Dense 출력만
사용한다. 모델 revision은 `5617a9f61b028005a4858fdac845db406aefb181`로 기록했다.

공식 자료:

- https://huggingface.co/BAAI/bge-m3
- https://github.com/FlagOpen/FlagEmbedding

## 청킹 v2

- 목표 크기: 600토큰
- 중복: 100토큰
- section과 subsection 경계를 넘지 않음
- 줄과 문장 경계를 우선하고 긴 단위만 토큰 경계로 분할
- `original_text`와 `cleaned_text`를 모두 보존
- ID: `{rcept_no}:v2:{section_index}:{chunk_index}`
- `source_file`, 기업, 연도, 보고서, `rcept_no`를 유지

짧은 문장, 표, 목차, 임원·주주총회 내용은 삭제하지 않는다. 다음 유형과 우선순위로
보존한다.

| content_type | search_priority |
|---|---:|
| narrative 핵심 용어 포함 | 1.00 |
| narrative 일반 | 0.70 |
| financial_statement | 0.75 |
| table | 0.40 |
| short_statement | 0.35 |
| governance | 0.30 |
| toc | 0.15 |

전체 v2 청크 파일에는 96개 공시가 들어 있으며, BGE DB에는 기존 운영 범위인 5개
기업·60개 공시의 21,797개 검색 가능 청크만 적재한다.

## 메타데이터 필터

검색은 SQLite에서 기업·연도·보고서·접수번호 조건을 먼저 적용한다. 허용된 행의
임베딩만 임시 `IndexFlatIP`에 넣어 정확 검색한다. 전체 FAISS Top-K를 먼저 구한 뒤
필터링하지 않으므로 필터 때문에 결과 수가 부족해지지 않는다.

macOS의 PyTorch와 `faiss-cpu` OpenMP 런타임 충돌을 피하기 위해 질의 모델을 먼저
초기화하고 FAISS를 나중에 불러오며 검색 스레드는 1개로 제한한다. macOS에서는
SQLite의 필터된 embedding BLOB을 사용해 임시 `IndexFlatIP`를 만든다. 저장된 전체
FAISS 인덱스는 무결성 검증과 Windows/Linux 배포에 그대로 사용한다.

## 검색 점수 모드

Dense/Hybrid와 `search_priority` 적용 여부를 독립적으로 조합한다.

```text
bge_m3_dense
= dense

bge_m3_priority_dense
= dense × (0.90 + 0.10 × search_priority)

bge_m3_hybrid
= 0.60 × dense + 0.40 × keyword

bge_m3_priority_hybrid
= (0.60 × dense + 0.40 × keyword)
  × (0.90 + 0.10 × search_priority)
```

서로 다른 임베딩 모델의 원점수는 직접 비교하지 않는다. 동일 평가 질문에서 각 검색
방식의 순위와 Hit@K, MRR을 비교한다. `search_priority`는 청크를 삭제하지 않고
Priority 모드에서만 최종 점수에 제한적으로 반영한다. 기본 검색은 Pure Dense다.

## 증분 처리와 재임베딩

`chunk_id`, `content_hash`, 모델명, revision이 모두 같으면 재임베딩하지 않는다.
다음 경우 재임베딩이 필요하다.

- 청크 본문 또는 청킹 규칙 변경
- 모델명 또는 revision 변경
- 임베딩 차원·정규화 방식 변경
- 임베딩에 포함하는 제목 형식 변경

배치마다 SQLite에 커밋하므로 중단 후 재실행할 수 있다. NaN, 무한대, 0벡터,
1,024차원 및 L2 정규화를 적재 전에 검사한다.

## 10만 청크 확장

1,024차원 float32 벡터 10만 개는 순수 벡터만 약 391MiB다. 현재 21,797개에서
`IndexFlatIP` 검색 시간이 서비스 기준을 만족하므로 정확 검색을 유지한다. 10만 개에서
실제 P95와 메모리를 다시 측정한 뒤에만 IVF 또는 HNSW를 검토한다. 메타데이터 필터로
기업·기간별 후보가 충분히 줄어들면 Flat 검색을 계속 사용할 수 있다.

## 실행

```bash
source .venv/bin/activate

python -m src.filing_pipeline_v2
python -m src.retrieval.bge_m3_indexer --batch-size 4
python -m src.quality.bge_m3_faiss_validation
python -m src.quality.vector_retrieval_benchmark_v2
streamlit run app/app.py
```

현재 기본 검색은 `bge_m3_dense`이며 priority와 keyword를 사용하지 않는 Pure Dense다.
나머지 세 모드는 Streamlit에서 비교·검증용으로 선택할 수 있다.
