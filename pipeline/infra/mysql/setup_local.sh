#!/bin/bash
# Docker 없이 WSL(Ubuntu)에 직접 설치한 MySQL에 DB·스키마·시드·계정을 만든다.
# 사전: sudo apt install -y mysql-server && sudo service mysql start
# 실행: bash infra/mysql/setup_local.sh   (sudo 비밀번호를 물어볼 수 있음)
# 비밀번호는 infra/mysql/.env 에서 읽고 화면에 출력하지 않는다.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$HERE/.env"; set +a
DB="${MYSQL_DATABASE:?}"

existing=$(sudo mysql -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='${DB}'")
if [ "$existing" -gt 0 ]; then
  echo "이미 ${DB}에 테이블 ${existing}개가 있어 스키마 생성을 건너뜁니다."
else
  sudo mysql -e "CREATE DATABASE IF NOT EXISTS \`${DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
  sudo mysql "$DB" < "$HERE/init/01_schema.sql"
  sudo mysql "$DB" < "$HERE/init/02_seed_reference.sql"
  echo "스키마와 참조 데이터를 생성했습니다."
fi

sudo mysql <<SQL
CREATE USER IF NOT EXISTS 'dart_etl'@'%' IDENTIFIED BY '${MYSQL_ETL_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE TEMPORARY TABLES, LOCK TABLES, CREATE VIEW, SHOW VIEW
  ON \`${DB}\`.* TO 'dart_etl'@'%';
CREATE USER IF NOT EXISTS 'dart_backend'@'%' IDENTIFIED BY '${MYSQL_BACKEND_PASSWORD}';
GRANT SELECT, SHOW VIEW ON \`${DB}\`.* TO 'dart_backend'@'%';
FLUSH PRIVILEGES;
SQL
echo "계정 dart_etl(읽기/쓰기), dart_backend(읽기 전용) 준비 완료."

sudo mysql "$DB" -e "SELECT COUNT(*) AS tables_and_views FROM information_schema.tables WHERE table_schema='${DB}'; SELECT COUNT(*) AS periods FROM periods; SELECT COUNT(*) AS standard_accounts FROM standard_accounts;"
