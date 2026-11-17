# 일반산업 KPI v7 백업 (운영 미반영)

- 작성일: 2026-10-09
- 결정: 백엔드 팀이 자체 분석 로직을 구현 중이므로 KPI v7의 운영 반영을 보류한다.
- 운영 상태(변경 없음): SQLite·MySQL 모두 `2026-10-09-dart25-bank-quality-v6` (`financial.db` `34d6f2ea…19fb`)
- 백업 위치: `backups/kpi/20261009_kpi_v7/` (`/backups/`는 `.gitignore` 대상이라 저장소 밖에 보존된다)

## 1. 백업 구성

| 파일 | 내용 | SHA-256 |
|---|---|---|
| `financial_kpi_v7.db` | 검증 완료 KPI staging DB 사본(원본 `db/staging/20261009_kpi_v7/financial.db`와 동일) | `337bb2fcb473ec7913a217cb50580a908416ffc32677bcfdf4159bb11be8e47a` |
| `code/` | 커밋 `d15963d` 시점의 KPI 계산·조회·감사 코드, 테스트, 정의서, 인계 문서, 분석기 패치 | `SHA256SUMS` 참조 |
| `reports/backup_readonly_checks.txt` | 무결성·FK 검사, 행 수 | |
| `reports/audit_kpi_v7.{txt,json}` | 전체 감사 결과(위반 0) | |
| `reports/diff_vs_quality_v6.txt` | 운영본 대비 차이 | |
| `reports/kpi_coverage.json` | 기업·KPI별 확보 기간과 결손 목록 | |
| `reports/query_examples_output.txt` | 백업 DB 대상 조회 예시 출력 | |
| `reports/test_full_suite.txt` | 전체 테스트 82개 통과 기록 | |
| `reports/git_commits.txt` | 관련 커밋 목록 | |
| `reports/operational_hashes_before.txt` / `_after.txt` | 백업 전후 운영 파일 해시와 MySQL current | |
| `SHA256SUMS` | 백업 디렉터리 전체 파일 해시 | |

관련 커밋(브랜치 `feature/bge-m3-dense-quarterly`, push 안 함):

| 커밋 | 내용 |
|---|---|
| `c15d209` | 검증 Q4 슬롯·KPI 설계 |
| `430350e` | 일반산업 KPI 계층 |
| `d15963d` | 행 단위 감사·백엔드 인계 |

## 2. 운영본(quality-v6) 대비 차이

| 테이블 | quality-v6 | KPI v7 | 차이 |
|---|---:|---:|---|
| company_financial_profiles | 5 | 25 | 일반 20개사 추가(KRX 그룹 → 산업 프로필, CFS 19 / OFS 1) |
| financial_metric_definitions | 29 | 40 | +11. 공유 5개(OPERATING_INCOME, NET_INCOME, TOTAL_ASSETS/LIABILITIES/EQUITY)는 `industry_profile` 표시만 BANKING → COMMON |
| financial_metric_mappings | 42 | 94 | +52(일반산업 공식 태그) |
| financial_metric_values | 1,554 | 6,033 | +4,479(일반 KPI). 기존 1,554행 변경 0 |
| financial_facts, capex_facts, periods, companies, account_mapping 등 | | | 동일 |
| 스키마 | | | 동일 |

검증(백업 DB 읽기 전용):
- integrity ok, FK 0
- DIRECT 3,716 / DERIVED 2,141 / DOCUMENT_EXTRACTED 176 재현·위치 정상
- 정의·단위·기간·범위·출처·검토상태·비율 입력 검사 6,033행 위반 0

## 3. 복원 및 조회 방법

### A. 복원 없이 읽기 전용으로 사용 (권장)

운영 DB를 건드리지 않고 백업 파일을 경로로 지정해 조회한다.

```python
from pathlib import Path
from src.retrieval.industry_kpi import get_industry_kpis, get_industry_kpis_by_index, kpi_coverage
from src.retrieval.financial_retriever import get_financial_context

KPI_DB = Path("backups/kpi/20261009_kpi_v7/financial_kpi_v7.db")
get_industry_kpis("00126380", 2024, "FY", value_type="annual", path=KPI_DB)             # 삼성전자 KPI
get_industry_kpis_by_index("KRX_AUTO", 2025, "Q3", ("OPERATING_MARGIN",), "quarterly", path=KPI_DB)
kpi_coverage(KPI_DB)                                                                      # 확보율
get_financial_context("현대자동차", 2024, "FY", account_name="매출채권", path=KPI_DB)       # 전체 계정도 그대로 조회 가능
```

`industry_kpi` 모듈은 `mode=ro`로만 연다. KPI 정의·단위·해석 규칙은 `docs/handoff/BACKEND_KPI_HANDOFF.md`를 따른다.

### B. 실험용 사본

```bash
mkdir -p db/sandbox
cp -p backups/kpi/20261009_kpi_v7/financial_kpi_v7.db db/sandbox/financial_kpi_v7.db
sha256sum db/sandbox/financial_kpi_v7.db   # 337bb2fc…e47a 확인
```

운영 경로 `db/financial/financial.db`에 덮어쓰지 않는다.

### C. 나중에 운영에 반영할 때

1. 운영본이 여전히 quality-v6(`34d6f2ea…`)이면 이 백업 파일을 그대로 staging으로 쓸 수 있다.
   - 이 파일은 그 운영본을 원본으로 만들었다.
2. 운영본이 그 사이 바뀌었다면 백업 파일로 덮어쓰지 않는다. 최신 데이터가 사라지기 때문이다.
   - 최신 운영본에서 KPI를 다시 생성한다: `python -m src.industry_kpi_pipeline --target db/staging/<날짜>_kpi/financial.db`
   - 이 파이프라인은 멱등이고 원천 계정·금융 KPI를 바꾸지 않는다.
   - 원천이 같은 기간은 이 백업과 값을 대조해 회귀를 확인한다.
3. 반영 전제 조건
   - 백엔드 분석기 정책 확정: `docs/handoff/patches/analyzer_bank_only_metric_layer.patch` 적용 또는 자체 로직 대체
   - 패치 없이 KPI 행이 운영에 들어가면, 현재 분석기는 일반 20개사에서 원천 계정 대신 KPI 행을 쓴다.
4. 반영 절차: 기존 절차와 같다(백업 → 교체 → 테스트·감사 → MySQL 적재 → 전체 검증 → 승격). 제안 버전명은 `…-kpi-v7` 형식이다.

## 4. 운영 영향

- **운영 DB·MySQL·Vector DB·FAISS·임베딩·원본 XML:** 변경하지 않았다. 백업 전후 해시와 MySQL current가 같다(`reports/operational_hashes_*`).
- **브랜치의 KPI 코드:** 일반산업 프로필이 없는 현재 운영 DB에서는 KPI 행을 만들지 않는다.
  - 관련 코드: `financial_metrics`의 일반 KPI 단계, `industry_kpi_pipeline`
  - 전체 계정 조회(`get_financial_context`)와 금융 KPI 조회(`get_financial_metrics`)의 결과는 그대로다.
- **에이치브이엠 자동 선택 조회 개선:** 프로필 데이터가 있어야 나타나므로 운영에는 아직 적용되지 않았다. KPI v7 반영 시 함께 적용된다.
