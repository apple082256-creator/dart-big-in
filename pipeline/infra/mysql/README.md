# DART 중앙 MySQL 서버

설계 문서: [`docs/handoff/MYSQL_CENTRALIZATION_PLAN.md`](../../docs/handoff/MYSQL_CENTRALIZATION_PLAN.md)

## 구성

| 파일 | 역할 |
|---|---|
| `docker-compose.yml` | MySQL 8.4 컨테이너(`dart-mysql`), 데이터 볼륨 `dart_mysql_data` |
| `init/01_schema.sql` | 운영 테이블과 조회 VIEW `standardized_financials` |
| `init/02_seed_reference.sql` | 기간 12개(2023~2025 × Q1/H1/Q3/FY), 24개 표준계정 (코드 상수에서 생성) |
| `init/03_users.sh` | 계정 `dart_etl`(읽기/쓰기), `dart_backend`(읽기 전용) |
| `.env` | 비밀번호(로컬 전용, Git 제외, 권한 600) |
| `.env.example` | `.env` 형식 예시 |
| `migrations/001_add_krx_index_layer.sql` | 기존 설치에 KRX 지수 연결 테이블 2개를 추가하는 멱등 migration |
| `migrations/003_rename_krx_bank_to_steel.sql` | 미사용 KRX_BANK를 KRX_STEEL로 전환 |
| `migrations/004_add_filing_source_documents.sql` | 태그 보존 XML과 청크→원본 문서 연결 테이블 추가 |

`init/` 스크립트는 **데이터 볼륨이 비어 있을 때 최초 1회만** 실행된다.

## 현재 운영 방식: Windows MySQL Server 8.0 (2026-10-07)

이 PC에는 Windows용 MySQL Community Server 8.0(서비스 `MySQL80`, 포트 3306)이 설치되어 있어
Docker 대신 이것을 중앙 DB로 사용한다. 최초 1회 DB·스키마·계정 생성:

```bash
bash infra/mysql/setup_windows.sh      # MySQL root 비밀번호 입력
```

SQLite → MySQL 동기화(운영 SQLite 스냅샷 → upsert → 대조 검증, 재실행해도 중복 없음):

```bash
.venv/bin/python infra/mysql/sync_sqlite_to_mysql.py --data-version <YYYY-MM-DD-이름-vN> --dry-run
.venv/bin/python infra/mysql/sync_sqlite_to_mysql.py --data-version <YYYY-MM-DD-이름-vN>
```

- 결과 기록: `sync_runs`(테이블별 실행 결과), `data_versions`(원천 sha256, `validated`, `is_current`)
- 검증 불일치가 있으면 `is_current`를 바꾸지 않고 실패로 끝난다.
- 활성 행이 1% 넘게 줄면 중단한다(의도한 축소면 `--allow-shrink`).
- 최초 적재: `2026-10-06-dart5-v1` — 5개사, financial_facts 10,849 / capex_facts 120 /
  filings 60 / chunk_metadata 21,797 / coverage 60(PASS 60)
- 현재 운영본: `2026-10-07-dart20-auto-xml-v1` — 20개사, financial_facts 44,057 / capex_facts 469 /
  filings 235 / chunk_metadata·chunk_embeddings 97,097 / 원본 XML 329 / chunk 연결 97,097

아래 Docker 방식은 다른 서버로 옮길 때 쓰는 대안이다.

## 실행 (Docker 대안)

사전 조건: Docker Desktop 실행 + Settings → Resources → WSL integration에서 이 Ubuntu 배포판 활성화.

```bash
cd infra/mysql
docker compose up -d
docker compose ps          # STATUS가 healthy가 될 때까지 대기
```

접속 확인(비밀번호는 `.env` 참고, 화면·채팅에 붙여넣지 않는다):

```bash
docker exec -it dart-mysql mysql -udart_backend -p dart_big_in -e "SHOW TABLES;"
```

## 데이터 적재

검증 완료된 20개사 SQLite/FAISS를 기준으로 MySQL 동기화를 실행했다
(`sync_sqlite_to_mysql.py`, data_version `2026-10-07-dart20-auto-xml-v1`). 현재 235개 공시,
97,097개 청크·임베딩, 원본 XML 329개와 청크 연결 97,097개가 검증된 운영본이다.
FAISS 파일 자체는 MySQL에 넣지 않고 로컬 운영 파일로 보존한다.

기존 MySQL 설치에는 KRX 계층 migration을 먼저 한 번 적용한다. Windows
MySQL root 비밀번호를 직접 입력해 다음 스크립트를 실행한 뒤 SQLite 동기화를 수행한다.

```bash
bash infra/mysql/apply_krx_migration_windows.sh
```

공식 구성종목 원본을 적재하기 전에는 지수 4개만 존재하고 구성종목은 0개인
상태가 정상이다. 상세 정책은
[`docs/data/KRX_INDEX_INTEGRATION.md`](../../docs/data/KRX_INDEX_INTEGRATION.md)를 참고한다.

## 청크 벡터 적재와 팀원용 코드 (2026-10-06)

- `migrations/002_add_chunk_embeddings.sql`: 벡터 테이블 `chunk_embeddings`(vector_id = FAISS ID, float32 1024차원 BLOB) 추가. `dart_etl` 권한으로 적용 가능
- `sync_embeddings_to_mysql.py`: BGE SQLite 벡터 → MySQL. MySQL 현재 데이터 버전의 BGE sha256과 같을 때만 실행,
  FAISS 표본 200개 바이트 대조, 적재 후 CRC32 체크섬·vector_id·chunk_metadata 연결 검증.
  `sync_sqlite_to_mysql.py` 실행 후 이어서 실행한다.
  ```bash
  .venv/bin/python infra/mysql/sync_embeddings_to_mysql.py --dry-run
  .venv/bin/python infra/mysql/sync_embeddings_to_mysql.py
  ```
- `team_client/`: 팀원에게 폴더째 전달(드라이브 등). `.env`는 포함하지 않는다.
  - `dart_db_client.py` 조회, `dart_semantic_search.py` 의미 검색(프로젝트 검색과 상위 10개 순서 일치 확인)

## 태그 보존 원본 XML

`filing_source_documents.raw_xml`은 DART XML 파일의 원본 바이트를 보존한다. `chunk_source_documents`는
각 운영 `chunk_id`를 정확히 한 원본 문서에 연결한다. XML→HTML 변환과 화면 렌더링은 백엔드/UI 범위다.

```bash
.venv/bin/python infra/mysql/sync_source_documents_to_mysql.py \
  --data-version 2026-10-07-dart20-auto-xml-v1 --dry-run
.venv/bin/python infra/mysql/sync_source_documents_to_mysql.py \
  --data-version 2026-10-07-dart20-auto-xml-v1
.venv/bin/python infra/mysql/validate_dart15_xml_mysql.py \
  --data-version 2026-10-07-dart20-auto-xml-v1
```

백엔드 조회 SQL과 스키마 설명은
[`docs/handoff/MYSQL_XML_SOURCE_HANDOFF.md`](../../docs/handoff/MYSQL_XML_SOURCE_HANDOFF.md)를 참고한다.

## 팀원 접속 열기

처음에는 `127.0.0.1`에만 묶여 이 PC에서만 접속된다. 팀원 접속을 열 때:

1. `docker-compose.yml`의 `"127.0.0.1:${MYSQL_PORT:-3306}:3306"` → `"${MYSQL_PORT:-3306}:3306"`
2. Windows 방화벽에서 3306 인바운드를 **팀 네트워크 대역만** 허용
3. 팀원에게는 `dart_backend` 계정만 전달(별도 안전한 경로로)
4. `docker compose up -d`로 재적용

인터넷에 3306을 직접 공개하지 않는다. 외부 접속이 필요하면 VPN 또는 SSH 터널을 쓴다.

## 스키마를 바꿨을 때

```bash
docker compose down -v   # 볼륨(데이터) 삭제 — 적재 후에는 백업 먼저
docker compose up -d
```
