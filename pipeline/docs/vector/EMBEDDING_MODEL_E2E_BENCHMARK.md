# Embedding Model E2E Benchmark

> 운영 상태 알림(2026-10-06): 아래 결과와 권고는 실험 당시 기록이다. 이후 사용자의
> 명시적 운영 결정으로 기본 검색은 `bge_m3_dense`로 승격되었으며 Feature Hashing은
> legacy rollback으로 보존된다. 현재 상태는 `BGE_M3_DENSE_PROMOTION.md`를 참고한다.

## 1. 테스트 목적

실제 DART BIG:IN용 프롬프트와 동일 입력 통제를 적용했을 때 Feature Hashing Hybrid, BGE-M3 Dense, BGE-M3 Hybrid 중 최종 사용자 답변 품질이 가장 높은 방식을 확인했다. 검색 지표 자체가 아니라 검색 청크가 Gemini 최종 답변에 미치는 영향을 측정했다.

## 2. 비교 대상

| 방식 | DB | Query encoder | 차원 | 점수 공식 |
|---|---|---|---:|---|
| Feature Hashing Hybrid | `filings.db` | `local-ko-en-finance-hashing-v1` | 1536 | `0.35 × dense + 0.65 × lexical` |
| BGE-M3 Dense | `filings_bge_m3.db` | `BAAI/bge-m3` revision `5617a9f61b028005a4858fdac845db406aefb181` | 1024 | pure dense similarity |
| BGE-M3 Hybrid | `filings_bge_m3.db` | 같은 BGE-M3 | 1024 | `(0.60 × dense + 0.40 × lexical) × (0.90 + 0.10 × search_priority)` |

lexical score는 쿼리의 한글 2글자 이상·영문 2글자 이상·숫자 토큰을 추출하고, 문서 빈도 기반 가중치와 본문 최대 5회 빈도, 제목 일치 보너스를 합산한 뒤 후보군 최대값으로 0~1 정규화한다.

## 3. 실험 통제 조건

- 산업: 반도체; 메타데이터 필터: `year=2025`, `report_type=FY`, 기업 필터 없음
- Top-K: 5; 신규 청킹·DB 수정 없음
- 후보 모델은 API 쿼터 때문에 test_id별로 고정했다: `{'I01': 'gemini-3-flash-preview', 'I02': 'gemini-3.1-flash-lite', 'I03': 'gemini-3.1-flash-lite-preview', 'I04': 'gemini-3.1-flash-lite-preview', 'I05': 'gemini-3-flash-preview', 'K01': 'gemini-3.1-flash-lite-preview', 'K02': 'gemini-3.1-flash-lite-preview', 'K03': 'gemini-3.1-flash-lite-preview', 'K04': 'gemini-3-flash-preview', 'K05': 'gemini-3-flash-preview'}`. Judge 모델도 test_id별로 `{'I01': 'gemini-3-flash-preview', 'I02': 'gemini-3.1-flash-lite', 'I03': 'gemini-3.1-flash-lite-preview', 'I04': 'gemini-3.1-flash-lite-preview', 'I05': 'gemini-3-flash-preview', 'K01': 'gemini-2.5-flash-lite', 'K02': 'gemini-2.5-flash-lite', 'K03': 'gemini-2.5-flash-lite', 'K04': 'gemini-3-flash-preview', 'K05': 'gemini-3-flash-preview'}`로 고정했다. temperature=0.1; 후보 max tokens는 K01~K03이 1024, 나머지가 4096이며 각 test_id의 세 방식은 동일하다.
- 같은 test_id 안에서 재무 데이터, system/user prompt, 모델, 설정, JSON schema를 동일하게 유지했다.
- `gemini-3.8-flash` 일일 쿼터 소진으로 test_id별 후보 모델을 나눴다. 각 test_id의 세 방식은 반드시 같은 모델을 사용하므로 paired 방식 비교에는 모델 편향이 없지만, 테스트 간 절대 점수를 직접 비교할 때는 모델 차이를 고려해야 한다.
- 유일한 독립변수는 retrieval pipeline과 `retrieved_chunks`다.
- Judge에는 실제 방식명을 숨기고 test_id마다 안정적으로 섞은 Method A/B/C만 제시했다.

## 4. 실제 서비스 프롬프트

프로젝트에는 요청서의 두 기능 전용 프롬프트 정의 파일이 없어 요청서에 명시된 System Prompt 원칙과 User Prompt 문구를 `embedding-model-e2e-request-v1`로 고정해 사용했다. 전체 프롬프트와 JSON schema는 `embedding_model_e2e_outputs.json`에 보존했다.

## 5. 테스트 케이스

산업 인사이트 5개(I01 최근 변화, I02 CAPEX, I03 수익성, I04 영업현금흐름, I05 생산능력/전략)와 키워드 인사이트 5개(K01 HBM, K02 AI, K03 파운드리, K04 설비투자, K05 생산능력), 총 10개다. 모든 키워드는 2025 FY 자산에서 실제 출현을 확인했다.

## 6. 검색 결과 차이

`공통 청크 수`는 각 Top-5의 chunk_id 교집합이다.

| test_id | FH↔BGE Dense 공통 | BGE Dense↔Hybrid 공통 | FH 기업 | BGE Dense 기업 | BGE Hybrid 기업 |
|---|---:|---:|---|---|---|
| I01 | 0/5 | 3/5 | SK하이닉스, 원익IPS, 한미반도체 | SK하이닉스, 주성엔지니어링, 원익IPS | SK하이닉스, 원익IPS, 주성엔지니어링, 한미반도체 |
| I02 | 0/5 | 2/5 | 주성엔지니어링, 한미반도체, 원익IPS, SK하이닉스 | 주성엔지니어링, 원익IPS, SK하이닉스 | 원익IPS, 주성엔지니어링, 한미반도체, 삼성전자 |
| I03 | 0/5 | 1/5 | 주성엔지니어링, 원익IPS, 한미반도체 | 원익IPS, 한미반도체, 주성엔지니어링 | 주성엔지니어링 |
| I04 | 0/5 | 1/5 | 원익IPS, 주성엔지니어링 | 원익IPS, 주성엔지니어링 | 원익IPS, 한미반도체, 주성엔지니어링 |
| I05 | 0/5 | 0/5 | SK하이닉스, 원익IPS, 주성엔지니어링 | SK하이닉스, 주성엔지니어링, 원익IPS | SK하이닉스, 원익IPS, 삼성전자 |
| K01 | 0/5 | 3/5 | 한미반도체, SK하이닉스 | 한미반도체, SK하이닉스 | 한미반도체 |
| K02 | 0/5 | 2/5 | 한미반도체, 삼성전자, SK하이닉스 | 한미반도체, SK하이닉스, 주성엔지니어링 | 한미반도체, SK하이닉스 |
| K03 | 0/5 | 0/5 | SK하이닉스, 주성엔지니어링 | 주성엔지니어링, 한미반도체, SK하이닉스 | SK하이닉스, 주성엔지니어링 |
| K04 | 0/5 | 2/5 | SK하이닉스, 원익IPS | 주성엔지니어링, SK하이닉스 | SK하이닉스, 삼성전자 |
| K05 | 0/5 | 2/5 | SK하이닉스, 원익IPS, 주성엔지니어링 | 주성엔지니어링, SK하이닉스, 원익IPS, 한미반도체 | SK하이닉스, 원익IPS, 주성엔지니어링 |

서로 다른 DB/청크 버전을 쓰는 Feature Hashing과 BGE의 chunk_id 직접 일치는 제한적일 수 있다. Dense↔Hybrid 차이는 동일 BGE 자산에서 lexical 결합이 상위 근거의 기업·section 구성을 얼마나 바꾸는지 보여준다. 개별 rank, section, 본문, 점수는 `embedding_model_retrieval_results.csv`에 있다.

주요 정성 차이는 다음과 같다.

- I03(수익성)에서 BGE Hybrid의 Top-5는 모두 주성엔지니어링에 집중됐다. 반면 BGE Dense는 원익IPS·한미반도체·주성엔지니어링의 재무/경영진단 섹션을 함께 찾아 산업 종합에 유리했다.
- I04(영업현금흐름)에서 BGE Hybrid는 원익IPS·한미반도체·주성엔지니어링의 사업 내용을 함께 확보해 현금흐름 변화와 사업 요인의 연결이 가장 명확했다.
- K03(파운드리)에서 Feature Hashing은 SK하이닉스 재무 섹션을 상위에 올려 관계기업·합작 맥락과 재무 영향을 연결했다. BGE 두 방식은 장비/일반 사업 문맥 비중이 커 공시 및 재무 grounding이 약했다.
- K04(설비투자)에서 BGE Dense는 주성엔지니어링의 구체적 시설투자 계획과 장비시장 전망을 찾았다. Feature Hashing은 일부 상위 근거가 유형자산 CAPEX가 아닌 관계기업·종속기업 지분투자에 치우쳤다.
- K05(생산능력)에서 세 방식 모두 관련 사업 섹션을 찾았지만, BGE Hybrid 답변은 검색된 원익IPS·주성엔지니어링 근거를 충분히 활용하지 못해 한 기업 사례에 치우쳤다.

## 7. LLM 출력 차이

| test_id | 주제 | Feature Hashing | BGE Dense | BGE Hybrid | 최고 방식 |
|---|---|---:|---:|---:|---|
| I01 | 산업 전반 최근 변화 | 5.00 | 4.89 | 4.56 | feature_hashing_hybrid |
| I02 | 설비투자 / CAPEX 변화 | 3.78 | 3.67 | 5.00 | bge_m3_hybrid |
| I03 | 수익성 변화 | 3.78 | 4.78 | 3.78 | bge_m3_dense |
| I04 | 영업활동현금흐름 변화 | 4.11 | 4.00 | 5.00 | bge_m3_hybrid |
| I05 | 생산능력 / 사업전략 변화 | 4.44 | 4.78 | 5.00 | bge_m3_hybrid |
| K01 | HBM | 5.00 | 5.00 | 5.00 | feature_hashing_hybrid |
| K02 | AI | 3.89 | 4.56 | 3.89 | bge_m3_dense |
| K03 | 파운드리 | 4.22 | 2.89 | 2.89 | feature_hashing_hybrid |
| K04 | 설비투자 | 4.44 | 5.00 | 4.78 | bge_m3_dense |
| K05 | 생산능력 | 5.00 | 5.00 | 4.11 | feature_hashing_hybrid |

실제 raw/parsed 응답, 재무 입력, retrieved chunks, 자동 검증 결과는 `embedding_model_e2e_outputs.json`에 보존했다.

산업 인사이트에서는 BGE Hybrid가 I02·I04·I05에서 1위였고 평균도 4.667로 가장 높았다. 키워드 인사이트에서는 Feature Hashing이 K03에서 BGE 두 방식보다 크게 앞섰고 K01·K05에서도 공동 1위여서 평균 4.511로 가장 높았다.

## 8. 기능별 평균 점수

| 기능 | Feature Hashing Hybrid | BGE-M3 Dense | BGE-M3 Hybrid | 최고 방식 |
|---|---:|---:|---:|---|
| AI 산업 인사이트 | 4.222 | 4.422 | 4.667 | bge_m3_hybrid |
| 공시 키워드 인사이트 | 4.511 | 4.489 | 4.133 | feature_hashing_hybrid |
| 전체 | 4.367 | 4.456 | 4.400 | bge_m3_dense |

## 9. Feature Hashing vs BGE-M3 Dense

전체 평균은 Feature Hashing 4.367, BGE Dense 4.456다. 따라서 이 E2E 표본에서 BGE-M3 임베딩 자체의 효과는 `+0.089`점이며, 최종 답변 품질 기준으로 개선이다.

다만 효과는 기능별로 달랐다. 산업 인사이트는 `+0.200`점(4.222→4.422) 개선됐지만 키워드 인사이트는 `-0.022`점(4.511→4.489)으로 사실상 동률이면서 소폭 낮았다.

## 10. BGE-M3 Dense vs Hybrid

전체 평균은 BGE Dense 4.456, BGE Hybrid 4.400다. 동일 임베딩에서 keyword 결합 효과는 `-0.056`점으로, 개선으로 확인되지 않음이다.

이 효과도 기능별 방향이 반대였다. 산업 인사이트는 `+0.245`점(4.422→4.667) 개선됐으나 키워드 인사이트는 `-0.356`점(4.489→4.133) 하락했다. 따라서 keyword 결합을 모든 기능에 일괄 적용하는 것은 권장하지 않는다.

## 11. 기존 Hit@k/MRR과 실제 서비스 품질 관계

| 방식 | Hit@1 | Hit@5 | MRR | E2E 평균 |
|---|---:|---:|---:|---:|
| Feature Hashing Hybrid | 0.50 | 0.64 | 0.5540 | 4.367 |
| BGE-M3 Dense | 0.30 | 0.56 | 0.4167 | 4.456 |
| BGE-M3 Hybrid | 0.44 | 0.66 | 0.5257 | 4.400 |

네 가지 핵심 질문의 답은 다음과 같다.

1. 기존 검색 지표 순위와 E2E 순위는 같지 않다. Hit@1/MRR 1위는 Feature Hashing, Hit@5 1위는 BGE Hybrid인 반면 전체 E2E 1위는 검색 지표가 가장 낮았던 BGE Dense다.
2. BGE Hybrid의 높은 Hit@5는 산업 인사이트 품질 개선으로 이어졌다. 산업 평균 4.667로 1위였지만, 키워드 평균은 4.133으로 최하위여서 전체 서비스 품질 개선으로 일반화할 수 없다.
3. BGE Dense의 낮은 검색 지표는 답변 품질 약점으로 그대로 나타나지 않았다. 전체 E2E 평균 4.456으로 1위였고 Feature Hashing보다 0.089점 높았다.
4. Feature Hashing의 높은 Hit@1/MRR은 짧은 Top-5 RAG에서 특히 키워드 기능의 장점으로 나타났다. 키워드 평균 4.511로 1위였지만 산업 평균은 4.222로 최하위였다.

## 12. 자동 검증과 환각

| 방식 | Judge 환각 플래그(10건 중) | 자동 REVIEW(10건 중) |
|---|---:|---:|
| Feature Hashing Hybrid | 0 | 2 |
| BGE-M3 Dense | 0 | 2 |
| BGE-M3 Hybrid | 0 | 3 |

자동 검증은 JSON 파싱, 문장 수, 금지 표현, 입력에 없는 숫자, 기업명 혼입, rcept_no 일치, 길이를 검사했다. REVIEW는 실패 확정이 아니라 수동 확인 대상이다.

- JSON 파싱은 30/30 성공했고 모든 후보 응답의 `finish_reason`은 `STOP`이었다.
- Judge가 확인한 환각은 0건이었다.
- 엄격한 숫자 검증은 7건을 REVIEW로 표시했다. 주된 원인은 입력의 원 단위 값을 조원 단위로 반올림하거나 증감률을 계산해 새 숫자로 표현한 경우다. 계산상 맞더라도 “입력에 없는 숫자 금지”를 엄격 적용하면 출력 정책 위반 위험이므로 운영 프롬프트에서 계산·반올림 허용 여부를 명시해야 한다.
- K05의 Feature Hashing과 BGE Dense 답변은 1~2문장 요구를 넘어 3문장이어서 길이 규칙 REVIEW에도 해당한다.

## 13. 최종 추천

- AI 산업 인사이트: **PROMOTE_BGE_HYBRID**
- 공시 키워드 인사이트: **KEEP_FEATURE_HASHING**
- 전체 서비스: **USE_DIFFERENT_METHOD_BY_FEATURE**

현재 Streamlit 기본 검색기는 `feature_hashing`이며 UI에서도 첫 선택지다. 본 10개 통제 실험 기준으로 전체 결정이 `KEEP_FEATURE_HASHING`이면 유지하고, `PROMOTE_*`면 해당 방식으로 변경하며, `USE_DIFFERENT_METHOD_BY_FEATURE`이면 기능별 라우팅을 도입하는 것이 권고다. 표본이 10개이므로 운영 전에는 반복 호출 또는 사람 평가로 변동성을 재확인하는 것이 안전하다.

따라서 현재 Streamlit의 **단일 기본 검색기를 BGE 방식 하나로 즉시 교체하지 않는다**. 기능별 라우팅을 추가해 산업 인사이트에는 BGE-M3 Hybrid, 키워드 인사이트에는 Feature Hashing Hybrid를 사용하는 것이 1차 권고다. 단일 방식만 선택해야 한다면 전체 평균이 가장 높은 BGE-M3 Dense가 후보지만, Feature Hashing 대비 차이가 0.089점으로 작고 test_id별 모델이 달랐으므로 반복 실행 또는 사람 평가 후 전환하는 것이 안전하다.
