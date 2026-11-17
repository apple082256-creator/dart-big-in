from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

from src.config import FINANCIAL_DB_PATH


# These are stable application identifiers, not asserted KRX official index codes.
# official_index_code remains NULL until an official KRX source is imported.
KRX_INDEX_SEEDS = (
    ("KRX_SEMI", "KRX 반도체", "반도체"),
    ("KRX_STEEL", "KRX 철강", "철강"),
    ("KRX_AUTO", "KRX 자동차", "자동차"),
    ("KRX_HEALTH", "KRX 헬스케어", "헬스케어"),
    ("KRX_BANK", "KRX 은행", "은행·금융지주"),
)


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS krx_indices (
    index_code TEXT PRIMARY KEY,
    official_index_code TEXT,
    index_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    source_as_of TEXT,
    source_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS krx_index_constituents (
    index_code TEXT NOT NULL,
    corp_code TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    source_as_of TEXT NOT NULL,
    weight REAL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(index_code, corp_code, effective_from),
    FOREIGN KEY(index_code) REFERENCES krx_indices(index_code),
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code),
    CHECK(effective_to IS NULL OR effective_to >= effective_from)
);
CREATE INDEX IF NOT EXISTS ix_krx_constituents_index_current
ON krx_index_constituents(index_code, is_current);
CREATE INDEX IF NOT EXISTS ix_krx_constituents_corp_current
ON krx_index_constituents(corp_code, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS uk_krx_constituents_one_current
ON krx_index_constituents(index_code, corp_code) WHERE is_current = 1;
"""


def initialize_krx_schema(connection: sqlite3.Connection) -> None:
    """Add the KRX grouping layer without copying company-owned facts."""
    connection.executescript(SCHEMA)
    connection.executemany(
        """
        INSERT INTO krx_indices(index_code, index_name, display_name)
        VALUES (?, ?, ?)
        ON CONFLICT(index_code) DO UPDATE SET
          index_name=excluded.index_name,
          display_name=excluded.display_name,
          updated_at=CURRENT_TIMESTAMP
        """,
        KRX_INDEX_SEEDS,
    )


def migrate_krx_schema(path: Path = FINANCIAL_DB_PATH) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        initialize_krx_schema(connection)


def copy_krx_index_layer(source_path: Path, target_path: Path) -> None:
    """Preserve index metadata when the financial pipeline atomically rebuilds its DB."""
    if not source_path.exists():
        return
    with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as source:
        has_schema = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='krx_indices'"
        ).fetchone()
        if not has_schema:
            return
        source.row_factory = sqlite3.Row
        indices = [dict(row) for row in source.execute("SELECT * FROM krx_indices")]
        constituents = [dict(row) for row in source.execute("SELECT * FROM krx_index_constituents")]

    with closing(sqlite3.connect(target_path)) as target, target:
        target.execute("PRAGMA foreign_keys = ON")
        initialize_krx_schema(target)
        target_codes = {row[0] for row in target.execute("SELECT corp_code FROM companies")}
        missing = sorted({row["corp_code"] for row in constituents} - target_codes)
        if missing:
            raise RuntimeError(
                "재무 DB 재생성 대상에 KRX 구성기업이 누락되어 연결 계층을 보존할 수 없습니다: "
                f"{missing}"
            )
        target.executemany(
            """INSERT INTO krx_indices(
                   index_code, official_index_code, index_name, display_name, source_as_of,
                   source_url, is_active, created_at, updated_at
               ) VALUES (:index_code, :official_index_code, :index_name, :display_name,
                         :source_as_of, :source_url, :is_active, :created_at, :updated_at)
               ON CONFLICT(index_code) DO UPDATE SET
                 official_index_code=excluded.official_index_code,
                 index_name=excluded.index_name, display_name=excluded.display_name,
                 source_as_of=excluded.source_as_of, source_url=excluded.source_url,
                 is_active=excluded.is_active, updated_at=excluded.updated_at""",
            indices,
        )
        target.executemany(
            """INSERT INTO krx_index_constituents(
                   index_code, corp_code, effective_from, effective_to, source_as_of,
                   weight, is_current, created_at, updated_at
               ) VALUES (:index_code, :corp_code, :effective_from, :effective_to,
                         :source_as_of, :weight, :is_current, :created_at, :updated_at)""",
            constituents,
        )


def _iso(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else value


def replace_current_constituents(
    index_code: str,
    constituents: Iterable[Mapping[str, object]],
    *,
    source_as_of: date | str,
    effective_from: date | str | None = None,
    path: Path = FINANCIAL_DB_PATH,
) -> dict[str, int]:
    """Apply one verified KRX snapshot while retaining membership history.

    Input rows must already contain DART ``corp_code`` values resolved from
    official KRX ``stock_code`` values. This function never name-matches.
    """
    as_of = _iso(source_as_of)
    effective = _iso(effective_from or source_as_of)
    rows = list(constituents)
    requested_codes = {str(row["corp_code"]) for row in rows}
    if len(requested_codes) != len(rows):
        raise ValueError("구성종목에 중복 corp_code가 있습니다.")

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        initialize_krx_schema(connection)
        if not connection.execute(
            "SELECT 1 FROM krx_indices WHERE index_code=?", (index_code,)
        ).fetchone():
            raise ValueError(f"등록되지 않은 KRX index_code입니다: {index_code}")
        known = {
            row[0]
            for row in connection.execute(
                "SELECT corp_code FROM companies WHERE corp_code IN ("
                + ",".join("?" for _ in requested_codes)
                + ")",
                sorted(requested_codes),
            )
        } if requested_codes else set()
        missing = sorted(requested_codes - known)
        if missing:
            raise ValueError(f"companies에 없는 corp_code입니다: {missing}")

        current = {
            row["corp_code"]: row
            for row in connection.execute(
                "SELECT * FROM krx_index_constituents WHERE index_code=? AND is_current=1",
                (index_code,),
            )
        }
        removed = set(current) - requested_codes
        if removed:
            placeholders = ",".join("?" for _ in removed)
            connection.execute(
                f"""UPDATE krx_index_constituents
                    SET effective_to=?, is_current=0, updated_at=CURRENT_TIMESTAMP
                    WHERE index_code=? AND is_current=1 AND corp_code IN ({placeholders})""",
                (effective, index_code, *sorted(removed)),
            )

        inserted = updated = 0
        for row in rows:
            corp_code = str(row["corp_code"])
            weight = row.get("weight")
            if corp_code in current:
                connection.execute(
                    """UPDATE krx_index_constituents
                       SET source_as_of=?, weight=?, updated_at=CURRENT_TIMESTAMP
                       WHERE index_code=? AND corp_code=? AND is_current=1""",
                    (as_of, weight, index_code, corp_code),
                )
                updated += 1
            else:
                connection.execute(
                    """INSERT INTO krx_index_constituents(
                           index_code, corp_code, effective_from, source_as_of, weight, is_current
                       ) VALUES (?, ?, ?, ?, ?, 1)""",
                    (index_code, corp_code, effective, as_of, weight),
                )
                inserted += 1
        connection.execute(
            """UPDATE krx_indices
               SET source_as_of=?, updated_at=CURRENT_TIMESTAMP
               WHERE index_code=?""",
            (as_of, index_code),
        )
    return {"inserted": inserted, "updated": updated, "closed": len(removed)}


def resolve_constituents_by_stock_code(
    rows: Iterable[Mapping[str, object]],
    *,
    path: Path = FINANCIAL_DB_PATH,
) -> tuple[list[dict], list[dict]]:
    """Resolve official constituent rows through stock_code, never company name."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        companies = {
            row["stock_code"]: dict(row)
            for row in connection.execute(
                "SELECT corp_code, company_name AS corp_name, stock_code FROM companies"
            )
            if row["stock_code"]
        }
    matched: list[dict] = []
    unresolved: list[dict] = []
    for raw in rows:
        item = dict(raw)
        stock_code = str(item.get("stock_code") or "").zfill(6)
        company = companies.get(stock_code)
        if company is None:
            item["mapping_status"] = "stock_code_not_found"
            unresolved.append(item)
            continue
        item.update(company)
        item["mapping_status"] = "matched"
        matched.append(item)
    return matched, unresolved


def get_companies_by_index(
    index_code: str,
    as_of: date | str | None = None,
    *,
    path: Path = FINANCIAL_DB_PATH,
) -> list[dict]:
    params: list[object] = [index_code]
    if as_of is None:
        validity = "k.is_current=1"
    else:
        point = _iso(as_of)
        validity = "k.effective_from <= ? AND (k.effective_to IS NULL OR k.effective_to > ?)"
        params.extend([point, point])
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT c.corp_code, c.stock_code, c.company_name AS corp_name,
                           k.weight, k.effective_from, k.effective_to, k.source_as_of
                    FROM krx_index_constituents k
                    JOIN companies c USING(corp_code)
                    WHERE k.index_code=? AND {validity}
                    ORDER BY k.weight IS NULL, k.weight DESC, c.corp_code""",
                params,
            )
        ]


def get_indices_by_company(
    corp_code: str,
    as_of: date | str | None = None,
    *,
    path: Path = FINANCIAL_DB_PATH,
) -> list[dict]:
    params: list[object] = [corp_code]
    if as_of is None:
        validity = "k.is_current=1"
    else:
        point = _iso(as_of)
        validity = "k.effective_from <= ? AND (k.effective_to IS NULL OR k.effective_to > ?)"
        params.extend([point, point])
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                f"""SELECT i.index_code, i.official_index_code, i.index_name, i.display_name,
                           k.weight, k.effective_from, k.effective_to, k.source_as_of
                    FROM krx_index_constituents k
                    JOIN krx_indices i USING(index_code)
                    WHERE k.corp_code=? AND {validity}
                    ORDER BY i.index_code""",
                params,
            )
        ]
