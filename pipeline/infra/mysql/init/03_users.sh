#!/bin/bash
# 역할별 계정 생성 (최초 기동 시 1회). 비밀번호는 .env 에서만 읽는다.
#   dart_etl     : 동기화 스크립트용, 대상 DB 읽기/쓰기
#   dart_backend : 백엔드 API용, 읽기 전용(SELECT)
# Docker NAT 때문에 접속 IP가 게이트웨이로 보이므로 host는 '%'로 두고,
# 접근 제한은 포트 바인딩과 방화벽으로 한다(README 참고).
set -euo pipefail

: "${MYSQL_ETL_PASSWORD:?MYSQL_ETL_PASSWORD is required}"
: "${MYSQL_BACKEND_PASSWORD:?MYSQL_BACKEND_PASSWORD is required}"

mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'dart_etl'@'%' IDENTIFIED BY '${MYSQL_ETL_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE TEMPORARY TABLES, LOCK TABLES, CREATE VIEW, SHOW VIEW
  ON \`${MYSQL_DATABASE}\`.* TO 'dart_etl'@'%';

CREATE USER IF NOT EXISTS 'dart_backend'@'%' IDENTIFIED BY '${MYSQL_BACKEND_PASSWORD}';
GRANT SELECT, SHOW VIEW ON \`${MYSQL_DATABASE}\`.* TO 'dart_backend'@'%';

FLUSH PRIVILEGES;
SQL
