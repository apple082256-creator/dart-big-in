"""Add newly filed periods (e.g. 2026 Q1/H1) to a staging copy of the Financial DB.

- Companies, stock codes and DART corp codes come from the target DB and are
  checked against the DART corp code list.
- Each company keeps the statement scope it already uses (CFS, or OFS for the
  separate-only filers).
- ``fnlttSinglAcntAll`` payloads are saved under ``--raw-dir`` (never under
  ``data/raw``) and only when the API returned data: a "no data" answer (013)
  is not cached, so a report filed later is fetched on the next run.
- A report not filed yet is NOT_DUE before its legal deadline and MISSING after;
  every slot is recorded in ``financial_collection_status``.
- With ``--download-xml`` the latest valid filing (corrections are filed
  later) is downloaded under ``--raw-dir``; existing files are kept.
- ``replace_slot`` re-loads one slot from a corrected filing, keeping the
  replaced rows in history tables.
- The filing list (``list.json``, including corrections) is recorded per slot,
  and the API receipt number is compared with it.
- Facts are appended with the regular cleaning, mapping and quarter rules;
  existing rows are never changed (a slot already in the DB is skipped).
- CAPEX is rebuilt from facts for CAPEX-applicable companies only; metric
  slots are filled for the bank profiles (new expense rows stay in review
  until their filed-statement evidence is added).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from src.collect.financial_collector import DartApiError, DartClient
from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT, REPORTS, load_settings
from src.db.capex_db import materialize_capex_tables
from src.db.financial_db import save_financial_data
from src.db.financial_metrics import add_missing_fact_metrics, replace_collection_status
from src.mapping.account_mapper import apply_mapping, build_mapping_draft
from src.preprocess.financial_cleaner import clean_financials
from src.preprocess.period_normalizer import normalize_period_values

REPORT_NAMES = {"Q1": "분기보고서", "H1": "반기보고서", "Q3": "분기보고서", "FY": "사업보고서"}
REPORT_MONTH = {"Q1": ".03)", "H1": ".06)", "Q3": ".09)", "FY": ".12)"}


def filing_deadline(year: int, report_type: str) -> date:
    """Legal deadline: 45 days after the quarter (Q1/H1/Q3), 90 days after the year (FY)."""
    return {"Q1": date(year, 5, 15), "H1": date(year, 8, 14), "Q3": date(year, 11, 14),
            "FY": date(year + 1, 3, 31)}[report_type]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _companies(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """SELECT c.corp_code, c.company_name, c.stock_code,
                  (SELECT f.fs_div FROM financial_facts f WHERE f.corp_code=c.corp_code
                   GROUP BY f.fs_div ORDER BY COUNT(*) DESC LIMIT 1) AS fs_div
           FROM companies c ORDER BY c.corp_code""").fetchall()
    return [{"corp_code": r[0], "corp_name": r[1], "stock_code": r[2], "fs_div": r[3]} for r in rows]


def check_company_codes(client: DartClient, companies: list[dict]) -> list[dict]:
    codes = client.get_corp_codes()
    issues = []
    for company in companies:
        match = codes[codes["corp_code"].eq(company["corp_code"])]
        stock = match["stock_code"].fillna("").str.strip().iloc[0] if len(match) else None
        if stock != company["stock_code"]:
            issues.append({**company, "dart_stock_code": stock})
    return issues


def filing_list(client: DartClient, corp_code: str, year: int, today: date) -> list[dict]:
    response = client._get("list.json", {"corp_code": corp_code, "bgn_de": f"{year}0101",
                                         "end_de": today.strftime("%Y%m%d"), "last_reprt_at": "N",
                                         "pblntf_ty": "A", "page_count": "100"})
    payload = response.json()
    return payload.get("list") or [] if payload.get("status") == "000" else []


def _slot_filings(filings: list[dict], year: int, report_type: str) -> list[dict]:
    token = f"({year}{REPORT_MONTH[report_type]}"
    return sorted((f for f in filings if REPORT_NAMES[report_type] in f["report_nm"] and token in f["report_nm"]),
                  key=lambda f: f["rcept_no"])


def fetch_slot(client: DartClient, company: dict, year: int, report_type: str, raw_dir: Path) -> dict:
    report_code = REPORTS[report_type][0]
    path = raw_dir / "financial" / company["corp_code"] / str(year) / f"{report_type}_{company['fs_div']}.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = client._get("fnlttSinglAcntAll.json", {"corp_code": company["corp_code"], "bsns_year": str(year),
                                                          "reprt_code": report_code,
                                                          "fs_div": company["fs_div"]}).json()
        if payload.get("status") == "000":  # never cache "no data"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def download_document(client: DartClient, corp_code: str, year: int, report_type: str, rcept_no: str,
                      raw_dir: Path) -> list[dict]:
    """Original filing (document.xml ZIP) under ``raw_dir``; existing files are never overwritten."""
    zip_path = raw_dir / "filings" / "zip" / corp_code / str(year) / report_type / f"{rcept_no}.zip"
    xml_dir = raw_dir / "filings" / "xml" / corp_code / str(year) / report_type / rcept_no
    if not zip_path.is_file():
        content = client._get("document.xml", {"rcept_no": rcept_no}).content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            # DART answers errors (e.g. 800 maintenance, 020 rate limit) as a small XML.
            message = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content[:500].decode("utf-8", "replace")))
            raise DartApiError(f"원문 다운로드 실패 {rcept_no}: {message.strip()}")
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(content)
    documents = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename)
            if member.is_dir() or name.suffix.lower() not in {".xml", ".xhtml", ".html", ".htm"}:
                continue
            destination = xml_dir / "__".join(p for p in name.parts if p not in {".", ".."})
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
            documents.append({"path": str(destination), "sha256": _sha256(destination),
                              "bytes": destination.stat().st_size,
                              "main": destination.name == f"{rcept_no}.xml"})
    return documents


def status_rows(statuses: list[dict]) -> list[dict]:
    """``financial_collection_status`` rows for the collected slots."""
    rows = []
    for status in statuses:
        cfs = status["fs_div"] == "CFS"
        collected = status["state"] == "COLLECTED"
        rows.append({
            "corp_code": status["corp_code"], "year": status["year"], "report_type": status["report_type"],
            "requested_fs_div": status["fs_div"], "selected_fs_div": status["fs_div"] if collected else None,
            "cfs_status": str(status["api_status"]) if cfs else "NOT_TRIED",
            "ofs_status": "NOT_TRIED" if cfs else str(status["api_status"]),
            "missing_reason": None if collected else (
                "NOT_DUE" if status["state"] == "NOT_DUE" else "DART_FNLTT_API_NO_DATA"),
            "rcept_no": status["api_rcept_no"],
        })
    return rows


HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_facts_history AS SELECT * FROM financial_facts WHERE 0;
CREATE TABLE IF NOT EXISTS financial_metric_values_history AS SELECT * FROM financial_metric_values WHERE 0;
"""


def _ensure_history(connection: sqlite3.Connection) -> None:
    connection.executescript(HISTORY_SCHEMA)
    for table in ("financial_facts_history", "financial_metric_values_history"):
        columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
        for column, kind in (("archived_at", "TEXT"), ("replaced_by_rcept_no", "TEXT"), ("archive_reason", "TEXT")):
            if column not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _slot_api_facts(new_raw: pd.DataFrame) -> pd.DataFrame:
    cleaned = clean_financials(new_raw)
    mapping = build_mapping_draft(cleaned)
    periodized = normalize_period_values(apply_mapping(cleaned, mapping))
    return periodized[~periodized["is_calculated"].astype(bool)], mapping


def replace_slot(connection: sqlite3.Connection, corp_code: str, year: int, report_type: str,
                 new_raw: pd.DataFrame, reason: str) -> dict:
    """Replace one already-loaded slot with a re-filed (corrected) report.

    The slot's API facts, the company-year calculated quarters and the
    company-year fact-based metric rows are copied to the history tables
    before they change; only that company-year is regenerated.  An identical
    payload changes nothing.
    """
    from src.db.financial_metrics import add_missing_fact_metrics as _fill_metrics
    from src.db.quarter_backfill import FACT_COLUMNS, _raw_frame, insert_account_matched_quarters
    from src.preprocess.period_normalizer import _add_calculated_quarterly_values

    period_id = connection.execute("SELECT period_id FROM periods WHERE year=? AND report_type=?",
                                   (year, report_type)).fetchone()[0]
    compare = ("fs_div", "sj_div", "account_key", "ord", "value_type", "normalized_value", "rcept_no")
    new_facts, mapping = _slot_api_facts(new_raw)
    current = {tuple(r) for r in connection.execute(
        f"SELECT {','.join(compare)} FROM financial_facts WHERE corp_code=? AND period_id=? AND is_calculated=0",
        (corp_code, period_id))}
    incoming = {tuple(None if pd.isna(r[c]) else (int(r[c]) if c in ("ord", "normalized_value") else r[c])
                      for c in compare) for r in new_facts.to_dict("records")}
    if current == incoming:
        return {"changed": False}
    rcept = str(new_facts["rcept_no"].iloc[0])
    now = datetime.now(timezone.utc).isoformat()
    year_periods = [r[0] for r in connection.execute("SELECT period_id FROM periods WHERE year=?", (year,))]
    marks = ",".join("?" for _ in year_periods)
    with connection:
        _ensure_history(connection)
        fact_filter = (f"corp_code=? AND ((period_id=? AND is_calculated=0) OR "
                       f"(period_id IN ({marks}) AND is_calculated=1))")
        args = (corp_code, period_id, *year_periods)
        connection.execute(f"INSERT INTO financial_facts_history SELECT *, ?, ?, ? FROM financial_facts "
                           f"WHERE {fact_filter}", (now, rcept, reason, *args))
        archived_facts = connection.execute(f"DELETE FROM financial_facts WHERE {fact_filter}", args).rowcount
        metric_filter = "corp_code=? AND year=? AND source_kind IN ('DIRECT','DERIVED')"
        connection.execute(f"INSERT INTO financial_metric_values_history SELECT *, ?, ?, ? FROM "
                           f"financial_metric_values WHERE {metric_filter}", (now, rcept, reason, corp_code, year))
        archived_metrics = connection.execute(f"DELETE FROM financial_metric_values WHERE {metric_filter}",
                                              (corp_code, year)).rowcount
        records = new_facts.assign(period_id=period_id)
        values = [tuple(None if (isinstance(v, float) and pd.isna(v)) else v
                        for v in (r.get(c) for c in FACT_COLUMNS)) for r in records.to_dict("records")]
        connection.executemany(f"INSERT INTO financial_facts({','.join(FACT_COLUMNS)}) "
                               f"VALUES ({','.join('?' for _ in FACT_COLUMNS)})",
                               [tuple(int(v) if isinstance(v, bool) else v for v in row) for row in values])
        frame = _raw_frame(connection)
        frame = frame[(frame["corp_code"] == corp_code) & (frame["year"] == year)]
        generated = _add_calculated_quarterly_values(frame).iloc[len(frame):].to_dict("records")
        inserted = insert_account_matched_quarters(connection, generated)
        existing_mapping = set(connection.execute("SELECT account_key, sj_div FROM account_mapping"))
        new_mapping = mapping[~mapping.apply(lambda r: (r["account_key"], r["sj_div"]) in existing_mapping, axis=1)]
    if len(new_mapping):
        save_financial_data(_db_path(connection), new_facts.iloc[0:0], new_mapping)
    with connection:
        metrics = _fill_metrics(connection, years={year}, corp_codes={corp_code})
    materialize_capex_tables(_db_path(connection), validation_path=_db_path(connection).parent / "capex_validation.csv",
                             source_label=f"slot replacement {corp_code} {year} {report_type}")
    return {"changed": True, "archived_facts": archived_facts, "archived_metrics": archived_metrics,
            "api_facts": len(values), "calculated_quarters": inserted, "metric_rows": metrics, "rcept_no": rcept}


def _db_path(connection: sqlite3.Connection) -> Path:
    return Path(connection.execute("PRAGMA database_list").fetchone()[2])


def collect(client: DartClient, companies: list[dict], year: int, reports: tuple[str, ...], raw_dir: Path,
            today: date, download_xml: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    rows, statuses = [], []
    for company in companies:
        filings = filing_list(client, company["corp_code"], year, today)
        for report_type in reports:
            payload = fetch_slot(client, company, year, report_type, raw_dir)
            listed = _slot_filings(filings, year, report_type)
            api_rows = payload.get("list") or [] if payload.get("status") == "000" else []
            api_rcept = str(api_rows[0]["rcept_no"]) if api_rows else None
            latest = listed[-1]["rcept_no"] if listed else None  # corrections are filed later
            documents = download_document(client, company["corp_code"], year, report_type, latest, raw_dir) \
                if latest and download_xml else []
            due = filing_deadline(year, report_type)
            state = "COLLECTED" if api_rows else ("NOT_DUE" if today <= due else "MISSING")
            statuses.append({
                "corp_code": company["corp_code"], "company": company["corp_name"], "year": year,
                "report_type": report_type, "fs_div": company["fs_div"], "api_status": payload.get("status"),
                "state": state, "deadline": due.isoformat(), "api_rcept_no": api_rcept,
                "filings": [{"rcept_no": f["rcept_no"], "report_nm": f["report_nm"], "rcept_dt": f["rcept_dt"]}
                            for f in listed],
                "api_rcept_in_list": api_rcept in {f["rcept_no"] for f in listed} if api_rcept else None,
                "latest_rcept_no": latest, "api_is_latest": api_rcept == latest if api_rcept else None,
                "documents": documents,
                "corrections": sum("정정" in f["report_nm"] for f in listed),
                "rows": len(api_rows),
            })
            for account in api_rows:
                rows.append({**account, "company_name": company["corp_name"], "stock_code": company["stock_code"],
                             "year": year, "quarter": report_type, "report_code": REPORTS[report_type][0],
                             "report_name": REPORTS[report_type][1], "fs_div_requested": company["fs_div"]})
    return pd.DataFrame(rows), statuses


def ingest(target: Path, raw: pd.DataFrame, statuses: list[dict] | None = None) -> dict:
    """Append new-period facts; slots already present are skipped, existing rows untouched."""
    with closing(sqlite3.connect(target)) as connection:
        present = {(r[0], r[1], r[2]) for r in connection.execute(
            "SELECT DISTINCT f.corp_code, p.year, p.report_type FROM financial_facts f JOIN periods p USING(period_id)")}
        existing_mapping = set(connection.execute("SELECT account_key, sj_div FROM account_mapping"))
        before = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("financial_facts", "capex_facts", "financial_metric_values", "periods")}
    raw = raw[~raw.apply(lambda r: (r["corp_code"], int(r["year"]), r["quarter"]) in present, axis=1)]
    if raw.empty:
        return {"facts_added": 0, "skipped_existing_slots": True}
    years = {int(value) for value in raw["year"].unique()}
    cleaned = clean_financials(raw)
    mapping = build_mapping_draft(cleaned)
    standardized = apply_mapping(cleaned, mapping)
    periodized = normalize_period_values(standardized)
    new_mapping = mapping[~mapping.apply(lambda r: (r["account_key"], r["sj_div"]) in existing_mapping, axis=1)]
    save_financial_data(target, periodized, new_mapping)
    capex = materialize_capex_tables(target, validation_path=target.parent / "capex_validation.csv",
                                     source_label="period increment")
    with closing(sqlite3.connect(target)) as connection:
        with connection:
            if statuses:
                replace_collection_status(connection, status_rows(statuses))
            metrics = add_missing_fact_metrics(connection, years=years)
        after = {t: connection.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    return {"facts_added": after["financial_facts"] - before["financial_facts"],
            "capex_rows": [before["capex_facts"], after["capex_facts"]],
            "metric_rows": [before["financial_metric_values"], after["financial_metric_values"]],
            "periods": [before["periods"], after["periods"]], "new_mapping_rows": len(new_mapping),
            "metric_rows_added": metrics, "capex": capex["base_records"] + capex["quarterly_records"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--reports", nargs="+", default=["Q1", "H1"], choices=list(REPORTS))
    parser.add_argument("--report", type=Path)
    parser.add_argument("--download-xml", action="store_true", help="최신 유효 공시 원문 XML도 raw-dir에 받는다.")
    args = parser.parse_args()
    if args.target.resolve() in (FINANCIAL_DB_PATH.resolve(), args.source.resolve()):
        raise ValueError("운영 DB나 source를 직접 대상으로 실행할 수 없습니다.")
    if (PROJECT_ROOT / "data" / "raw").resolve() in args.raw_dir.resolve().parents or \
            args.raw_dir.resolve() == (PROJECT_ROOT / "data" / "raw").resolve():
        raise ValueError("원본 data/raw가 아닌 별도 raw 경로를 지정하세요.")
    if args.target.exists():
        raise FileExistsError(args.target)
    args.target.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _sha256(args.source)
    with closing(sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(args.target)) as dst:
        src.backup(dst)
        companies = _companies(dst)
    client = DartClient(load_settings().api_key)
    today = date.today()
    code_issues = check_company_codes(client, companies)
    raw, statuses = collect(client, companies, args.year, tuple(args.reports), args.raw_dir, today,
                            download_xml=args.download_xml)
    result = {"source_sha256": source_hash, "companies": len(companies), "company_code_issues": code_issues,
              "statuses": statuses,
              "ingest": ingest(args.target, raw, statuses) if not raw.empty else {"facts_added": 0}}
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    summary = pd.DataFrame(statuses).groupby(["report_type", "state"]).size().to_dict()
    print({"companies": len(companies), "code_issues": len(code_issues), "states": summary, **result["ingest"]})


if __name__ == "__main__":
    main()
