"""Extract healthcare R&D disclosures into the standardized metric layer.

The DART financial-statement API omits most healthcare R&D disclosures because
they live in the business-report R&D table or notes.  This pipeline reads the
already collected BGE-M3 filing chunks, prefers consolidated totals, preserves
the disclosed composition and provenance, and writes only to a staging copy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT
from src.db.financial_metrics import (
    initialize_financial_metric_schema,
    upsert_company_profiles,
    upsert_metric_values,
)

BGE_DB_PATH = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
YEARS = (2023, 2024, 2025)
REPORTS = ("Q1", "H1", "Q3", "FY")
EXTRACTOR_VERSION = "healthcare-rnd-v1"
UNIT_SCALE = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}
TOTAL_RE = re.compile(r"연구개발비용\s*(?:합계|계)?(?:\(\*\))?\s+([-()\d,]+)")
RATIO_LABEL_RE = re.compile(r"연구개발비\s*/\s*매출액\s*비율")
UNIT_RE = re.compile(r"\(\s*단위\s*:\s*(원|천원|백만원|억원)(?:\s*,\s*%)?\s*\)")
COMPONENT_LABELS = (
    "판매비와 관리비", "판매관리비", "판매비와관리비", "제조경비", "매출원가",
    "개발비(무형자산)", "정부보조금", "보조금 차감 후 금액",
)


def document_id(rcept_no: str, source_path: str) -> str:
    """Return the canonical ID used by MySQL ``filing_source_documents``.

    Keep the human-readable path in ``source_values.locator.source_path``;
    ``source_document_id`` itself must be the stable lookup key.
    """
    return hashlib.sha256(f"{rcept_no}\0{source_path}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candidate:
    corp_code: str
    corp_name: str
    year: int
    report_type: str
    rcept_no: str
    chunk_id: str
    source_file: str
    source_document_id: str
    section_name: str
    subsection_name: str | None
    value: int
    unit: str
    presented_value: str
    ratio_percent: str | None
    fs_div: str
    scope: str
    score: int
    components: dict[str, str]
    excerpt: str


def _number(text: str) -> int | None:
    raw = text.replace(",", "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if not raw or not re.fullmatch(r"\d+", raw):
        return None
    value = int(raw)
    return -value if negative else value


def _nearest_unit(text: str, position: int) -> str | None:
    matches = list(UNIT_RE.finditer(text[max(0, position - 1800):position + 50]))
    return matches[-1].group(1) if matches else None


def _component_values(text: str, start: int, unit: str) -> dict[str, str]:
    window = text[max(0, start - 1000):start + 1100]
    found: dict[str, str] = {}
    for label in COMPONENT_LABELS:
        match = re.search(re.escape(label) + r"\s+([-()\d,]+)", window)
        if match and _number(match.group(1)) is not None:
            found[label] = match.group(1)
    return found


def _disclosed_ratio(text: str) -> str | None:
    label = RATIO_LABEL_RE.search(text)
    if not label:
        return None
    tail = text[label.end():label.end() + 260]
    # Formula captions contain numbers (notably ×100); remove the bracketed
    # formula before taking the first presented period value.
    tail = re.sub(r"\[[^]]{0,180}\]", " ", tail, count=1)
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%?", tail)
    return match.group(1) if match else None


def extract_candidates(row: sqlite3.Row) -> list[Candidate]:
    text = re.sub(r"\s+", " ", row["original_text"])
    candidates = []
    for match in TOTAL_RE.finditer(text):
        # A table can show both gross R&D and the post-subsidy subtotal.  The
        # standardized total is the explicitly labelled gross line; preserve
        # the subsidy subtotal as a component instead of selecting it again.
        if "차감후" in text[max(0, match.start() - 24):match.start()].replace(" ", ""):
            continue
        presented = match.group(1)
        amount = _number(presented)
        unit = _nearest_unit(text, match.start())
        if amount is None or amount <= 0 or unit is None:
            continue
        before = text[max(0, match.start() - 1800):match.start()]
        after = text[match.start():match.start() + 1400]
        connected = bool(re.search(r"연결\s*(?:재무제표\s*)?기준", before, re.I))
        separate = bool(re.search(r"별도\s*(?:재무제표\s*)?기준", before, re.I))
        # If both headings occur in a long chunk, the nearest heading governs.
        last_connected = max(before.rfind("연결기준"), before.rfind("연결 재무제표 기준"))
        last_separate = max(before.rfind("별도기준"), before.rfind("별도 재무제표 기준"))
        if last_connected >= 0 or last_separate >= 0:
            connected = last_connected > last_separate
            separate = last_separate > last_connected
        if re.search(r"K-?IFRS\s*연결기준", after, re.I):
            connected, separate = True, False
        fs_div, scope = ("CFS", "consolidated") if connected else (("OFS", "separate") if separate else ("OFS", "unspecified"))
        ratio = _disclosed_ratio(after)
        components = _component_values(text, match.start(), unit)
        subsection = row["subsection_name"] or ""
        score = (100 if connected else 20 if separate else 0)
        score += 35 if "연구개발활동" in subsection else 0
        score += 20 if ratio else 0
        score += min(len(components), 5) * 3
        score += 5 if "합계" in match.group(0) or " 계" in match.group(0) else 0
        candidates.append(Candidate(
            corp_code=row["corp_code"], corp_name=row["corp_name"], year=int(row["year"]),
            report_type=row["report_type"], rcept_no=row["rcept_no"], chunk_id=row["chunk_id"],
            source_file=row["source_file"],
            source_document_id=(
                row["source_document_id"]
                if "source_document_id" in row.keys() and row["source_document_id"]
                else document_id(row["rcept_no"], row["source_file"])
            ),
            section_name=row["section_name"],
            subsection_name=row["subsection_name"], value=amount * UNIT_SCALE[unit], unit=unit,
            presented_value=presented, ratio_percent=ratio,
            fs_div=fs_div, scope=scope, score=score, components=components,
            excerpt=text[max(0, match.start() - 300):match.start() + 500],
        ))
    return candidates


def _candidate_key(candidate: Candidate) -> tuple:
    return (candidate.value, candidate.ratio_percent, candidate.fs_div, tuple(sorted(candidate.components.items())))


def select_candidate(candidates: list[Candidate]) -> tuple[Candidate | None, list[str]]:
    """Deduplicate overlapping chunks and select consolidated disclosure first."""
    unique: dict[tuple, Candidate] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        current = unique.get(key)
        if current is None or (candidate.score, len(candidate.excerpt)) > (current.score, len(current.excerpt)):
            unique[key] = candidate
    ranked = sorted(unique.values(), key=lambda c: (c.score, c.value), reverse=True)
    if not ranked:
        return None, ["research-development total not found"]
    chosen = ranked[0]
    reasons = []
    same_scope = [c for c in ranked if c.fs_div == chosen.fs_div and c.value != chosen.value]
    if same_scope:
        reasons.append("multiple different totals in selected scope")
    if chosen.scope == "unspecified":
        reasons.append("consolidated/separate scope not explicit")
    return chosen, reasons


def _profiles(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """SELECT DISTINCT c.corp_code FROM krx_index_constituents k
           JOIN companies c USING(corp_code)
           WHERE k.index_code='KRX_HEALTH' AND k.is_current=1 ORDER BY c.corp_code"""
    ).fetchall()
    return [{
        "corp_code": row[0], "industry_profile": "HEALTHCARE", "preferred_fs_div": "CFS",
        "fallback_fs_div": "OFS", "comparison_scope": "consolidated",
        "capex_applicable": True, "valid_from": "2023-01-01", "valid_to": None,
    } for row in rows]


def _row(candidate: Candidate, metric_code: str, value: int, reasons: list[str]) -> dict:
    value_type = "annual" if candidate.report_type == "FY" else ("quarterly" if candidate.report_type == "Q1" else "cumulative")
    ratio = metric_code == "RND_TO_REVENUE"
    sources = {
        "extractor": EXTRACTOR_VERSION,
        "locator": {"source_path": candidate.source_file, "section": candidate.section_name,
                    "subsection": candidate.subsection_name, "chunk_id": candidate.chunk_id},
        "presented": {"value": candidate.presented_value, "unit": candidate.unit,
                      "scale": UNIT_SCALE[candidate.unit]},
        "basis": {"fs_div": candidate.fs_div, "scope": candidate.scope,
                  "total_includes_disclosed_components": list(candidate.components)},
        "components_as_presented": candidate.components,
        "disclosed_ratio_percent": candidate.ratio_percent,
        "selection_score": candidate.score,
        "review_reasons": reasons,
        "excerpt": candidate.excerpt,
    }
    return {
        "corp_code": candidate.corp_code, "year": candidate.year, "report_type": candidate.report_type,
        "metric_code": metric_code, "value": value, "unit": "BPS" if ratio else "KRW",
        "value_type": "ratio" if ratio else value_type, "fs_div": candidate.fs_div,
        "scope": candidate.scope, "source_kind": "DOCUMENT_EXTRACTED",
        "source_type": "dart_business_report_rnd_table", "source_rcept_no": candidate.rcept_no,
        "source_document_id": candidate.source_document_id,
        "source_chunk_id": candidate.chunk_id,
        "source_account_id": None,
        "source_account_name": "연구개발비 / 매출액 비율" if ratio else "연구개발비용 합계",
        "source_fact_key": None,
        "calculation_method": ("disclosed_percent_x100_to_bps" if ratio else
                               f"document_table_value_x{UNIT_SCALE[candidate.unit]}"),
        "source_values": json.dumps(sources, ensure_ascii=False),
        "review_status": "approved" if not reasons else "review_required",
    }


def _revenue(financial: sqlite3.Connection, candidate: Candidate) -> int | None:
    value_type = "annual" if candidate.report_type == "FY" else ("quarterly" if candidate.report_type == "Q1" else "cumulative")
    values = {int(row[0]) for row in financial.execute(
        """SELECT f.normalized_value FROM financial_facts f JOIN periods p USING(period_id)
           WHERE f.corp_code=? AND p.year=? AND p.report_type=? AND f.fs_div=?
             AND f.sj_div IN ('IS','CIS') AND f.value_type=? AND f.is_calculated=0
             AND f.normalized_value IS NOT NULL
             AND (f.account_id='ifrs-full_Revenue' OR f.standard_account_nm='매출액')""",
        (candidate.corp_code, candidate.year, candidate.report_type, candidate.fs_div, value_type),
    )}
    positive = sorted(value for value in values if value > 0)
    return positive[-1] if positive else None


def build_rows(
    financial: sqlite3.Connection,
    bge: sqlite3.Connection,
    years: tuple[int, ...] = YEARS,
    reports: tuple[str, ...] = REPORTS,
) -> tuple[list[dict], list[dict]]:
    financial.row_factory = bge.row_factory = sqlite3.Row
    health_codes = {row[0] for row in financial.execute(
        "SELECT corp_code FROM krx_index_constituents WHERE index_code='KRX_HEALTH' AND is_current=1")}
    grouped: dict[tuple, list[Candidate]] = defaultdict(list)
    bge_columns = {row[1] for row in bge.execute("PRAGMA table_info(filing_chunks)")}
    document_column = "source_document_id" if "source_document_id" in bge_columns else "NULL AS source_document_id"
    query = f"""SELECT chunk_id,corp_name,corp_code,year,report_type,rcept_no,section_name,
                      subsection_name,source_file,{document_column},original_text
               FROM filing_chunks
               WHERE year IN ({','.join('?' for _ in years)})
                 AND report_type IN ({','.join('?' for _ in reports)})
               ORDER BY corp_code,year,report_type,rcept_no,chunk_id"""
    raw_rows = [dict(row) for row in bge.execute(query, (*years, *reports))
                if row["corp_code"] in health_codes]
    by_section: dict[tuple, list[dict]] = defaultdict(list)
    for row in raw_rows:
        by_section[(row["rcept_no"], row["section_name"], row["subsection_name"])].append(row)
    filing_rows = []
    for section_rows in by_section.values():
        section_rows.sort(key=lambda row: tuple(int(value) for value in row["chunk_id"].rsplit(":", 2)[-2:]))
        for index, row in enumerate(section_rows):
            neighbours = section_rows[max(0, index - 1):index + 2]
            combined = "\n".join(item["original_text"] for item in neighbours)
            if "연구개발비용" in combined:
                filing_rows.append({**row, "original_text": combined})
    connected_rcepts = {
        row["rcept_no"] for row in filing_rows
        if "연구개발활동" in (row["subsection_name"] or "")
        and re.search(r"K-?IFRS\s*연결기준으로\s*작성", row["original_text"], re.I)
    }
    for row in filing_rows:
        if row["corp_code"] not in health_codes:
            continue
        for candidate in extract_candidates(row):
            # Overlapping chunks can split the table unit/header from the
            # trailing K-IFRS scope footnote.  Join that evidence by receipt.
            if candidate.scope == "unspecified" and candidate.rcept_no in connected_rcepts:
                candidate = replace(
                    candidate, fs_div="CFS", scope="consolidated", score=candidate.score + 100,
                )
            grouped[(candidate.corp_code, candidate.year, candidate.report_type)].append(candidate)
    rows, summaries = [], []
    companies = list(financial.execute(
        """SELECT c.corp_code,c.company_name FROM companies c JOIN krx_index_constituents k USING(corp_code)
           WHERE k.index_code='KRX_HEALTH' AND k.is_current=1 ORDER BY c.company_name"""))
    for company in companies:
        for year in years:
            for report_type in reports:
                candidates = grouped.get((company["corp_code"], year, report_type), [])
                chosen, reasons = select_candidate(candidates)
                summary = {"corp_code": company["corp_code"], "company_name": company["company_name"],
                           "year": year, "report_type": report_type, "candidate_count": len(candidates),
                           "result": "MISSING" if chosen is None else ("APPROVED" if not reasons else "REVIEW_REQUIRED"),
                           "issues": reasons}
                if chosen is None:
                    summaries.append(summary)
                    continue
                rows.append(_row(chosen, "RND_EXPENSE", chosen.value, reasons))
                if chosen.ratio_percent is not None:
                    ratio_value = int(round(float(chosen.ratio_percent) * 100))
                    ratio_reasons = list(reasons)
                    revenue = _revenue(financial, chosen)
                    if revenue:
                        calculated = chosen.value / revenue * 100
                        if abs(calculated - float(chosen.ratio_percent)) > 0.25:
                            ratio_reasons.append("disclosed ratio differs from R&D/revenue recalculation by >0.25%p")
                    ratio_row = _row(chosen, "RND_TO_REVENUE", ratio_value, ratio_reasons)
                    source_values = json.loads(ratio_row["source_values"])
                    source_values["ratio_check"] = {"revenue": revenue, "tolerance_percentage_point": 0.25}
                    ratio_row["source_values"] = json.dumps(source_values, ensure_ascii=False)
                    rows.append(ratio_row)
                else:
                    revenue = _revenue(financial, chosen)
                    if revenue:
                        ratio_value = int(round(chosen.value / revenue * 10_000))
                        ratio_row = _row(chosen, "RND_TO_REVENUE", ratio_value, reasons)
                        ratio_row["source_type"] = "dart_rnd_table_plus_financial_fact"
                        ratio_row["source_account_name"] = "연구개발비용 합계 / 매출액"
                        ratio_row["calculation_method"] = "RND_EXPENSE / REVENUE x 10000 BPS"
                        source_values = json.loads(ratio_row["source_values"])
                        source_values["ratio_derivation"] = {"rnd_expense": chosen.value, "revenue": revenue}
                        ratio_row["source_values"] = json.dumps(source_values, ensure_ascii=False)
                        rows.append(ratio_row)
                summary.update(value=chosen.value, ratio_percent=chosen.ratio_percent,
                               fs_div=chosen.fs_div, scope=chosen.scope,
                               rcept_no=chosen.rcept_no, chunk_id=chosen.chunk_id)
                summaries.append(summary)
    return rows, summaries


def apply_healthcare_rnd(
    connection: sqlite3.Connection,
    bge_path: Path = BGE_DB_PATH,
    years: tuple[int, ...] = YEARS,
    reports: tuple[str, ...] = REPORTS,
) -> dict:
    connection.row_factory = sqlite3.Row
    with connection:
        initialize_financial_metric_schema(connection, include_general=True)
        upsert_company_profiles(connection, _profiles(connection))
    before = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    with closing(sqlite3.connect(f"file:{bge_path.resolve()}?mode=ro", uri=True)) as bge:
        rows, summaries = build_rows(connection, bge, years, reports)
    with connection:
        health_codes = [row[0] for row in connection.execute(
            "SELECT corp_code FROM krx_index_constituents WHERE index_code='KRX_HEALTH' AND is_current=1")]
        if health_codes:
            connection.execute(
                f"""DELETE FROM financial_metric_values
                    WHERE corp_code IN ({','.join('?' for _ in health_codes)})
                      AND year IN ({','.join('?' for _ in years)})
                      AND report_type IN ({','.join('?' for _ in reports)})
                      AND metric_code IN ('RND_EXPENSE','RND_TO_REVENUE')
                      AND source_kind='DOCUMENT_EXTRACTED'""",
                (*health_codes, *years, *reports),
            )
        upsert_metric_values(connection, rows)
    after = connection.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0]
    duplicates = connection.execute(
        """SELECT COUNT(*) FROM (
             SELECT corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind
             FROM financial_metric_values WHERE metric_code IN ('RND_EXPENSE','RND_TO_REVENUE')
             GROUP BY 1,2,3,4,5,6,7,8 HAVING COUNT(*)>1)"""
    ).fetchone()[0]
    coverage = {code: connection.execute(
        "SELECT COUNT(*) FROM financial_metric_values WHERE metric_code=?", (code,)
    ).fetchone()[0] for code in ("RND_EXPENSE", "RND_TO_REVENUE")}
    validation = {
        "expected_slots": 5 * len(years) * len(reports), "coverage": coverage,
        "period_coverage": {code: connection.execute(
            f"SELECT COUNT(*) FROM financial_metric_values WHERE metric_code=? "
            f"AND year IN ({','.join('?' for _ in years)}) "
            f"AND report_type IN ({','.join('?' for _ in reports)})",
            (code, *years, *reports),
        ).fetchone()[0] for code in ("RND_EXPENSE", "RND_TO_REVENUE")},
        "duplicate_natural_keys": duplicates,
        "approved_slots": sum(item["result"] == "APPROVED" for item in summaries),
        "review_required_slots": sum(item["result"] == "REVIEW_REQUIRED" for item in summaries),
        "missing_slots": sum(item["result"] == "MISSING" for item in summaries),
    }
    return {"document_rows": len(rows), "metric_values_before": before,
            "metric_values_after": after, "validation": validation, "summaries": summaries}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--bge", type=Path, default=BGE_DB_PATH)
    parser.add_argument("--apply-only", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--years", nargs="+", type=int, default=list(YEARS))
    parser.add_argument("--reports", nargs="+", choices=list(REPORTS), default=list(REPORTS))
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
    with closing(sqlite3.connect(args.target)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        result = apply_healthcare_rnd(connection, args.bge, tuple(args.years), tuple(args.reports))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "summaries"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
