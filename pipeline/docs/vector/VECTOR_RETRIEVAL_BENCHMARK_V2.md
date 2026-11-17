# 검색 평가 v2 실행과 현재 상태

> 운영 상태 알림(2026-10-06): 이 문서의 지표와 `NEEDS_TUNING` 판단은 당시 평가 기록으로
> 보존한다. 이후 사용자의 명시적 운영 결정으로 기본 검색은 `bge_m3_dense`로 승격되었다.
> 현재 설정과 rollback 절차는 `BGE_M3_DENSE_PROMOTION.md`를 참고한다.

> 이 문서는 검색 지표 중심 평가 기록이다. 최종 답변 품질에 따른 후속 결정은
> `EMBEDDING_MODEL_E2E_BENCHMARK.md`를 함께 확인한다.

## 기존 비교의 한계

`src/quality/vector_comparison.py`는 삼성전자 2025 FY에 대해 5개 질문만 평가한다. 예상 제목 용어가 있으면 likely_relevant_section, 없으면 manual_review이며 실제 정답 chunk_id나 본문 정답을 검증하지 않는다. 이를 검색 정확도나 Hit@k로 해석할 수 없다.

## 평가셋 및 정답 판정

신규 정답셋은 `data/processed/quality/vector_retrieval_goldset_v2.json`에 50개 초안으로 생성했다. 5개 기업 각 10개, 연도 2023/2024/2025, 보고서 FY/H1/Q1/Q3를 포함한다. 본문에서 근거 구절을 선택하고 동일 구절을 포함한 기존/v2 청크 ID를 허용 집합에 연결한다. 50개 모두 기존 ID 연결이 있다. 평가 시 제목이나 키워드만 맞는 결과는 정답으로 인정하지 않는다. 기업·연도·보고서·접수번호가 일치하고 expected/acceptable ID 안에 있어야 정답이다.

초안은 원문을 검토하기 위한 후보이며 정답 확정이 아니다. 광범위한 키워드만으로 자동 생성한 근거가 질문에 충분히 답하지 않는 경우가 있다. 현재 10개 유형만 포함되어 제품·서비스/현금흐름/재무구조 범위를 더 보완해야 한다. 원문을 확인해 ID와 근거를 검토한 뒤 review_status를 reviewed로 변경한다. 미검토 초안으로 운영 승격할 수 없다. manual_review는 정답 실패를 뜻하는 별도 판정값으로 사용하지 않는다.

## 비교 및 승격

feature_hashing_hybrid, multilingual_e5_dense, multilingual_e5_hybrid, bge_m3_dense, bge_m3_hybrid를 동일 질문/필터/Top 5로 실행한다. 모델 로딩을 포함한 최초 호출 시간은 별도로 기록하고 이후 평균/P95를 측정한다. 인덱스 크기와 프로세스 최대 RSS도 기록하지만 RSS는 같은 프로세스 누적 최대값이므로 모델별 독립 메모리 소비로 해석하지 않는다. 기존 E5 캐시의 복사 손상으로 별도 e5_v2 캐시를 사용하며 revision은 기존 캐시 참조 `614241f622f53c4eeff9890bdc4f31cfecc418b3`로 고정한다. 기존 E5 DB에는 revision 기록이 없으므로 생성 당시 가중치와의 완전한 일치 보장은 제한된다.

승격은 Hit@5 비열화, Hit@1 또는 MRR 최소 0.02 개선, 기업/연도/보고서 혼입 0, 접수번호 일치율 1, 검증 PASS, 최소 30개 검토 완료 정답과 기업/연도/보고서 범위 충족, P95 1,000ms 이하를 모두 요구한다. 0.02와 1,000ms는 명시적 초기 정책이며 서비스 요구에 따라 조정해야 한다. 승격 문턱을 넘기 전 기본값은 Feature hashing이다. UI의 BGE 선택지도 산출물 검증 PASS 뒤에 활성화한다.

전체 21,797개 임베딩과 FAISS/SQLite 무결성 검증을 완료했다. 초안 정답셋 50개 기준 결과는 다음과 같다.

| 방식 | Hit@1 | Hit@3 | Hit@5 | MRR | 평균/P95 지연(ms) |
|---|---:|---:|---:|---:|---:|
| Feature hashing hybrid | 0.44 | 0.56 | 0.64 | 0.5113 | 12.85 / 28.61 |
| Multilingual E5 dense | 0.26 | 0.36 | 0.42 | 0.3107 | 24.06 / 49.14 |
| Multilingual E5 hybrid | 0.42 | 0.62 | 0.64 | 0.5073 | 19.48 / 32.23 |
| BGE-M3 dense | 0.22 | 0.44 | 0.50 | 0.3207 | 38.69 / 58.31 |
| BGE-M3 hybrid | 0.38 | 0.54 | 0.58 | 0.4590 | 37.36 / 53.95 |

모든 방식에서 기업·연도·보고서 유형 혼입은 0건이고 접수번호 일치율은 1.0이다. BGE-M3 hybrid의 Hit@5와 MRR이 현행보다 낮아 판단은 `KEEP_CURRENT_DEFAULT`이다. 이 수치는 자동 생성한 초안 정답셋 결과이므로 원문 검토 후 다시 확정해야 한다.

## 검수 정답셋 재평가 (최종)

### 초안 정답셋 검토 결과

초안 50문항을 원문과 대조한 결과 근거가 질문에 답하지 않는 문항이 약 절반이었다. 예: V2-003(실적 변화 원인 → 손상검사용 현금흐름 추정), V2-011(반도체 업황 → 원재료 설명), V2-021~025(CAPEX → 특수관계자 거래·현금성자산 정의·투자부동산 표), V2-036/040(생산능력 → PCB 원가·원재료 매입표). 주제 단어가 본문 어디에든 등장하면 정답으로 고른 결과이며, 키워드가 강한 방식에 유리한 편향도 있다. 초안 결과 파일은 보존하고 판단 근거로는 사용하지 않는다.

### 검수 규칙

`src/quality/vector_retrieval_goldset_v2_reviewed.py` → `data/processed/quality/vector_retrieval_goldset_v2_reviewed.json`

1. 주제별로 DART 표준 목차의 해당 소제목 안에서만 근거를 찾는다. 실적 변화 원인·현금흐름·재무구조는 IV 경영진단에서 찾는다. 분기·반기보고서에는 IV가 없으므로 이 세 유형은 FY 보고서에만 배정한다.
2. 소제목 본문에서 질문에 직접 답하는 문장(수치·원인·대상)을 근거 구절로 코드에 고정한다. 기업·연도·보고서·근거 구절을 `SPEC`에서 사람이 그대로 읽고 원문과 대조할 수 있다.
3. 근거 구절(공백 무시)을 포함하는 청크를 v2·기존·E5 DB 전체에서 찾아 허용 ID로 연결한다. 청크 ID 체계·크기가 달라도 같은 근거를 담으면 동일하게 정답이다. 허용 청크는 문항당 대부분 1~3개로 좁다.
4. 질문은 주제별 고정 템플릿이며 어떤 검색 결과도 질문·정답 선정에 사용하지 않았다.

범위: 50문항, 13개 유형 전부(실적 5, 현금흐름 4, 재무구조 4, 사업 전략 3, 반도체 업황 4, 설비투자 4, CAPEX 3, 연구개발 4, 수주 4, 생산능력 3, 시장 위험 4, 원재료 4, 제품·서비스 4), 5개 기업, 2023~2025, FY 18·Q1 10·H1 11·Q3 11.

검수는 AI 보조로 원문 대조 후 `review_status=reviewed`로 기록했다(`review_method=rule+source_text_check`). 운영 승격 전 담당자가 `SPEC`의 50개 근거 구절을 한 번 확인하는 것을 권장한다.

**기존 인덱스 커버리지 결손**: V2R-024(원익IPS 설비 신설 계획), V2R-025/026(SK하이닉스 투자집행 현황)의 근거 문장은 기존 `filings.db`·`filings_e5.db`에 존재하지 않는다. 기존 청킹이 숫자·표 중심 청크를 품질 필터로 제외하면서 같은 청크 안의 서술 문장도 함께 빠졌다. v2는 이를 삭제하지 않고 낮은 우선순위로 보존한다. 이 문항은 유지하고 `legacy_coverage=false`로 표시했으며, 세 인덱스 모두 근거가 있는 47문항 지표를 별도로 산출한다.

### 결과

`python -m src.quality.vector_retrieval_benchmark_v2 --goldset data/processed/quality/vector_retrieval_goldset_v2_reviewed.json --tag reviewed`

| 방식 | Hit@1 | Hit@3 | Hit@5 | MRR | 공통 47문항 Hit@1 / Hit@5 / MRR | 평균/P95(ms) |
|---|---:|---:|---:|---:|---:|---:|
| Feature hashing hybrid (현행) | **0.50** | **0.62** | 0.64 | **0.554** | **0.532** / **0.681** / **0.589** | 14.9 / 42.9 |
| Multilingual E5 dense | 0.24 | 0.36 | 0.38 | 0.301 | 0.255 / 0.404 / 0.320 | 29.6 / 62.6 |
| Multilingual E5 hybrid | 0.48 | 0.60 | 0.62 | 0.537 | 0.511 / 0.660 / 0.572 | 26.0 / 43.2 |
| BGE-M3 dense | 0.30 | 0.56 | 0.56 | 0.417 | 0.319 / 0.596 / 0.443 | 97.1 / 195.4 |
| BGE-M3 hybrid (0.6/0.4) | 0.44 | **0.62** | **0.66** | 0.526 | 0.468 / **0.681** / 0.552 | 96.1 / 213.5 |

전 방식 기업·연도·보고서 혼입 0건, 접수번호 일치율 1.0. 지연은 GPU(RTX 3060) 질의 임베딩 기준이며 초안 실행과 측정 환경이 달라 직접 비교하지 않는다. P95 213ms는 정책 예산 1,000ms 안이다.

유형별 Top 5 적중(문항 수 대비):

| 유형 | 현행 | E5 hybrid | BGE dense | BGE hybrid |
|---|---:|---:|---:|---:|
| 실적 변화 원인 (5) | 1 | 0 | 1 | 1 |
| 현금흐름 (4) | 0 | 0 | 1 | 0 |
| 재무구조 (4) | 3 | 3 | 0 | 2 |
| 사업 전략 (3) | 1 | 1 | 2 | 1 |
| 반도체 업황 (4) | 1 | 1 | 2 | 2 |
| 설비투자 (4) | 3 | 3 | 3 | 4 |
| CAPEX (3) | 0 | 0 | 0 | 0 |
| 연구개발 (4) | 4 | 4 | 2 | 4 |
| 수주·시장 위험·원재료·제품 (각 4) | 16 | 16 | 15 | 16 |
| 생산능력 (3) | 3 | 3 | 2 | 3 |

### 해석

- 초안 기준(BGE hybrid Hit@5 0.58 < 현행 0.64)과 달리 검수 정답셋에서는 BGE hybrid Hit@5가 현행 이상이다(0.66 vs 0.64, 공통 문항 동률). 차이 2문항 중 1문항(V2R-024)은 기존 인덱스에 근거가 없는 문항이다.
- Hit@1(−0.06)과 MRR(−0.028)은 여전히 현행보다 낮다. 50문항에서 Hit@1 0.06은 3문항이므로 통계적으로 확정적인 차이는 아니지만 "의미 있는 개선" 조건은 충족하지 않는다.
- 공통 실패 10문항은 모델보다 질의 표현 문제다. "현금흐름/CAPEX/실적" 같은 질의 단어가 III장 재무제표 표 제목(현금흐름표, 자금조달 실적)과 겹쳐 dense·lexical 모두 표 청크를 상위에 올리고 IV 경영진단 서술이나 "투자집행 현황"을 놓친다. CAPEX는 영문 약어라 국문 공시 "시설투자/투자집행"과 연결되지 않는다.
- BGE dense는 업황·전략·현금흐름처럼 서술형 질문에서 강하고, 수치 비율(재무구조)과 정형 문구(연구개발 조직)에서 약하다. 하이브리드가 이를 보완하지만 현재 0.6/0.4 비율에서는 키워드 신호가 충분하지 않다.

### 최종 판단: `NEEDS_TUNING`

승격 조건 중 Hit@5 비열화·혼입 0·접수번호 추적·FAISS/SQLite 무결성·지연 조건은 충족하고, Hit@1 또는 MRR 0.02 이상 개선 조건을 충족하지 못했다. Streamlit 기본값은 Feature hashing 하이브리드로 유지하고 BGE-M3는 선택지로만 제공한다.

튜닝 후보(정답셋 과적합을 피하기 위해 이 50문항과 분리한 개발용 질문으로 조정한 뒤 이 정답셋으로 재평가):

1. 질의 확장 사전: CAPEX→시설투자·투자집행·유형자산 취득, 실적→매출액·영업이익 증감 등 국문 공시 용어 매핑.
2. 질문 유형별 섹션 사전확률: 실적·현금흐름·재무구조 질문은 IV 경영진단, 재무제표 표(financial_statement) 청크는 감점.
3. dense/lexical 가중치 탐색(예: 0.4~0.6), Reciprocal Rank Fusion 비교.
4. BGE-M3 sparse/ColBERT 출력 활용 검토(현재 dense만 사용).

## 실행 명령

```bash
source .venv/bin/activate
python -m src.filing_pipeline_v2
python -m src.retrieval.bge_m3_indexer --batch-size 16
python -m unittest tests.test_bge_m3_pipeline tests.test_vector_v2_policy -v
python -m src.quality.bge_m3_faiss_validation
python -m src.quality.vector_retrieval_goldset_v2
python -m src.quality.vector_retrieval_benchmark_v2
python -m src.quality.vector_retrieval_goldset_v2_reviewed
python -m src.quality.vector_retrieval_benchmark_v2 --goldset data/processed/quality/vector_retrieval_goldset_v2_reviewed.json --tag reviewed
python -m unittest tests.test_vector_goldset_reviewed -v
streamlit run app/app.py
```

초기 모델 다운로드 뒤 `HF_HUB_OFFLINE=1`을 붙이면 로컬 캐시만 사용한다. 청킹은 기존 파일 내용이 같으면 그대로 보존하고 다르면 실패한다. goldset, validation, benchmark는 기존 결과가 있으면 실패하며 덮어쓰지 않는다. 다른 평가 실행은 새 경로/실행 디렉터리를 사용하도록 구성해야 한다. 운영 vector_pipeline이나 E5 DB 빌더를 실행하지 않는다.

GPU 호환 설치 및 재개(현재 드라이버 기준):

```bash
python -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cu118
HF_HUB_OFFLINE=1 BGE_M3_DEVICE=cuda python -m src.retrieval.bge_m3_indexer --batch-size 4
HF_HUB_OFFLINE=1 BGE_M3_DEVICE=cuda python -m src.quality.vector_v2_followup
```

GPU 접근이 제한된 샌드박스에서는 실행 권한이 필요하다. requirements.txt는 PyTorch 버전을 고정하지만 CUDA wheel 선택은 위 공식 index-url 설치 명령으로 지정한다.

## 자동 검증

`tests/test_bge_m3_pipeline.py`는 임시 SQLite와 실제 FAISS에 결정적 가짜 임베딩을 넣어 1,024차원·정규화·NaN/0벡터 거부·저장/재로딩·전체 ID 일치·행수·기업/코드/연도/보고서/접수번호 필터·본문 연결·재실행 미재계산을 검증한다. 정책 테스트는 명시 ID 정답 판정, 혼입 방지, 미검토/미검증 승격 차단, SQLite Row 키워드 처리를 검증한다. 7개 통과는 실제 BGE 전체 인덱스 검증 통과를 의미하지 않는다. 실제 모델 한 문장에서는 정규화된 (1,1024) 출력과 고정 revision을 확인했다.
---

## 검색 지표 요약

작성 기준일: 2026-10-05

## 평가 범위

- 질문: 50개
- 기업: 삼성전자, SK하이닉스, 한미반도체, 주성엔지니어링, 원익IPS
- 기간: 2023~2025년
- 보고서: Q1, H1, Q3, FY
- 질문 유형: 13종
- 정답: 실제 청크 본문과 `rcept_no`를 확인한 정답·허용 청크 집합

검토된 정답셋은
`data/processed/quality/vector_retrieval_goldset_v2_reviewed.json`에 보존한다. 초안도
별도 파일로 유지한다. 기존 청크에 대응 항목이 없는 질문 3개는 전체 평가에는 포함하고,
동일 커버리지 47개 비교도 별도로 계산한다.

## 전체 50개 결과

| 검색 방식 | Hit@1 | Hit@3 | Hit@5 | MRR | 평균 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Feature hashing hybrid | 0.50 | 0.62 | 0.64 | 0.5540 | 14.94 | 42.90 |
| Multilingual E5 dense | 0.24 | 0.36 | 0.38 | 0.3007 | 29.60 | 62.63 |
| Multilingual E5 hybrid | 0.48 | 0.60 | 0.62 | 0.5373 | 26.03 | 43.17 |
| BGE-M3 dense | 0.30 | 0.56 | 0.56 | 0.4167 | 97.06 | 195.41 |
| BGE-M3 hybrid | 0.44 | 0.62 | 0.66 | 0.5257 | 96.09 | 213.53 |

모든 방식에서 기업, 연도, 보고서 유형 혼입은 0건이고 `rcept_no` 일치율은 100%였다.

## 동일 커버리지 47개 결과

| 검색 방식 | Hit@1 | Hit@3 | Hit@5 | MRR |
|---|---:|---:|---:|---:|
| Feature hashing hybrid | 0.5319 | 0.6596 | 0.6809 | 0.5894 |
| Multilingual E5 dense | 0.2553 | 0.3830 | 0.4043 | 0.3199 |
| Multilingual E5 hybrid | 0.5106 | 0.6383 | 0.6596 | 0.5716 |
| BGE-M3 dense | 0.3191 | 0.5957 | 0.5957 | 0.4433 |
| BGE-M3 hybrid | 0.4681 | 0.6383 | 0.6809 | 0.5521 |

## 승격 판단

최종 판단은 `NEEDS_TUNING`이다.

- BGE-M3 hybrid의 전체 Hit@5는 0.66으로 기존 0.64보다 낮지 않다.
- 기업·연도·보고서 혼입은 모두 0건이다.
- SQLite와 FAISS ID 불일치는 0건이다.
- P95 213.53ms로 Windows 벤치마크의 서비스 허용 기준 1초 이내다.
- 그러나 Hit@1은 0.44로 기존 0.50보다 낮다.
- MRR은 0.5257로 기존 0.5540보다 낮다.
- 요구한 Hit@1 또는 MRR의 유의미한 개선 조건을 충족하지 못했다.

따라서 Streamlit 기본값은 `Feature hashing 하이브리드`로 유지한다. BGE-M3 검색은
선택 옵션으로만 제공한다.

## 방식별 해석

- 기존 feature hashing hybrid는 키워드가 명확한 질문에서 빠르고 첫 결과가 강하다.
- BGE-M3 dense는 표현이 다른 의미 검색에 유리하지만 키워드가 중요한 금융 공시 질문의
  첫 순위가 약해질 수 있다.
- BGE-M3 hybrid는 Hit@5를 개선했지만 현재 60:40 가중치에서는 Hit@1과 MRR이 기존보다
  낮다.
- E5 hybrid는 E5 dense보다 크게 개선되지만 기존 방식과 BGE-M3 hybrid를 넘지 못했다.

## 다음 튜닝 후보

1. `CAPEX → 시설투자·설비투자·투자집행` 같은 금융 질의 확장 사전
2. 질문 유형별 section/subsection 사전확률
3. Dense/키워드 가중치 또는 RRF 비교
4. BGE-M3 sparse 출력 추가 비교
5. 자연어 질문과 프롬프트형 질문을 분리한 평가

관련 결과:

```text
data/processed/quality/vector_retrieval_benchmark_v2_reviewed.json
data/processed/quality/vector_retrieval_benchmark_v2_reviewed.csv
data/processed/quality/vector_retrieval_benchmark_summary_v2_reviewed.csv
data/processed/quality/vector_v2_final_report.json
data/processed/quality/bge_m3_faiss_test_report.json
```
