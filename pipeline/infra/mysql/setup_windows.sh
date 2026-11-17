#!/bin/bash
# Windows에 설치된 MySQL Server 8.0(서비스 MySQL80)에 DB·스키마·시드·계정을 만든다.
# 실행: bash infra/mysql/setup_windows.sh   → MySQL root 비밀번호를 물어본다(화면에 표시 안 됨)
# 앱 계정 비밀번호는 infra/mysql/.env 에서 읽는다. 이미 DB가 있으면 아무것도 바꾸지 않고 오류로 멈춘다.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MYSQL_EXE="/mnt/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe"
set -a; source "$HERE/.env"; set +a
DB="${MYSQL_DATABASE:?}"

# Windows mysql.exe가 읽을 수 있는 위치에 임시 SQL을 만들고, 끝나면 반드시 삭제한다(비밀번호 포함).
WIN_TEMP="$(wslpath "$(cmd.exe /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')")"
SQL_FILE="$(mktemp "$WIN_TEMP/dart_setup_XXXXXX.sql")"
chmod 600 "$SQL_FILE"
trap 'rm -f "$SQL_FILE"' EXIT

{
  echo "CREATE DATABASE \`${DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
  echo "USE \`${DB}\`;"
  cat "$HERE/init/01_schema.sql"
  cat "$HERE/init/02_seed_reference.sql"
  cat <<SQL
CREATE USER IF NOT EXISTS 'dart_etl'@'%' IDENTIFIED BY '${MYSQL_ETL_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES,
      CREATE TEMPORARY TABLES, LOCK TABLES, CREATE VIEW, SHOW VIEW
  ON \`${DB}\`.* TO 'dart_etl'@'%';
CREATE USER IF NOT EXISTS 'dart_backend'@'%' IDENTIFIED BY '${MYSQL_BACKEND_PASSWORD}';
GRANT SELECT, SHOW VIEW ON \`${DB}\`.* TO 'dart_backend'@'%';
FLUSH PRIVILEGES;
SELECT COUNT(*) AS tables_and_views FROM information_schema.tables WHERE table_schema='${DB}';
SELECT COUNT(*) AS periods FROM periods;
SELECT COUNT(*) AS standard_accounts FROM standard_accounts;
SQL
} > "$SQL_FILE"

WIN_SQL="$(wslpath -w "$SQL_FILE" | sed 's#\\#/#g')"
"$MYSQL_EXE" -uroot -p --default-character-set=utf8mb4 -e "source ${WIN_SQL}"
echo "완료: ${DB} 생성, 계정 dart_etl(읽기/쓰기) / dart_backend(읽기 전용)"
