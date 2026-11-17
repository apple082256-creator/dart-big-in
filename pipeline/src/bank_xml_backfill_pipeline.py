"""Backfill bank metrics for API-missing slots from preserved DART statement XML.

Only slots recorded as ``DART_FNLTT_API_NO_DATA`` are touched.  Values are
written to ``financial_metric_values`` as ``DOCUMENT_EXTRACTED`` with a full
XML locator; ``financial_facts`` (the DART API layer) is never modified.  Every
value carries the checks it passed and a verification label; values without a
passing check, with a failed in-document check, or on combined accounts are
stored as ``review_required``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT, RAW_DIR
from src.db.expense_sign import restore_expense_signs
from src.db.financial_metrics import (
    initialize_financial_metric_schema,
    refresh_derived_metrics,
    upsert_metric_values,
)
from src.extract.bank_statement_xml import (
    BS_LINES,
    COMBINED_LOAN_LINES,
    EXTRACTOR_VERSION,
    UNIT_SCALE,
    StatementLine,
    extract_combined_loan_note,
    extract_statement_lines,
    line_name,
)

BGE_DB_PATH = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
MISSING_REASON = "DART_FNLTT_API_NO_DATA"
EXPENSE_METRICS = {"INTEREST_EXPENSE", "FEE_COMMISSION_EXPENSE", "CREDIT_LOSS_EXPENSE"}
STATEMENT_SUBSECTION = {"CFS": "2. 연결재무제표", "OFS": "4. 재무제표"}
NET_IDENTITIES = (
    ("NET_INTEREST_INCOME", "INTEREST_INCOME", "INTEREST_EXPENSE"),
    ("NET_FEE_COMMISSION_INCOME", "FEE_COMMISSION_INCOME", "FEE_COMMISSION_EXPENSE"),
)


def document_id(rcept_no: str, source_path: str) -> str:
    """Same identifier as MySQL filing_source_documents.document_id."""
    return hashlib.sha256(f"{rcept_no}\0{source_path}".encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _to_int(text: object) -> int | None:
    if text in (None, ""):
        return None
    try:
        return int(str(text).replace(",", ""))
    except ValueError:
        return None


def _next_year_comparatives(corp_code: str, year: int, report_type: str, fs_div: str,
                            account_ids: dict[str, set[str]]) -> dict[str, tuple[int, str]]:
    """Year values restated in the year+1 same-type API report (flows only)."""
    path = RAW_DIR / "financial" / corp_code / str(year + 1) / f"{report_type}_{fs_div}.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    found: dict[str, tuple[int, str]] = {}
    for item in payload.get("list") or []:
        if item.get("sj_div") not in {"IS", "CIS"}:
            continue
        for metric_code, ids in account_ids.items():
            if metric_code in found or item.get("account_id") not in ids:
                continue
            value = _to_int(item.get("frmtrm_add_amount")) or _to_int(item.get("frmtrm_q_amount"))
            if value is not None:
                found[metric_code] = (value, str(item.get("rcept_no")))
    return found


def _document(bge: sqlite3.Connection, corp_code: str, year: int, report_type: str) -> tuple[str, str]:
    rows = bge.execute(
        """SELECT DISTINCT rcept_no, source_file FROM filing_chunks
           WHERE corp_code=? AND year=? AND report_type=?""",
        (corp_code, year, report_type),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"{corp_code} {year} {report_type}: 원본 문서가 1개가 아닙니다: {rows}")
    return str(rows[0][0]), str(rows[0][1])


def _evidence_chunk(bge: sqlite3.Connection, rcept_no: str, fs_div: str, line: StatementLine) -> str | None:
    """First statement chunk containing the presented amount (evidence link only)."""
    needle = f"{abs(line.presented_value):,}"
    row = bge.execute(
        """SELECT chunk_id FROM filing_chunks
           WHERE rcept_no=? AND subsection_name=? AND instr(original_text, ?) > 0
           ORDER BY vector_id LIMIT 1""",
        (rcept_no, STATEMENT_SUBSECTION[fs_div], needle),
    ).fetchone()
    return row[0] if row else None


def build_slot_rows(fin: sqlite3.Connection, bge: sqlite3.Connection, slot: sqlite3.Row,
                    profile: sqlite3.Row, account_ids: dict[str, set[str]]) -> tuple[list[dict], dict]:
    corp_code, year, report_type = slot["corp_code"], int(slot["year"]), slot["report_type"]
    fs_div, scope = profile["preferred_fs_div"], profile["comparison_scope"]
    rcept_no, source_path = _document(bge, corp_code, year, report_type)
    extraction = extract_statement_lines(PROJECT_ROOT / source_path, fs_div, year, report_type)
    summary = {"corp_code": corp_code, "year": year, "report_type": report_type, "fs_div": fs_div,
               "rcept_no": rcept_no, "statements": extraction.statements, "issues": extraction.issues,
               "metrics": {}}
    if extraction.issues:
        return [], summary
    lines = extraction.lines
    krw = {code: line.krw for code, line in lines.items()}
    checks: dict[str, list[dict]] = {code: [] for code in lines}

    totals = ("TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY")
    if all(code in krw for code in totals):
        passed = krw["TOTAL_ASSETS"] == krw["TOTAL_LIABILITIES"] + krw["TOTAL_EQUITY"]
        for code in totals:
            checks[code].append({"check": "assets=liabilities+equity", "passed": passed})
    for net, income, expense in NET_IDENTITIES:
        if net in krw and income in krw and expense in krw:
            passed = krw[net] == krw[income] - abs(krw[expense])
            for code in (net, income, expense):
                checks[code].append({"check": f"{net}={income}-|{expense}| (document)", "passed": passed})
    aux = {code: line.krw for code, line in extraction.aux.items()}
    if "NET_INCOME" in krw and "PRETAX_INCOME" in aux and "INCOME_TAX" in aux:
        # A tax benefit is presented as a negative expense, so accept either direction.
        candidates = {aux["PRETAX_INCOME"] - abs(aux["INCOME_TAX"]), aux["PRETAX_INCOME"] + abs(aux["INCOME_TAX"])}
        checks["NET_INCOME"].append({"check": "pretax income - income tax = net income (document)",
                                     "pretax": aux["PRETAX_INCOME"], "income_tax": aux["INCOME_TAX"],
                                     "passed": krw["NET_INCOME"] in candidates})
    for code, line in lines.items():
        if line.statement != "BS":
            continue
        match = next((item for name, item in extraction.summary.items() if name in BS_LINES[code]), None)
        if match is not None:
            checks[code].append({"check": "summary statement table (document D-0-3-1-0)",
                                 "table_line": match["table_line"], "row_index": match["row_index"],
                                 "summary_value": match["value"], "passed": match["value"] == krw[code]})
    for code, (value, source_rcept) in _next_year_comparatives(
            corp_code, year, report_type, fs_div, account_ids).items():
        if code in krw:
            checks[code].append({"check": "next-year API comparative |value|", "rcept_no": source_rcept,
                                 "api_value": value, "passed": abs(value) == abs(krw[code])})

    reference = {
        (row["metric_code"], row["value_type"]): int(row["value"])
        for row in fin.execute(
            """SELECT metric_code, value_type, value FROM financial_metric_values
               WHERE corp_code=? AND year=? AND report_type='Q3' AND fs_div=? AND scope=?
                 AND source_kind='DIRECT' AND value IS NOT NULL""",
            (corp_code, year, fs_div, scope),
        )
    }
    if report_type == "H1":
        for code in krw:
            cumulative = reference.get((code, "cumulative"))
            quarter = reference.get((code, "quarterly"))
            if cumulative is not None and quarter is not None:
                checks[code].append({"check": "Q3 cumulative - Q3 quarter (API) |value|",
                                     "api_value": cumulative - quarter,
                                     "passed": abs(cumulative - quarter) == abs(krw[code])})

    rows: list[dict] = []
    doc_id = document_id(rcept_no, source_path)
    for code, line in lines.items():
        passed = any(item["passed"] for item in checks[code])
        reasons = []
        value = line.krw
        sign = {"presented_sign": "-" if value < 0 else "+", "aligned": False}
        ref = reference.get((code, "cumulative"))
        if line.statement == "CIS" and ref and value and (ref > 0) != (value > 0):
            sign["reference"] = {"source": f"{year} Q3 DIRECT cumulative", "value": ref}
            if code in EXPENSE_METRICS and passed:
                value, sign["aligned"] = -value, True
            else:
                reasons.append("sign_differs_from_api_series")
        if line_name(line.account_name) in COMBINED_LOAN_LINES:
            reasons.append("combined_account")
        failed_in_document = [c["check"] for c in checks[code] if "(document" in c["check"] and not c["passed"]]
        if failed_in_document:
            reasons.append("document_check_failed")
        if not passed:
            reasons.append("no_passing_check")
        verification = ("NOT_VERIFIABLE" if not passed else
                        "REVIEW_REQUIRED" if reasons else "VERIFIED")
        review = "approved" if verification == "VERIFIED" else "review_required"
        restatement = next((
            {"rcept_no": c["rcept_no"], "restated_value": c["api_value"],
             "note": "later report restated this period; originally reported value kept"}
            for c in checks[code]
            if c["check"] == "next-year API comparative |value|" and not c["passed"]
        ), None)
        method = f"xml_statement_extract({EXTRACTOR_VERSION}) x{line.scale}"
        if sign["aligned"]:
            method += f" sign_aligned_to_{year}Q3"
        sources = json.dumps({
            "extractor": EXTRACTOR_VERSION,
            "locator": {"source_path": source_path, "section_code": line.section_code,
                        "statement": line.statement, "table_line": line.table_line,
                        "row_index": line.row_index, "column_header": line.column_header,
                        "layout": line.layout},
            "presented": {"text": line.presented_text, "value": line.presented_value,
                          "unit": line.unit, "scale": line.scale},
            "sign": sign,
            "checks": checks[code],
            "verification": verification,
            **({"review_reasons": reasons} if reasons else {}),
            **({"restatement": restatement} if restatement else {}),
        }, ensure_ascii=False)
        value_types = ["point_in_time"] if line.statement == "BS" else (
            ["quarterly", "cumulative"] if report_type == "Q1" else ["cumulative"])
        for value_type in value_types:
            rows.append({
                "corp_code": corp_code, "year": year, "report_type": report_type,
                "metric_code": code, "value": value, "unit": "KRW", "value_type": value_type,
                "fs_div": fs_div, "scope": scope, "source_kind": "DOCUMENT_EXTRACTED",
                "source_type": "dart_document_xml", "source_rcept_no": rcept_no,
                "source_document_id": doc_id, "source_chunk_id": _evidence_chunk(bge, rcept_no, fs_div, line),
                "source_account_id": None, "source_account_name": line.account_name,
                "source_fact_key": None, "calculation_method": method,
                "source_values": sources, "review_status": review,
            })
        summary["metrics"][code] = {"value": value, "review": review, "reasons": reasons,
                                    "verification": verification, "restatement": restatement,
                                    "checks_passed": [c["check"] for c in checks[code] if c["passed"]],
                                    "checks_failed": [c["check"] for c in checks[code] if not c["passed"]],
                                    "sign_aligned": sign["aligned"]}
    return rows, summary


def apply_backfill(connection: sqlite3.Connection, bge_path: Path = BGE_DB_PATH) -> dict[str, object]:
    connection.row_factory = sqlite3.Row
    slots = connection.execute(
        """SELECT corp_code, year, report_type FROM financial_collection_status
           WHERE missing_reason=? ORDER BY corp_code, year, report_type""",
        (MISSING_REASON,),
    ).fetchall()
    profiles = {row["corp_code"]: row for row in connection.execute("SELECT * FROM company_financial_profiles")}
    bank_codes = {code for code, profile in profiles.items()
                  if profile["industry_profile"] in ("BANK_HOLDING", "INTERNET_BANK")}
    account_ids: dict[tuple[str, str], dict[str, set[str]]] = {}
    for row in connection.execute(
            "SELECT industry_profile, metric_code, account_id FROM financial_metric_mappings"):
        account_ids.setdefault(row["industry_profile"], {}).setdefault(row["metric_code"], set()).add(row["account_id"])
    before = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    all_rows, summaries = [], []
    with connection, closing(sqlite3.connect(f"file:{bge_path.resolve()}?mode=ro", uri=True)) as bge:
        # Sign checks compare with stored metric values, so read them as filed;
        # the refresh below gives expense rows their analysis sign again.
        restore_expense_signs(connection)
        connection.row_factory = sqlite3.Row
        for slot in slots:
            profile = profiles[slot["corp_code"]]
            rows, summary = build_slot_rows(connection, bge, slot, profile,
                                            account_ids.get(profile["industry_profile"], {}))
            all_rows += rows
            summaries.append(summary)
        upsert_metric_values(connection, all_rows)
        derived = refresh_derived_metrics(connection, corp_codes=bank_codes)
    after = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    return {"slots": len(slots), "document_rows": len(all_rows), "derived_upserts": derived,
            "metric_values_before": before, "metric_values_after": after,
            "summaries": summaries}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--apply-only", action="store_true", help="기존 staging DB에 다시 적용(멱등).")
    parser.add_argument("--report", type=Path, help="슬롯별 추출·검증 결과 JSON 경로")
    args = parser.parse_args()
    if args.target.resolve() == FINANCIAL_DB_PATH.resolve():
        raise ValueError("운영 DB를 직접 대상으로 실행할 수 없습니다. staging 경로를 지정하세요.")
    if not args.apply_only:
        if args.target.exists():
            raise FileExistsError(f"이미 존재하는 staging DB입니다: {args.target}")
        source_hash = _sha256(args.source)
        args.target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)) as src, \
                closing(sqlite3.connect(args.target)) as dst:
            src.backup(dst)
        if _sha256(args.source) != source_hash:
            raise RuntimeError("스냅샷 중 source DB가 변경됐습니다.")
        print(f"source sha256 {source_hash}")
    with closing(sqlite3.connect(args.target)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        result = apply_backfill(connection)
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print({key: value for key, value in result.items() if key != "summaries"})


if __name__ == "__main__":
    main()


NOTE_METRIC = "LOANS_AT_AMORTISED_COST_NOTE"


def _combined_loan_periods(connection: sqlite3.Connection, corp_code: str, fs_div: str) -> list[dict]:
    """Periods whose balance sheet shows loans combined with other financial assets."""
    periods: dict[tuple, dict] = {}
    for year, report, name, value, rcept in connection.execute(
            """SELECT p.year,p.report_type,f.original_account_nm,f.normalized_value,f.rcept_no
               FROM financial_facts f JOIN periods p USING(period_id)
               WHERE f.corp_code=? AND f.fs_div=? AND f.sj_div='BS' AND f.value_type='point_in_time'
                 AND f.is_calculated=0""",
            (corp_code, fs_div)):
        if line_name(name) in COMBINED_LOAN_LINES and value is not None:
            periods[(int(year), report)] = {"year": int(year), "report_type": report, "combined": int(value),
                                            "combined_source": "API financial_facts", "rcept_no": rcept}
    for year, report, name, value, rcept in connection.execute(
            """SELECT year,report_type,source_account_name,value,source_rcept_no FROM financial_metric_values
               WHERE corp_code=? AND fs_div=? AND metric_code='LOANS_AT_AMORTISED_COST'
                 AND source_kind='DOCUMENT_EXTRACTED'""",
            (corp_code, fs_div)):
        if line_name(name) in COMBINED_LOAN_LINES:
            periods.setdefault((int(year), report), {
                "year": int(year), "report_type": report, "combined": int(value),
                "combined_source": "DOCUMENT_EXTRACTED statement", "rcept_no": rcept})
    return sorted(periods.values(), key=lambda p: (p["year"], "Q1 H1 Q3 FY".split().index(p["report_type"])))


def build_combined_loan_note_rows(connection: sqlite3.Connection, bge: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Loans split out of a combined balance-sheet line, from the filing notes."""
    rows, summaries = [], []
    for profile in connection.execute("SELECT * FROM company_financial_profiles").fetchall():
        corp_code, fs_div, scope = profile["corp_code"], profile["preferred_fs_div"], profile["comparison_scope"]
        for period in _combined_loan_periods(connection, corp_code, fs_div):
            files = [r[0] for r in bge.execute(
                """SELECT DISTINCT source_file FROM filing_chunks
                   WHERE corp_code=? AND year=? AND report_type=? ORDER BY source_file""",
                (corp_code, period["year"], period["report_type"]))]
            rcepts = {r[0] for r in bge.execute(
                "SELECT DISTINCT rcept_no FROM filing_chunks WHERE corp_code=? AND year=? AND report_type=?",
                (corp_code, period["year"], period["report_type"]))}
            summary = {"corp_code": corp_code, **period, "issues": []}
            note, source_path = None, None
            for path in files:  # main report first; attached audit report as fallback
                candidate = extract_combined_loan_note(PROJECT_ROOT / path, fs_div)
                if "composition" in candidate and "loan_detail" in candidate:
                    note, source_path = candidate, path
                    break
                summary["issues"] += [f"{Path(path).name}: {issue}" for issue in candidate["issues"]]
            if note is None or len(rcepts) != 1:
                summary["result"] = "NOT_VERIFIABLE"
                summaries.append(summary)
                continue
            rcept_no = rcepts.pop()
            composition, detail = note["composition"], note["loan_detail"]
            values = composition["values"]
            loans = values.get("대출채권", values.get("상각후원가측정대출채권"))
            total = values.get("합계")
            parts = sum(v for k, v in values.items() if k != "합계")
            current = (composition.get("period_label") or composition.get("column_header") or "").strip()
            checks = [
                {"check": "composition parts = composition total", "passed": total is not None and parts == total},
                {"check": "composition total = balance-sheet combined line",
                 "combined": period["combined"], "source": period["combined_source"],
                 "passed": total == period["combined"]},
                {"check": "loans detail carrying amount = composition loans", "detail_net": detail["net"],
                 "passed": loans is not None and detail["net"] == loans},
                {"check": "loss allowance deducted in loans detail", "passed": bool(detail["allowance_deducted"])},
                {"check": "current-period column", "label": current, "passed": current.startswith("당")},
            ]
            verified = loans is not None and all(c["passed"] for c in checks)
            unit_scale = UNIT_SCALE[composition["unit"]]
            needle = f"{abs(loans) // unit_scale:,}" if loans is not None else None
            chunk = bge.execute(
                "SELECT chunk_id FROM filing_chunks WHERE source_file=? AND instr(original_text, ?) > 0 "
                "ORDER BY vector_id LIMIT 1", (source_path, needle)).fetchone() if needle else None
            summary.update(result="VERIFIED" if verified else "REVIEW_REQUIRED", loans=loans,
                           rcept_no_note=rcept_no, source_path=source_path,
                           checks_failed=[c["check"] for c in checks if not c["passed"]])
            summaries.append(summary)
            if loans is None:
                continue
            rows.append({
                "corp_code": corp_code, "year": period["year"], "report_type": period["report_type"],
                "metric_code": NOTE_METRIC, "value": loans, "unit": "KRW", "value_type": "point_in_time",
                "fs_div": fs_div, "scope": scope, "source_kind": "DOCUMENT_EXTRACTED",
                "source_type": "dart_document_xml_note", "source_rcept_no": rcept_no,
                "source_document_id": document_id(rcept_no, source_path),
                "source_chunk_id": chunk[0] if chunk else None, "source_account_id": None,
                "source_account_name": "대출채권", "source_fact_key": None,
                "calculation_method": f"xml_note_extract({EXTRACTOR_VERSION}) x{unit_scale}",
                "source_values": json.dumps({
                    "extractor": EXTRACTOR_VERSION,
                    "locator": {"source_path": source_path, "section_code": note["section_code"],
                                "composition_paragraph": composition["paragraph"],
                                "composition_table_line": composition["table_line"],
                                "loan_detail_paragraph": detail["paragraph"],
                                "loan_detail_table_line": detail["table_line"],
                                "loan_detail_layout": detail["layout"]},
                    "unit": composition["unit"], "composition": values, "checks": checks,
                    "verification": "VERIFIED" if verified else "REVIEW_REQUIRED",
                    "definition": "상각후원가측정 대출채권 순장부금액: 총장부금액 + 이연대출부대손익 - "
                                  "현재가치할인차금 - 손실충당금. 결합 계정(예치금·기타금융자산 포함)은 별도 보존",
                }, ensure_ascii=False),
                "review_status": "approved" if verified else "review_required",
            })
    return rows, summaries


def apply_combined_loan_notes(connection: sqlite3.Connection, bge_path: Path = BGE_DB_PATH) -> dict:
    connection.row_factory = sqlite3.Row
    initialize_financial_metric_schema(connection)
    with closing(sqlite3.connect(f"file:{bge_path.resolve()}?mode=ro", uri=True)) as bge:
        rows, summaries = build_combined_loan_note_rows(connection, bge)
    with connection:
        upsert_metric_values(connection, rows)
    return {"rows": len(rows), "summaries": summaries}
