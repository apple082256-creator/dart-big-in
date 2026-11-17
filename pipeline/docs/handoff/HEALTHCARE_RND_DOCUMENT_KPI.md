# 헬스케어 연구개발비 문서 KPI

## 목적

DART 재무제표 본표 API에 없는 헬스케어 연구개발비를 사업보고서의
`주요계약 및 연구개발활동` 표와 주석에서 추출해 표준 KPI로 제공한다.

## 표준 정의

| 코드 | 정의 | 단위 | 값 구분 |
|---|---|---|---|
| `RND_EXPENSE` | 공시된 연구개발비용 총액. 연결 우선, 연결 부재 시 별도 | KRW | Q1 3개월, H1/Q3 누적, FY 연간 |
| `RND_TO_REVENUE` | 공시 비율 우선, 없으면 같은 범위의 `RND_EXPENSE / REVENUE` | BPS | ratio |

- 비용 처리액, 제조경비·매출원가, 자산화 개발비 및 정부보조금은 총액과
  중복 합산하지 않고 `source_values`에 공시 구성요소로 보존한다.
- 연결/별도 여부가 명시되지 않거나 같은 범위에서 서로 다른 총액 후보가
  경쟁하면 `review_required`로 적재한다.
- `source_rcept_no`, 원본 XML 경로, 청크 ID, 표시 단위와 환산 배수를 보존한다.

## 실행

운영 DB 직접 쓰기는 차단된다. 새 스테이징 DB를 만들 때:

```bash
python -m src.healthcare_rnd_pipeline \
  --target /tmp/healthcare-rnd/financial.db \
  --report /tmp/healthcare-rnd/report.json
```

동일 스테이징 DB에 멱등 재실행할 때:

```bash
python -m src.healthcare_rnd_pipeline \
  --target /tmp/healthcare-rnd/financial.db \
  --apply-only \
  --report /tmp/healthcare-rnd/report-rerun.json
```

## 검증 항목

JSON 보고서는 5개사 × 2023~2025년 × Q1/H1/Q3/FY의 60개 슬롯별로
후보 수, 선택값, 연결/별도 범위, 접수번호, 검토 상태와 예외를 기록한다.
요약에는 두 KPI의 커버리지, 자연키 중복, 승인·검토·누락 슬롯 수가 포함된다.

현재 검증 스냅샷 결과:

- `RND_EXPENSE`: 60/60
- `RND_TO_REVENUE`: 60/60
- 자연키 중복: 0
- 슬롯 상태: 승인 60, 검토 필요 0, 누락 0
- 유한양행 2024 Q3·2025 Q3는 표의 단위와 연결 기준 각주가 인접한 겹침
  청크로 분리되어 있었다. 동일 접수번호의 연구개발활동 청크를 결합해
  `K-IFRS 연결기준으로 작성`을 확인한 뒤 연결 기준으로 승인한다.

## 조회 동작

분석기는 `연구개발` 또는 `R&D` 질문에 `financial_metric_values`의 두 KPI를
우선 사용한다. KPI가 없으면 공시 검색으로 폴백하며, 이 경우 “기업에 수치가
없다”가 아니라 “현재 표준 KPI에 미적재”라고 안내하도록 프롬프트를 제한한다.
