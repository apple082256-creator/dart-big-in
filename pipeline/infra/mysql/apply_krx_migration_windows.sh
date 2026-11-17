#!/usr/bin/env bash
# 기존 Windows MySQL 설치에 KRX 연결 계층을 추가한다.
# root 비밀번호는 mysql.exe 프롬프트에서 직접 입력하며 파일에 저장하지 않는다.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MYSQL_EXE="/mnt/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe"
source "$HERE/.env"
DB="${MYSQL_DATABASE:?}"
WIN_MIGRATION="$(wslpath -w "$HERE/migrations/001_add_krx_index_layer.sql" | sed 's#\\#/#g')"

"$MYSQL_EXE" -uroot -p --default-character-set=utf8mb4 "$DB" -e "
source ${WIN_MIGRATION};
GRANT REFERENCES ON \`${DB}\`.* TO 'dart_etl'@'%';
FLUSH PRIVILEGES;
SELECT COUNT(*) AS krx_indices FROM krx_indices;
SELECT COUNT(*) AS krx_index_constituents FROM krx_index_constituents;
SELECT COUNT(*) AS constituent_foreign_keys
FROM information_schema.referential_constraints
WHERE constraint_schema = DATABASE() AND table_name = 'krx_index_constituents';
"

echo "완료: KRX 지수 연결 계층 및 dart_etl REFERENCES 권한 적용"
