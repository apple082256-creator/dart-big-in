"""Evidence lookups for the residual quality review (read-only).

Two independent sources are used to judge a pair of cumulative values that
the automatic quarter rules could not settle:

* the filed statement (DART XML): the sign with which the very amount is
  presented in the statement row of each report, so a presentation flip in
  the API (same printed sign, opposite API sign) is told apart from a real
  reversal (printed signs differ as well);
* next-year comparatives (OpenDART API): the same company restating both
  periods under one line label, tag and sign convention, which also shows
  whether two differently named lines are the same line.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

from src.config import PROJECT_ROOT, RAW_DIR
from src.extract.bank_statement_xml import (
    SECTION_BY_FS_DIV,
    UNIT_SCALE,
    _UNIT,
    _blocks,
    _compact,
    _sections,
    line_name,
    parse_amount,
)
from src.preprocess.period_normalizer import is_cf_balance_line

BGE_DB_PATH = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"
STATEMENT_KEYS = (("BS", ("재무상태표", "대차대조표")), ("IS", ("손익계산서",)), ("CF", ("현금흐름표",)))
_PAREN = re.compile(r"\([^)]*\)")


OFFICIAL_PREFIXES = ("ifrs-full_", "ifrs_", "dart_")


def statement_group(sj_div: str) -> str:
    return "IS" if sj_div in ("IS", "CIS") else sj_div


def _name_keys(name: str) -> set[str]:
    base = line_name(name)
    return {base, _PAREN.sub("", base)}


# Filings not indexed in the vector DB yet (e.g. a new period in staging):
# (corp_code, year, report_type) -> path of the main XML document.
EXTRA_DOCUMENTS: dict[tuple[str, int, str], str] = {}


def register_documents(documents: dict[tuple[str, int, str], str]) -> None:
    """Make filed statements outside the vector DB available to the evidence checks."""
    EXTRA_DOCUMENTS.update(documents)
    _document_path.cache_clear()
    _statement_rows.cache_clear()
    presentation_style_changed.cache_clear()


@lru_cache(maxsize=None)
def _document_path(corp_code: str, year: int, report_type: str) -> str | None:
    if (corp_code, int(year), report_type) in EXTRA_DOCUMENTS:
        return EXTRA_DOCUMENTS[(corp_code, int(year), report_type)]
    with closing(sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True)) as bge:
        files = [r[0] for r in bge.execute(
            "SELECT DISTINCT source_file FROM filing_chunks WHERE corp_code=? AND year=? AND report_type=? "
            "ORDER BY length(source_file), source_file", (corp_code, year, report_type))]
    return files[0] if files else None


@lru_cache(maxsize=64)
def _statement_rows(corp_code: str, year: int, report_type: str, fs_div: str) -> tuple:
    """(statement, normalized-name keys, amounts, unit) for every statement table row."""
    path = _document_path(corp_code, year, report_type)
    if path is None:
        return ()
    raw = (PROJECT_ROOT / path).read_text(encoding="utf-8", errors="replace")
    section = _sections(raw, coded_only=True).get(SECTION_BY_FS_DIV[fs_div])
    if section is None:
        return ()
    rows, statement, unit = [], None, None
    for kind, value, _ in _blocks(raw, *section):
        text = _compact(value if kind == "p" else " ".join(c["text"] for r in value for c in r[:3]))
        header_like = kind == "p" or len(value) <= 6
        if header_like:
            for code, keys in STATEMENT_KEYS:
                if any(k in text for k in keys) and "자본변동" not in text:
                    statement = code
            found = _UNIT.search(text)
            unit = found.group(1) if found else unit
            if kind == "p":
                continue
        if statement is None:
            continue
        for row in value:
            if len(row) < 2:
                continue
            amounts = tuple(a for a in (parse_amount(c["text"]) for c in row[1:]) if a is not None)
            if amounts:
                rows.append((statement, frozenset(_name_keys(row[0]["text"])), amounts, unit))
    return tuple(rows)


def presented_sign(corp_code: str, year: int, report_type: str, fs_div: str, sj_div: str,
                   account_name: str, value: int) -> str | None:
    """'+' or '-' as the amount |value| is printed in the matching statement row; None if not found."""
    if not value:
        return None
    keys = _name_keys(account_name)
    group = statement_group(sj_div)
    signs = set()
    for statement, names, amounts, unit in _statement_rows(corp_code, year, report_type, fs_div):
        if statement != group or not (names & keys):
            continue
        scales = (UNIT_SCALE[unit],) if unit in UNIT_SCALE else (1, 1_000, 1_000_000)
        for amount in amounts:
            if any(abs(amount) * scale == abs(value) for scale in scales):
                signs.add("-" if amount < 0 else "+")
    return signs.pop() if len(signs) == 1 else None


@lru_cache(maxsize=None)
def _api_items(corp_code: str, year: int, report_type: str, fs_div: str) -> tuple:
    path = RAW_DIR / "financial" / corp_code / str(year) / f"{report_type}_{fs_div}.json"
    if not path.is_file():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")).get("list") or [])


def _to_int(text: object) -> int | None:
    if text in (None, ""):
        return None
    try:
        return int(str(text).replace(",", ""))
    except ValueError:
        return None


def next_year_comparative(corp_code: str, year: int, report_type: str, fs_div: str, sj_div: str,
                          account_id: str, account_names: tuple[str, ...]) -> dict | None:
    """The year's cumulative/annual value as restated in the year+1 report of the same type.

    Matched by official account id when available, else by exact line name;
    the match must be unique within the statement.
    """
    group = statement_group(sj_div)
    keys = set().union(*(_name_keys(n) for n in account_names))
    items = [i for i in _api_items(corp_code, year + 1, report_type, fs_div)
             if statement_group(i.get("sj_div", "")) == group]
    official = account_id.startswith(("ifrs-full_", "ifrs_", "dart_"))
    hits = [i for i in items if official and i.get("account_id") == account_id]
    if len(hits) != 1:
        hits = [i for i in items if _name_keys(i.get("account_nm", "")) & keys]
    if len(hits) != 1:
        return None
    item = hits[0]
    field = "frmtrm_add_amount" if group == "IS" and report_type != "FY" else "frmtrm_amount"
    value = _to_int(item.get(field))
    if value is None and field == "frmtrm_add_amount":
        value = _to_int(item.get("frmtrm_q_amount")) if report_type == "Q1" else None
    if value is None:
        return None
    return {"value": value, "account_id": item.get("account_id"), "account_nm": item.get("account_nm"),
            "rcept_no": item.get("rcept_no")}


def judge_pair(corp_code: str, year: int, fs_div: str, sj_div: str, account_id: str,
               current: dict, previous: dict, need_same_line: bool) -> dict:
    """Judge two cumulative values (current report, earlier report) of one line.

    ``current``/``previous``: {'report_type', 'value', 'name'}.  Returns the
    verdict, the evidence, and — when derivable — the quarter value in the
    current report's sign convention.
    """
    now, before = current["value"], previous["value"]
    conflict = bool(now and before and (now > 0) != (before > 0))
    evidence: dict = {}
    if not need_same_line and conflict:
        doc_now = presented_sign(corp_code, year, current["report_type"], fs_div, sj_div, current["name"], now)
        doc_before = presented_sign(corp_code, year, previous["report_type"], fs_div, sj_div,
                                    previous["name"], before)
        evidence["document_signs"] = [doc_now, doc_before]
        if doc_now and doc_before:
            if doc_now == doc_before:
                return {"verdict": "presentation_flip", "basis": "document", "evidence": evidence,
                        "quarter": now + before}
            return {"verdict": "real_reversal", "basis": "document", "evidence": evidence,
                    "quarter": now - before}
    names = (current["name"], previous["name"])
    comp_now = next_year_comparative(corp_code, year, current["report_type"], fs_div, sj_div, account_id, names)
    comp_before = next_year_comparative(corp_code, year, previous["report_type"], fs_div, sj_div,
                                        account_id, names)
    evidence["next_year"] = [comp_now, comp_before]
    if comp_now and comp_before and abs(comp_now["value"]) == abs(now) and abs(comp_before["value"]) == abs(before):
        same_official_tag = comp_now["account_id"] == comp_before["account_id"] and \
            comp_now["account_id"].startswith(OFFICIAL_PREFIXES)
        if need_same_line and not same_official_tag and \
                (comp_now["account_id"], line_name(comp_now["account_nm"])) != \
                (comp_before["account_id"], line_name(comp_before["account_nm"])):
            return {"verdict": "unproven", "basis": "next_year_label_differs", "evidence": evidence}
        same_sign_restated = (comp_now["value"] > 0) == (comp_before["value"] > 0) \
            if comp_now["value"] and comp_before["value"] else not conflict
        if conflict and same_sign_restated:
            return {"verdict": "presentation_flip", "basis": "next_year", "evidence": evidence,
                    "quarter": now + before}
        if conflict:
            return {"verdict": "real_reversal", "basis": "next_year", "evidence": evidence,
                    "quarter": now - before}
        return {"verdict": "same_line", "basis": "next_year", "evidence": evidence, "quarter": now - before}
    if comp_now and comp_before:
        return {"verdict": "restated", "basis": "next_year_values_differ", "evidence": evidence}
    return {"verdict": "unproven", "basis": "no_evidence", "evidence": evidence}


VERDICT = {
    # stored calculated quarters (A)
    ("stored", "presentation_flip"): "CONFIRMED_ERROR",
    ("stored", "real_reversal"): "VERIFIED",
    # quarters held back by the automatic rules (B): verified ones are derivable
    ("held", "presentation_flip"): "VERIFIED",
    ("held", "real_reversal"): "VERIFIED",
    ("held", "same_line"): "VERIFIED",
}


def _impact_tags(db: sqlite3.Connection) -> dict[str, set[str]]:
    kpi = {r[0] for r in db.execute("SELECT DISTINCT account_id FROM financial_metric_mappings")}
    from src.db.industry_kpi_config import GENERAL_MAPPINGS
    kpi |= {m[1] for m in GENERAL_MAPPINGS}
    capex = set()
    for corp, names in db.execute("SELECT DISTINCT corp_code, source_account_names FROM capex_facts"):
        capex |= {(corp, line_name(n)) for n in json.loads(names or "[]")}
    return {"kpi": kpi, "capex": capex}


def _impact(tags: dict, corp_code: str, account_id: str | None, name: str | None) -> str:
    if account_id in tags["kpi"]:
        return "KPI"
    if (corp_code, line_name(name or "")) in tags["capex"]:
        return "CAPEX"
    return "facts_only"


def classify_stored_conflicts(db: sqlite3.Connection) -> list[dict]:
    tags = _impact_tags(db)
    rows = db.execute(
        """SELECT f.fact_id, f.corp_code, p.year, p.report_type, f.fs_div, f.sj_div, f.account_id,
                  f.account_key, f.original_account_nm, f.calculated_value, f.source_values
           FROM financial_facts f JOIN periods p USING(period_id) WHERE f.is_calculated=1""").fetchall()
    previous_of = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
    results = []
    for fact_id, corp, year, report, fs, sj, account_id, key, name, stored, sources in rows:
        inputs = json.loads(sources or "{}")
        if inputs.get("sign_note") == "sign_aligned" or report not in previous_of:
            continue
        prev_report = previous_of[report]
        now, before = inputs.get(report), inputs.get(prev_report)
        if not isinstance(now, int) or not isinstance(before, int) or not now or not before \
                or (now > 0) == (before > 0):
            continue
        prev_name = db.execute(
            """SELECT original_account_nm FROM financial_facts f JOIN periods p USING(period_id)
               WHERE f.corp_code=? AND p.year=? AND p.report_type=? AND f.fs_div=? AND f.sj_div=?
                 AND f.account_key=? AND f.is_calculated=0 AND COALESCE(f.normalized_value,0)=?""",
            (corp, year, prev_report, fs, sj, key, before)).fetchone()
        judged = judge_pair(corp, year, fs, sj, account_id or "",
                            {"report_type": report, "value": now, "name": name},
                            {"report_type": prev_report, "value": before,
                             "name": prev_name[0] if prev_name else name}, need_same_line=False)
        verdict = VERDICT.get(("stored", judged["verdict"]),
                              "REVIEW_REQUIRED" if judged["verdict"] == "restated" else "NOT_VERIFIABLE")
        results.append({"set": "A_stored_sign_conflict", "fact_id": fact_id, "corp_code": corp, "year": year,
                        "report_type": report, "fs_div": fs, "sj_div": sj, "account_id": account_id,
                        "account_key": key, "account_name": name, "stored": stored, "inputs": [now, before],
                        "verdict": verdict, "finding": judged["verdict"], "basis": judged["basis"],
                        "corrected": judged.get("quarter") if verdict == "CONFIRMED_ERROR" else None,
                        "evidence": judged["evidence"],
                        "impact": _impact(tags, corp, account_id, name)})
    return results


def classify_held_quarters(db: sqlite3.Connection, review: list[dict]) -> list[dict]:
    tags = _impact_tags(db)
    previous_of = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
    results = []
    for item in review:
        corp, year, report = item["corp_code"], int(item["year"]), item["report_type"]
        prev_report = previous_of[report]
        def rows(rep):
            return db.execute(
                """SELECT f.account_id, f.original_account_nm, f.normalized_value FROM financial_facts f
                   JOIN periods p USING(period_id)
                   WHERE f.corp_code=? AND p.year=? AND p.report_type=? AND f.fs_div=? AND f.sj_div=?
                     AND f.account_key=? AND f.is_calculated=0 AND f.value_type IN ('cumulative','annual')""",
                (corp, year, rep, item["fs_div"], item["sj_div"], item["account_key"])).fetchall()
        current, previous = rows(report), rows(prev_report)
        base = {"set": "B_held_quarter", "corp_code": corp, "year": year, "report_type": report,
                "fs_div": item["fs_div"], "sj_div": item["sj_div"], "account_key": item["account_key"],
                "account_name": item.get("account_name"), "held_reason": item["reason"]}
        if len(current) != 1 or len(previous) != 1:
            results.append({**base, "verdict": "NOT_VERIFIABLE", "finding": "ambiguous_rows",
                            "basis": f"{len(current)}/{len(previous)} rows"})
            continue
        (account_id, name, now), (_, prev_name, before) = current[0], previous[0]
        judged = judge_pair(corp, year, item["fs_div"], item["sj_div"], account_id or "",
                            {"report_type": report, "value": now, "name": name},
                            {"report_type": prev_report, "value": before, "name": prev_name},
                            need_same_line=item["reason"] != "sign_conflict")
        verdict = VERDICT.get(("held", judged["verdict"]),
                              "REVIEW_REQUIRED" if judged["verdict"] == "restated"
                              or judged["basis"] == "next_year_label_differs" else "NOT_VERIFIABLE")
        results.append({**base, "account_id": account_id, "previous_account_name": prev_name,
                        "inputs": [now, before], "verdict": verdict, "finding": judged["verdict"],
                        "basis": judged["basis"], "derivable_quarter": judged.get("quarter")
                        if verdict == "VERIFIED" else None, "evidence": judged["evidence"],
                        "impact": _impact(tags, corp, account_id, name)})
    return results


def classify_quarter_gaps(db: sqlite3.Connection, gaps: list[dict]) -> list[dict]:
    """API quarter != cumulative difference: are the stored quarter and cumulative the filed figures?"""
    results = []
    for gap in gaps:
        corp, year, fs, sj, key, _ = gap["group"]
        report, name = gap["report"], gap["account"] or ""
        prior_key = next(k for k in gap if k.endswith("_cumulative"))
        base = {"set": "C_quarter_gap", "corp_code": corp, "year": year, "report_type": report, "fs_div": fs,
                "sj_div": sj, "account_key": key, "account_name": name, "quarter": gap["quarter"],
                "cumulative": gap["cumulative"], prior_key: gap[prior_key]}
        if "주당" in name:
            results.append({**base, "verdict": "VERIFIED", "finding": "per_share_not_additive",
                            "basis": "definition"})
            continue
        quarter_doc = presented_sign(corp, year, report, fs, sj, name, gap["quarter"])
        cumulative_doc = presented_sign(corp, year, report, fs, sj, name, gap["cumulative"])
        if quarter_doc and cumulative_doc:
            results.append({**base, "verdict": "VERIFIED", "finding": "stored_values_match_filing",
                            "basis": "document", "document_signs": [quarter_doc, cumulative_doc]})
        else:
            results.append({**base, "verdict": "NOT_VERIFIABLE", "finding": "values_not_located",
                            "basis": "document", "document_signs": [quarter_doc, cumulative_doc]})
    return results


def classify_mappings(db: sqlite3.Connection) -> list[dict]:
    results = []
    for key, sj, account_id, name, count in db.execute(
            """SELECT account_key, sj_div, account_id, standard_account_name, observation_count
               FROM account_mapping WHERE mapping_status='review_required'"""):
        official = (account_id or "").startswith(("ifrs-full_", "ifrs_", "dart_"))
        results.append({"set": "D_mapping", "account_key": key, "sj_div": sj, "account_id": account_id,
                        "standard_account_name": name, "observations": count,
                        "verdict": "VERIFIED" if official else "REVIEW_REQUIRED",
                        "finding": "identified_by_official_tag" if official else "name_based_mapping",
                        "impact": "label_only"})
    return results


def _raw_rows(db: sqlite3.Connection, corp: str, year: int, report: str, fs: str, sj: str, key: str) -> list:
    return db.execute(
        """SELECT f.original_account_nm, COALESCE(f.normalized_value, 0), f.rcept_no, f.ord
           FROM financial_facts f JOIN periods p USING(period_id)
           WHERE f.corp_code=? AND p.year=? AND p.report_type=? AND f.fs_div=? AND f.sj_div=?
             AND f.account_key=? AND f.is_calculated=0 AND f.value_type IN ('cumulative','annual')""",
        (corp, year, report, fs, sj, key)).fetchall()


def classify_held_by_documents(db: sqlite3.Connection, records: list[dict]) -> list[dict]:
    """Second pass over held quarters that had no next-year evidence.

    Derivable only when each report holds exactly one cumulative row for the
    key and both cumulatives are found in the filed statements; the quarter is
    taken in filed signs and expressed in the current report's API convention.
    Restated pairs (next-year comparatives differ) stay in review.
    """
    previous_report = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
    results = []
    for record in records:
        r = dict(record)
        corp, year, report, fs, sj, key = (r["corp_code"], r["year"], r["report_type"], r["fs_div"],
                                           r["sj_div"], r["account_key"])
        if sj == "CF" and is_cf_balance_line(r.get("account_id"), r.get("account_name")):
            results.append({**r, "category": "not_a_flow_balance_line", "final": "EXCLUDED"})
            continue
        if "주당" in (r.get("account_name") or "") or "PerShare" in (r.get("account_id") or ""):
            results.append({**r, "category": "per_share_not_additive", "final": "EXCLUDED"})
            continue
        if r["verdict"] == "VERIFIED":
            if r.get("basis") == "document" and r["finding"] in ("real_reversal", "presentation_flip") and \
                    presentation_style_changed(corp, year, report, previous_report[report], fs, sj) is not False:
                results.append({**r, "category": "sign_unproven_style", "final": "REVIEW_REQUIRED"})
                continue
            results.append({**r, "category": f"verified_{r['finding']}", "final": "DERIVABLE"})
            continue
        if r["finding"] == "restated":
            results.append({**r, "category": "restated_basis_differs", "final": "REVIEW_REQUIRED"})
            continue
        prev = previous_report[report]
        now_rows = _raw_rows(db, corp, year, report, fs, sj, key)
        prev_rows = _raw_rows(db, corp, year, prev, fs, sj, key)
        if len(now_rows) != 1 or len(prev_rows) != 1:
            results.append({**r, "category": "ambiguous_rows", "final": "NOT_VERIFIABLE",
                            "rows": [len(now_rows), len(prev_rows)]})
            continue
        (now_name, now, now_rcept, _), (prev_name, before, prev_rcept, _) = now_rows[0], prev_rows[0]
        doc_now = presented_sign(corp, year, report, fs, sj, now_name, now)
        doc_prev = presented_sign(corp, year, prev, fs, sj, prev_name, before)
        evidence = {"document_signs": [doc_now, doc_prev], "rcept_nos": [now_rcept, prev_rcept]}
        if not (doc_now and doc_prev):
            category = "sign_unproven" if r["held_reason"] == "sign_conflict" else "no_filed_evidence"
            results.append({**r, "category": category, "final": "NOT_VERIFIABLE", "doc_evidence": evidence})
            continue
        conflict = bool(now and before and (now > 0) != (before > 0))
        if not conflict:
            # Same API sign: the API relation holds (issuers that switch how they
            # print expenses between reports flip the printed sign, not the API one).
            results.append({**r, "category": "document_same_line", "final": "DERIVABLE",
                            "derivable_quarter": now - before, "basis": "document_located",
                            "doc_evidence": evidence})
            continue
        style = presentation_style_changed(corp, year, report, prev, fs, sj)
        evidence["presentation_style_changed"] = style
        if style is not False:
            results.append({**r, "category": "sign_unproven_style", "final": "REVIEW_REQUIRED",
                            "doc_evidence": evidence})
            continue
        flip = doc_now == doc_prev
        results.append({**r, "category": "document_presentation_flip" if flip else "document_real_reversal",
                        "final": "DERIVABLE", "derivable_quarter": now + before if flip else now - before,
                        "basis": "document", "doc_evidence": evidence})
    return results


@lru_cache(maxsize=None)
def presentation_flip_ratio(corp_code: str, year: int, report_now: str, report_prev: str, fs_div: str,
                            sj_div: str) -> tuple[float, int]:
    """Share of the lines common to two filed statements whose printed sign differs.

    Issuers sometimes switch between printing expenses as positive amounts and
    as deductions in parentheses from one report to the next; then many lines
    flip at once and a sign difference of a single line proves nothing.
    """
    group = statement_group(sj_div)

    def signs(report: str) -> dict:
        out = {}
        for statement, names, amounts, _ in _statement_rows(corp_code, year, report, fs_div):
            if statement == group and amounts and amounts[0] and len(names) == 1:
                out.setdefault(next(iter(names)), "-" if amounts[0] < 0 else "+")
        return out

    now, prev = signs(report_now), signs(report_prev)
    common = [k for k in now if k in prev]
    if not common:
        return (1.0, 0)
    return (sum(now[k] != prev[k] for k in common) / len(common), len(common))


STYLE_REFERENCES = {
    "IS": ("판매비와관리비", "판매비와일반관리비", "관리비및판매비", "매출원가", "일반관리비", "영업비용"),
    "CF": ("유형자산의취득", "배당금의지급", "배당금지급"),
}


@lru_cache(maxsize=None)
def presentation_style_changed(corp_code: str, year: int, report_now: str, report_prev: str, fs_div: str,
                               sj_div: str) -> bool | None:
    """Did the issuer change how it prints expenses/outflows between the two reports?

    Reference lines never change economic sign (cost of sales, SG&A, PP&E
    purchases, dividends paid); if one of them is printed with the opposite
    sign in the two reports, printed-sign comparisons between them are not
    evidence.  None when no reference line is found in both.
    """
    group = statement_group(sj_div)

    def reference_signs(report: str) -> dict:
        out = {}
        for statement, names, amounts, _ in _statement_rows(corp_code, year, report, fs_div):
            if statement != group or not amounts or not amounts[0]:
                continue
            for name in names:
                for reference in STYLE_REFERENCES.get(group, ()):
                    if name == reference:
                        out.setdefault(reference, "-" if amounts[0] < 0 else "+")
        return out

    now, prev = reference_signs(report_now), reference_signs(report_prev)
    common = [k for k in now if k in prev]
    if not common:
        return None
    return any(now[k] != prev[k] for k in common)


def build_quarter_manifest(db: sqlite3.Connection, results: list[dict]) -> list[dict]:
    """Pinned inputs for every derivable held quarter (``verified_quarter_overrides``)."""
    previous_report = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
    entries = []
    for r in results:
        if r["final"] != "DERIVABLE":
            continue
        corp, year, report, fs, sj, key = (r["corp_code"], r["year"], r["report_type"], r["fs_div"],
                                           r["sj_div"], r["account_key"])
        now_rows = _raw_rows(db, corp, year, report, fs, sj, key)
        prev_rows = _raw_rows(db, corp, year, previous_report[report], fs, sj, key)
        if len(now_rows) != 1 or len(prev_rows) != 1:
            continue  # the normalizer only derives unique pairs
        (now_name, now, now_rcept, _), (prev_name, before, prev_rcept, _) = now_rows[0], prev_rows[0]
        quarter = int(r["derivable_quarter"])
        if quarter not in (now - before, now + before) or (now - before == now + before and before):
            raise ValueError(f"unexpected quarter for {r}")
        aligned = quarter != now - before
        entries.append({
            "corp_code": corp, "year": year, "report_type": report, "fs_div": fs, "sj_div": sj,
            "account_key": key, "account_name": now_name, "previous_account_name": prev_name,
            "current_value": now, "previous_value": before, "quarter": quarter, "sign_aligned": aligned,
            "held_reason": r["held_reason"], "category": r["category"], "basis": r.get("basis"),
            "rcept_nos": [now_rcept, prev_rcept],
            "evidence": r.get("doc_evidence") or r.get("evidence"),
        })
    return entries
