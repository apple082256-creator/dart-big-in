from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import FINANCIAL_CAPEX_DB_PATH, FINANCIAL_DB_PATH, PROCESSED_DIR


STANDARD_CAPEX_NAME = "유형자산취득(CAPEX)"
DIRECT_CAPEX_IDS = {"ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"}
COMPONENT_ACCOUNT_IDS = {
    "dart_PurchaseOfLand",
    "dart_PurchaseOfBuildings",
    "dart_PurchaseOfStructure",
    "dart_PurchaseOfMachinery",
    "dart_PurchaseOfVehicles",
    "dart_PurchaseOfFixturesAndFittings",
    "dart_PurchaseOfOfficeEquipment",
    "dart_PurchaseOfConstructionInProgress",
}
COMPONENT_NAMES = {
    "토지의취득",
    "건물의취득",
    "구축물의취득",
    "기계장치의취득",
    "차량운반구의취득",
    "공구와기구의취득",
    "공구와기구",
    "비품의취득",
    "사무용비품의취득",
    "건설중인자산의취득",
}
EXCLUDED_TERMS = ("무형자산", "소프트웨어", "회원권", "투자부동산", "영업권")
REPORT_ORDER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}


CAPEX_SCHEMA = """
CREATE TABLE capex_facts (
    capex_id INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code TEXT NOT NULL,
    period_id INTEGER NOT NULL,
    fs_div TEXT NOT NULL,
    sj_div TEXT NOT NULL CHECK(sj_div = 'CF'),
    rcept_no TEXT,
    account_id TEXT NOT NULL,
    original_account_nm TEXT NOT NULL,
    standard_account_nm TEXT NOT NULL,
    period_type TEXT NOT NULL,
    value_type TEXT NOT NULL,
    period_start TEXT,
    period_end TEXT NOT NULL,
    currency TEXT NOT NULL,
    raw_value TEXT,
    normalized_value INTEGER,
    calculated_value INTEGER,
    cash_outflow_amount INTEGER,
    sign_normalized INTEGER NOT NULL CHECK(sign_normalized IN (0, 1)),
    is_calculated INTEGER NOT NULL CHECK(is_calculated IN (0, 1)),
    is_derived INTEGER NOT NULL CHECK(is_derived IN (0, 1)),
    value_source TEXT NOT NULL CHECK(value_source IN ('direct', 'derived')),
    capex_method TEXT NOT NULL,
    calculation_method TEXT NOT NULL,
    source_account_ids TEXT NOT NULL,
    source_account_names TEXT NOT NULL,
    source_values TEXT NOT NULL,
    source_account_count INTEGER NOT NULL,
    direct_account_found INTEGER NOT NULL CHECK(direct_account_found IN (0, 1)),
    review_required INTEGER NOT NULL CHECK(review_required IN (0, 1)),
    note TEXT,
    UNIQUE(corp_code, period_id, fs_div, value_type),
    FOREIGN KEY(corp_code) REFERENCES companies(corp_code),
    FOREIGN KEY(period_id) REFERENCES periods(period_id)
);
CREATE INDEX idx_capex_lookup ON capex_facts(corp_code, period_id, fs_div, value_type);
CREATE TABLE capex_build_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE VIEW financial_facts_final AS
SELECT c.company_name AS corp_name, f.corp_code, p.year, p.report_type,
       f.rcept_no, f.account_id, f.original_account_nm, f.standard_account_nm,
       f.sj_div, f.fs_div, f.value_type, f.period_type, f.raw_value,
       f.normalized_value, f.calculated_value, f.is_calculated,
       f.calculation_method, f.currency, 'reported' AS value_source,
       0 AS is_derived, NULL AS source_account_ids,
       NULL AS source_account_names, f.source_values, NULL AS cash_outflow_amount
FROM financial_facts f
JOIN companies c USING(corp_code)
JOIN periods p USING(period_id)
UNION ALL
SELECT c.company_name AS corp_name, cf.corp_code, p.year, p.report_type,
       cf.rcept_no, cf.account_id, cf.original_account_nm, cf.standard_account_nm,
       cf.sj_div, cf.fs_div, cf.value_type, cf.period_type, cf.raw_value,
       cf.normalized_value, cf.calculated_value, cf.is_calculated,
       cf.calculation_method, cf.currency, cf.value_source,
       cf.is_derived, cf.source_account_ids, cf.source_account_names,
       cf.source_values, cf.cash_outflow_amount
FROM capex_facts cf
JOIN companies c USING(corp_code)
JOIN periods p USING(period_id);
"""


def _name_key(value: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value or "")


def _is_direct(row: sqlite3.Row) -> bool:
    return row["account_id"] in DIRECT_CAPEX_IDS or _name_key(row["original_account_nm"]) == "유형자산의취득"


def _is_component(row: sqlite3.Row) -> bool:
    name = row["original_account_nm"] or ""
    if any(term in name for term in EXCLUDED_TERMS):
        return False
    return row["account_id"] in COMPONENT_ACCOUNT_IDS or _name_key(name) in COMPONENT_NAMES


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quarter_dates(year: int, report_type: str) -> tuple[str, str]:
    starts = {"Q1": (1, 1), "H1": (4, 1), "Q3": (7, 1), "FY": (10, 1)}
    ends = {"Q1": (3, 31), "H1": (6, 30), "Q3": (9, 30), "FY": (12, 31)}
    start_month, start_day = starts[report_type]
    end_month, end_day = ends[report_type]
    return date(year, start_month, start_day).isoformat(), date(year, end_month, end_day).isoformat()


def _base_capex_record(rows: list[sqlite3.Row]) -> dict | None:
    direct = [row for row in rows if _is_direct(row)]
    components = [row for row in rows if _is_component(row)]
    if direct:
        selected = direct
        value_source = "direct"
        capex_method = "direct_account"
        is_derived = 0
        note = "direct CAPEX account selected; component fallback suppressed"
    elif components:
        selected = components
        value_source = "derived"
        capex_method = "component_fallback"
        is_derived = 1
        note = "direct CAPEX account absent; approved tangible-asset purchase components summed"
    else:
        return None

    signed_values = [int(row["normalized_value"]) for row in selected if row["normalized_value"] is not None]
    if not signed_values:
        return None
    signed_sum = sum(signed_values)
    cash_outflow = sum(abs(value) for value in signed_values)
    source_values = [
        {
            "account_id": row["account_id"],
            "account_nm": row["original_account_nm"],
            "raw_value": row["raw_value"],
            "normalized_value": row["normalized_value"],
        }
        for row in selected
    ]
    source_ids = list(dict.fromkeys(str(row["account_id"]) for row in selected))
    source_names = list(dict.fromkeys(str(row["original_account_nm"]) for row in selected))
    formula = "direct: 유형자산의 취득" if direct else " + ".join(source_names)
    current = selected[0]
    review = len(direct) > 1
    return {
        "corp_code": current["corp_code"],
        "period_id": current["period_id"],
        "year": current["year"],
        "report_type": current["report_type"],
        "fs_div": current["fs_div"],
        "sj_div": "CF",
        "rcept_no": current["rcept_no"],
        "account_id": current["account_id"] if direct else "derived_CAPEX_from_tangible_components",
        "original_account_nm": current["original_account_nm"] if direct else "세부 유형자산 취득 계정 합계",
        "standard_account_nm": STANDARD_CAPEX_NAME,
        "period_type": current["period_type"],
        "value_type": current["value_type"],
        "period_start": current["period_start"],
        "period_end": current["period_end"],
        "currency": current["currency"] or "KRW",
        "raw_value": str(signed_sum),
        "normalized_value": signed_sum,
        "calculated_value": None,
        "cash_outflow_amount": cash_outflow,
        "sign_normalized": int(any(value < 0 for value in signed_values)),
        "is_calculated": 0,
        "is_derived": is_derived,
        "value_source": value_source,
        "capex_method": capex_method,
        "calculation_method": formula,
        "source_account_ids": json.dumps(source_ids, ensure_ascii=False),
        "source_account_names": json.dumps(source_names, ensure_ascii=False),
        "source_values": json.dumps(source_values, ensure_ascii=False),
        "source_account_count": len(selected),
        "direct_account_found": int(bool(direct)),
        "review_required": int(review),
        "note": note,
        "component_count_available": len(components),
    }


def _quarterly_records(base_records: list[dict]) -> list[dict]:
    quarterly: list[dict] = []
    grouped: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for record in base_records:
        grouped[(record["corp_code"], record["year"], record["fs_div"])][record["report_type"]] = record
    for reports in grouped.values():
        for report_type in ("Q1", "H1", "Q3", "FY"):
            current = reports.get(report_type)
            if not current:
                continue
            previous_type = {"Q1": None, "H1": "Q1", "Q3": "H1", "FY": "Q3"}[report_type]
            if previous_type is None:
                value = current["cash_outflow_amount"]
                inputs = {report_type: current["cash_outflow_amount"]}
                method = "Q1 cumulative cash_outflow_amount"
            else:
                previous = reports.get(previous_type)
                if not previous:
                    continue
                value = current["cash_outflow_amount"] - previous["cash_outflow_amount"]
                inputs = {
                    report_type: current["cash_outflow_amount"],
                    previous_type: previous["cash_outflow_amount"],
                }
                method = f"{report_type} cumulative CAPEX - {previous_type} cumulative CAPEX"
            period_start, period_end = _quarter_dates(current["year"], report_type)
            negative_difference = value < 0
            record = {**current}
            record.update({
                "period_type": "quarterly",
                "value_type": "quarterly",
                "period_start": period_start,
                "period_end": period_end,
                "raw_value": current["raw_value"],
                "normalized_value": None,
                "calculated_value": value,
                # A negative difference between two separately filed cumulative
                # amounts is not a negative cash outflow to normalize.  It means
                # the later filing revised/reclassified the cumulative line, so
                # the standalone quarter cannot be established automatically.
                "cash_outflow_amount": None if negative_difference else value,
                "sign_normalized": 0,
                "is_calculated": 1,
                "calculation_method": f"{method} [{json.dumps(inputs, ensure_ascii=False)}]",
                "source_values": json.dumps(inputs, ensure_ascii=False),
                "review_required": int(bool(current["review_required"]) or negative_difference),
                "note": current["note"] + (
                    "; negative cumulative difference; standalone CAPEX unavailable pending review"
                    if negative_difference else ""
                ),
            })
            quarterly.append(record)
    return quarterly


def _insert_records(connection: sqlite3.Connection, records: list[dict]) -> None:
    columns = [
        "corp_code", "period_id", "fs_div", "sj_div", "rcept_no", "account_id",
        "original_account_nm", "standard_account_nm", "period_type", "value_type",
        "period_start", "period_end", "currency", "raw_value", "normalized_value",
        "calculated_value", "cash_outflow_amount", "sign_normalized", "is_calculated",
        "is_derived", "value_source", "capex_method", "calculation_method",
        "source_account_ids", "source_account_names", "source_values", "source_account_count",
        "direct_account_found", "review_required", "note",
    ]
    placeholders = ",".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO capex_facts({','.join(columns)}) VALUES ({placeholders})",
        [tuple(record[column] for column in columns) for record in records],
    )


def materialize_capex_tables(
    database_path: Path,
    validation_path: Path | None = None,
    source_label: str = "pipeline financial facts",
) -> dict:
    """Build CAPEX records inside a database created by the main pipeline."""
    base_financial_hash = _file_sha256(database_path)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP VIEW IF EXISTS financial_facts_final")
        connection.execute("DROP TABLE IF EXISTS capex_facts")
        connection.execute("DROP TABLE IF EXISTS capex_build_metadata")
        connection.executescript(CAPEX_SCHEMA)
        # Companies whose profile says CAPEX does not apply (banks) get none.
        has_profiles = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='company_financial_profiles'").fetchone()
        excluded = "AND f.corp_code NOT IN (SELECT corp_code FROM company_financial_profiles " \
                   "WHERE capex_applicable=0)" if has_profiles else ""
        rows = connection.execute(
            f"""
            SELECT f.*, p.year, p.report_type, c.company_name
            FROM financial_facts f
            JOIN periods p USING(period_id)
            JOIN companies c USING(corp_code)
            WHERE f.sj_div='CF' AND f.is_calculated=0
              AND f.value_type IN ('cumulative', 'annual') {excluded}
            ORDER BY c.company_name, p.year,
              CASE p.report_type WHEN 'Q1' THEN 1 WHEN 'H1' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END,
              f.ord
            """
        ).fetchall()
        groups: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            groups[(row["corp_code"], row["period_id"], row["fs_div"])].append(row)
        base_records = [record for group in groups.values() if (record := _base_capex_record(group))]
        records = base_records + _quarterly_records(base_records)
        _insert_records(connection, records)
        metadata = {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_database": source_label,
            "source_sha256": base_financial_hash,
            "standard_account_nm": STANDARD_CAPEX_NAME,
            "base_record_count": str(len(base_records)),
            "quarterly_record_count": str(len(records) - len(base_records)),
        }
        connection.executemany("INSERT INTO capex_build_metadata VALUES (?, ?)", metadata.items())
        connection.commit()
    validation_path = validation_path or PROCESSED_DIR / "quality" / "capex_mapping_validation.csv"
    _write_validation_csv(database_path, validation_path)
    return {
        "base_records": len(base_records),
        "quarterly_records": len(records) - len(base_records),
        "target_path": str(database_path),
        "validation_path": str(validation_path),
    }


def build_capex_database(
    source_path: Path = FINANCIAL_DB_PATH,
    target_path: Path = FINANCIAL_CAPEX_DB_PATH,
    validation_path: Path | None = None,
) -> dict:
    """Retain the v2 comparison builder while keeping its source immutable."""
    source_hash_before = _file_sha256(source_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_suffix(".tmp.db")
    if temporary_path.exists():
        temporary_path.unlink()
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(temporary_path)) as target:
        source.backup(target)
    result = materialize_capex_tables(
        temporary_path,
        validation_path=validation_path,
        source_label=source_path.name,
    )
    temporary_path.replace(target_path)
    source_hash_after = _file_sha256(source_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("원본 Financial DB 해시가 변경되었습니다.")
    result.update({"source_unchanged": True, "target_path": str(target_path)})
    return result


def _write_validation_csv(database_path: Path, output_path: Path) -> None:
    import csv

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT c.company_name AS corp_name, p.year, p.report_type,
                   cf.capex_method, cf.direct_account_found,
                   CASE WHEN cf.value_source='derived' THEN cf.source_account_count ELSE 0 END AS derived_account_count,
                   cf.value_source, cf.value_type, cf.cash_outflow_amount AS capex_value,
                   cf.raw_value, cf.normalized_value, cf.calculated_value,
                   cf.sign_normalized, cf.is_derived, cf.is_calculated,
                   cf.source_account_ids, cf.source_account_names, cf.source_values,
                   cf.rcept_no, cf.review_required, cf.note
            FROM capex_facts cf
            JOIN companies c USING(corp_code)
            JOIN periods p USING(period_id)
            ORDER BY c.company_name, p.year,
              CASE p.report_type WHEN 'Q1' THEN 1 WHEN 'H1' THEN 2 WHEN 'Q3' THEN 3 WHEN 'FY' THEN 4 END,
              cf.value_type
            """
        ).fetchall()
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(dict(row) for row in rows)


if __name__ == "__main__":
    print(build_capex_database())
