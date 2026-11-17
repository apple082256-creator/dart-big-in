# BGE-M3 Four-Mode E2E Ablation

## 실험 조건

- 기존 `bge_m3_dense` 후보 10개와, 과거 명칭 `bge_m3_hybrid`였던
  `bge_m3_priority_hybrid` 후보 10개를 재사용했다.
- 신규 후보는 `bge_m3_priority_dense`, `bge_m3_hybrid`만 생성했다.
- 2025 FY, 기업 필터 없음, Top-K=5, temperature=0.1 조건을 유지했다.
- 후보 및 Judge 모델은 기존 test_id별 매핑을 그대로 사용했고 모델 fallback은 금지했다.
- 네 후보는 test_id별 결정적 순서의 Method A/B/C/D로 익명화해 새 Judge 평가를 수행했다.

## 점수 공식

| 방식 | 공식 |
|---|---|
| `bge_m3_dense` | `dense_score` |
| `bge_m3_priority_dense` | `dense_score * (0.90 + 0.10 * search_priority)` |
| `bge_m3_hybrid` | `0.60 * dense_score + 0.40 * lexical_score` |
| `bge_m3_priority_hybrid` | `(0.60 * dense_score + 0.40 * lexical_score) * (0.90 + 0.10 * search_priority)` |

## 평균 결과

| 방식 | 전체 | 산업 인사이트 | 키워드 인사이트 |
|---|---:|---:|---:|
| `bge_m3_dense` | 4.642 | 4.600 | 4.683 |
| `bge_m3_priority_dense` | 4.600 | 4.533 | 4.667 |
| `bge_m3_hybrid` | 4.667 | 4.617 | 4.717 |
| `bge_m3_priority_hybrid` | 4.667 | 4.583 | 4.750 |

## 분리 효과

- Dense에서 priority 효과: -0.042
- Hybrid에서 priority 효과: +0.000
- Pure 조건에서 lexical 효과: +0.025
- Priority 조건에서 lexical 효과: +0.067

## test_id별 최고 방식

| test_id | 최고 방식 |
|---|---|
| I01 | bge_m3_priority_dense, bge_m3_dense, bge_m3_hybrid |
| I02 | bge_m3_hybrid, bge_m3_priority_hybrid |
| I03 | bge_m3_priority_dense |
| I04 | bge_m3_hybrid |
| I05 | bge_m3_priority_hybrid, bge_m3_hybrid, bge_m3_dense |
| K01 | bge_m3_dense, bge_m3_priority_dense, bge_m3_hybrid, bge_m3_priority_hybrid |
| K02 | bge_m3_priority_hybrid, bge_m3_priority_dense, bge_m3_hybrid, bge_m3_dense |
| K03 | bge_m3_priority_hybrid, bge_m3_hybrid |
| K04 | bge_m3_priority_dense, bge_m3_priority_hybrid, bge_m3_dense |
| K05 | bge_m3_priority_dense, bge_m3_dense |

## 품질 및 호출 현황

- 기존 후보 재사용: 20개
- 신규 Gemini 후보 성공: 20개
- 신규 Judge 성공: 10개
- 실패 기록: 24건
- 재시도: 3회
- quota 오류: 8건
- Judge 환각 플래그: 4건
- 자동 REVIEW: 5건

## 제한사항

- 기존 재사용 후보에는 과거 실행 당시 검색/LLM 지연시간이 저장되지 않아 해당 값은 null이다.
- 40개 검색 결과는 원래 BGE 자산에서 체크포인트에 고정된 값을 사용했다. 이후 운영
  DB와 FAISS가 교체되어 재개 실행에서는 현재 운영 자산을 읽거나 재검색하지 않았다.
- K04 Judge는 4,096 출력 토큰에서 반복적으로 JSON이 잘려(`MAX_TOKENS`) 동일 모델·
  프롬프트·스키마를 유지하고 출력 상한만 8,192로 높여 완료했다.
- 표본은 산업 인사이트 5개와 키워드 인사이트 5개, 총 10개다.
- Streamlit 통합 질문에서 재무 계정 과다 조회, 순이익 중복, CF/SCE 편중,
  사업 내용·부문별 실적 근거 부족 문제가 알려져 있다. 기존 후보와의 조건 통제를 위해
  이번 실험 중 질의 확장이나 섹션 우선순위는 변경하지 않았다. 이는 별도 후속 과제다.

## 최종 추천

**USE_DIFFERENT_METHOD_BY_FEATURE**
