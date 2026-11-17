"""Read-only quality audit of the operational Financial DB (all companies).

Every check recomputes values from stored inputs or the preserved OpenDART
responses; nothing is written to the database.  Findings are reported with
counts and example rows so that corrections can be planned and staged.

Usage: python -m src.quality.financial_db_audit [--db PATH] [--json OUT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from src.config import FINANCIAL_DB_PATH, PROJECT_ROOT, RAW_DIR
from src.preprocess.financial_cleaner import parse_amount
from src.db import expense_sign
from src.db.expense_sign import KEY as EXPENSE_SIGN_KEY
from src.preprocess.period_normalizer import VERIFIED_SIGN_ALIGNMENTS

EXAMPLES = 8
BGE_DB_PATH = PROJECT_ROOT / "db" / "vector" / "filings_bge_m3.db"


@lru_cache(maxsize=None)
def _raw_items(corp_code: str, year: int, report_type: str, fs_div: str) -> tuple:
    path = RAW_DIR / "financial" / corp_code / str(year) / f"{report_type}_{fs_div}.json"
    if not path.is_file():
        return ()
    return tuple(json.loads(path.read_text(encoding="utf-8")).get("list") or [])


def _finding(name: str, checked: int, issues: list, note: str = "") -> dict:
    return {"check": name, "checked": checked, "issues": len(issues),
            "examples": issues[:EXAMPLES], **({"note": note} if note else {})}


def check_raw_values(db: sqlite3.Connection) -> dict:
    """Stored source values equal the preserved API response field they came from."""
    rows = db.execute(
        """SELECT f.fact_id,f.corp_code,p.year,p.report_type,f.fs_div,f.sj_div,f.account_id,
                  f.original_account_nm,f.ord,f.source_value_field,f.raw_value,f.normalized_value
           FROM financial_facts f JOIN periods p USING(period_id) WHERE f.is_calculated=0"""
    ).fetchall()
    issues, missing_file, checked = [], 0, 0
    for row in rows:
        items = _raw_items(row[1], row[2], row[3], row[4])
        if not items:
            missing_file += 1
            continue
        candidates = [i for i in items if i.get("sj_div") == row[5] and i.get("account_id") == row[6]
                      and i.get("account_nm") == row[7] and str(i.get("ord")) == str(row[8])]
        checked += 1
        values = {parse_amount(i.get(row[9])) for i in candidates}
        if row[11] not in values:
            issues.append({"fact_id": row[0], "corp_code": row[1], "period": f"{row[2]} {row[3]}",
                           "account": row[7], "field": row[9], "stored": row[11],
                           "raw": sorted(v for v in values if v is not None)[:3] or None})
    return _finding("stored source value = raw API field", checked, issues,
                    f"raw file missing for {missing_file} rows" if missing_file else "")


def check_units_and_duplicates(db: sqlite3.Connection) -> list[dict]:
    currencies = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(currency,'NULL') FROM financial_facts")]
    units = [r[0] for r in db.execute("SELECT DISTINCT COALESCE(unit,'NULL') FROM financial_metric_values")]
    dup_facts = db.execute(
        """SELECT corp_code,period_id,fs_div,sj_div,account_key,ord,value_type,is_calculated,COUNT(*)
           FROM financial_facts GROUP BY 1,2,3,4,5,6,7,8 HAVING COUNT(*)>1"""
    ).fetchall()
    dup_metrics = db.execute(
        """SELECT corp_code,year,report_type,metric_code,value_type,fs_div,scope,source_kind,COUNT(*)
           FROM financial_metric_values GROUP BY 1,2,3,4,5,6,7,8 HAVING COUNT(*)>1"""
    ).fetchall()
    return [
        _finding("single currency/unit (KRW; BPS for KPI ratios)", 2,
                 [c for c in currencies if c != "KRW"] + [u for u in units if u not in ("KRW", "BPS")]),
        _finding("no duplicate fact keys", db.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0],
                 [list(r) for r in dup_facts]),
        _finding("no duplicate metric keys",
                 db.execute("SELECT COUNT(*) FROM financial_metric_values").fetchone()[0],
                 [list(r) for r in dup_metrics]),
    ]


def check_statement_scope(db: sqlite3.Connection) -> dict:
    """One statement scope (CFS or OFS) per company; bank metrics follow the profile."""
    mixed = db.execute(
        """SELECT f.corp_code,c.company_name,GROUP_CONCAT(DISTINCT f.fs_div)
           FROM financial_facts f JOIN companies c USING(corp_code)
           GROUP BY 1 HAVING COUNT(DISTINCT f.fs_div)>1"""
    ).fetchall()
    profile = db.execute(
        """SELECT v.corp_code,v.fs_div,p.preferred_fs_div,COUNT(*) FROM financial_metric_values v
           JOIN company_financial_profiles p USING(corp_code)
           WHERE v.fs_div<>p.preferred_fs_div GROUP BY 1,2,3"""
    ).fetchall()
    return _finding("no CFS/OFS mixing per company",
                    db.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
                    [list(r) for r in mixed] + [["metric", *r] for r in profile])


def check_quarter_consistency(db: sqlite3.Connection) -> list[dict]:
    """Q1 quarter = Q1 cumulative; reported quarter = cumulative difference (IS/CIS)."""
    rows = db.execute(
        """SELECT f.corp_code,p.year,p.report_type,f.fs_div,f.sj_div,f.account_key,f.ord,f.value_type,
                  f.normalized_value,f.original_account_nm
           FROM financial_facts f JOIN periods p USING(period_id)
           WHERE f.is_calculated=0 AND f.sj_div IN ('IS','CIS') AND f.normalized_value IS NOT NULL"""
    ).fetchall()
    values: dict[tuple, dict[tuple, int]] = defaultdict(dict)
    names = {}
    for corp, year, report, fs, sj, key, ord_, value_type, value, name in rows:
        values[(corp, year, fs, sj, key, ord_)][(report, value_type)] = value
        names[(corp, year, fs, sj, key, ord_)] = name
    q1_issues, diff_issues, diff_sign_issues, checked_q1, checked_diff = [], [], [], 0, 0
    previous = {"H1": "Q1", "Q3": "H1"}
    for group, series in values.items():
        if ("Q1", "quarterly") in series and ("Q1", "cumulative") in series:
            checked_q1 += 1
            if series[("Q1", "quarterly")] != series[("Q1", "cumulative")]:
                q1_issues.append([*group, series[("Q1", "quarterly")], series[("Q1", "cumulative")]])
        for report, prior in previous.items():
            needed = [(report, "quarterly"), (report, "cumulative"), (prior, "cumulative")]
            if all(k in series for k in needed):
                checked_diff += 1
                quarter, cumulative, earlier = (series[k] for k in needed)
                if cumulative - earlier != quarter:
                    entry = {"group": list(group), "account": names[group], "report": report,
                             "quarter": quarter, "cumulative": cumulative, f"{prior}_cumulative": earlier}
                    if abs(cumulative) - abs(earlier) == abs(quarter) or cumulative + earlier == quarter:
                        diff_sign_issues.append(entry)
                    else:
                        diff_issues.append(entry)
    return [
        _finding("Q1 quarterly = Q1 cumulative (API)", checked_q1, q1_issues),
        _finding("reported quarter = cumulative difference (API, same sign)", checked_diff, diff_sign_issues,
                 "explained by opposite sign conventions between the two reports"),
        _finding("reported quarter = cumulative difference (API, unexplained)", checked_diff, diff_issues,
                 "may reflect restatement between reports"),
    ]


def check_calculated_quarters(db: sqlite3.Connection) -> dict:
    """Stored calculated quarters reproduce from their recorded inputs; sign conflicts tiered."""
    rows = db.execute(
        """SELECT f.fact_id,f.corp_code,c.company_name,p.year,p.report_type,f.fs_div,f.sj_div,
                  f.account_key,f.original_account_nm,f.calculated_value,f.source_values
           FROM financial_facts f JOIN periods p USING(period_id) JOIN companies c USING(corp_code)
           WHERE f.is_calculated=1"""
    ).fetchall()
    quarter_scale: dict[tuple, list[int]] = defaultdict(list)
    for corp, year, fs, sj, key, value in db.execute(
            """SELECT f.corp_code,p.year,f.fs_div,f.sj_div,f.account_key,
                      COALESCE(f.normalized_value,f.calculated_value)
               FROM financial_facts f JOIN periods p USING(period_id)
               WHERE f.value_type='quarterly' AND p.report_type IN ('Q1','H1','Q3')"""):
        if value:
            quarter_scale[(corp, year, fs, sj, key)].append(abs(value))
    mismatch, conflicts = [], Counter()
    high_risk = []
    for fact_id, corp, name, year, report, fs, sj, key, account, value, sources in rows:
        inputs = json.loads(sources or "{}")
        numbers = [v for k, v in inputs.items() if isinstance(v, int) and not isinstance(v, bool)]
        if len(numbers) == 1:
            expected = numbers[0]
        elif len(numbers) == 2:
            current, prior = numbers
            aligned = inputs.get("sign_note") == "sign_aligned"
            expected = current + prior if aligned else current - prior
            if current and prior and (current > 0) != (prior > 0):
                if aligned:
                    conflicts["aligned_verified"] += 1
                else:
                    conflicts[f"unaligned_{sj}"] += 1
                    scale = statistics.median(quarter_scale.get((corp, year, fs, sj, key), [0]) or [0])
                    if scale and abs(value) > 3 * scale and 0.2 <= abs(current + prior) / scale <= 3:
                        high_risk.append({"fact_id": fact_id, "company": name, "period": f"{year} {report}",
                                          "fs_div": fs, "sj_div": sj, "account": account, "stored": value,
                                          "sign_aligned_alternative": current + prior,
                                          "typical_quarter": scale})
        else:
            continue
        if expected != value:
            mismatch.append({"fact_id": fact_id, "company": name, "stored": value, "recomputed": expected})
    result = _finding("calculated quarter reproduces from inputs", len(rows), mismatch)
    result["sign_conflicts"] = dict(conflicts)
    result["sign_conflict_high_risk"] = {
        "count": len(high_risk), "examples": high_risk[:40],
        "note": "inference only: stored quarter exceeds 3x the typical quarter while the sign-aligned "
                "alternative is in range; needs document evidence before any correction",
    }
    return result


def check_balance_identity(db: sqlite3.Connection) -> dict:
    rows = db.execute(
        """SELECT f.corp_code,c.company_name,p.year,p.report_type,f.fs_div,
                  MAX(CASE WHEN f.account_id='ifrs-full_Assets' THEN f.normalized_value END),
                  MAX(CASE WHEN f.account_id='ifrs-full_Liabilities' THEN f.normalized_value END),
                  MAX(CASE WHEN f.account_id='ifrs-full_Equity' THEN f.normalized_value END)
           FROM financial_facts f JOIN periods p USING(period_id) JOIN companies c USING(corp_code)
           WHERE f.sj_div='BS' AND f.value_type='point_in_time'
           GROUP BY 1,2,3,4,5"""
    ).fetchall()
    checked, issues, incomplete = 0, [], []
    for corp, name, year, report, fs, assets, liabilities, equity in rows:
        if None in (assets, liabilities, equity):
            incomplete.append([name, year, report, fs])
            continue
        checked += 1
        if assets != liabilities + equity:
            issues.append([name, year, report, fs, assets, liabilities, equity])
    result = _finding("assets = liabilities + equity (API)", checked, issues)
    result["incomplete_slots"] = incomplete
    return result


def _fact_key(row: dict) -> str:
    raw = "|".join(str(row[k] if row[k] is not None else "") for k in (
        "corp_code", "year", "report_type", "fs_div", "sj_div", "account_key", "ord", "value_type",
        "is_calculated"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _recompute_kpi(method: str, inputs: dict) -> int | None:
    """Recompute a KPI ratio (bps), year-on-year growth (bps) or balance change from its inputs."""
    def bps(numerator: int, denominator: int) -> int:
        return int((Decimal(numerator) * 10000 / Decimal(denominator)).quantize(Decimal(1), ROUND_HALF_UP))
    if "numerator" in inputs:
        return bps(inputs["numerator"]["value"], inputs["denominator"]["value"])
    if "prior_year" in inputs:
        return bps(inputs["current"]["value"] - inputs["prior_year"]["value"], inputs["prior_year"]["value"])
    if "previous" in inputs:
        return inputs["current"]["value"] - inputs["previous"]["value"]
    return None


def _check_expense_signs(rows: list[dict]) -> list[dict]:
    """Standardized expense rows: proven sign, same size as the filed value, quarters = cumulative difference."""
    manifest = expense_sign._manifest()
    cumulative = {}
    for row in rows:
        if row["value_type"] in ("cumulative", "annual"):
            cumulative[(row["corp_code"], row["year"], row["report_type"], row["metric_code"], row["fs_div"],
                        row["scope"])] = row["value"]
    issues = []
    for row in rows:
        record = json.loads(row["source_values"])[EXPENSE_SIGN_KEY]
        original, value = int(record["original_value"]), row["value"]
        ident = {"metric_value_id": row["metric_value_id"], "metric": row["metric_code"], "stored": value,
                 "original": original}
        if record.get("status") != "standardized":
            if value != original or row["review_status"] != "review_required":
                issues.append({**ident, "issue": "unverified row must keep the filed value in review"})
            continue
        if abs(value) != abs(original) or record.get("standardized_value") != value:
            issues.append({**ident, "issue": "size or record differs from the filed value"})
            continue
        if row["value_type"] in ("cumulative", "annual"):
            evidence = manifest.get((row["corp_code"], row["year"], row["report_type"], row["fs_div"],
                                     row["metric_code"], original))
            expected = None if evidence is None else (abs(original) if evidence["meaning"] == "expense"
                                                      else -abs(original))
            if evidence is None or value != expected or not record.get("evidence_rcept_no"):
                issues.append({**ident, "issue": "no matching filed-statement evidence"})
        else:
            previous = expense_sign.PREVIOUS_REPORT.get(row["report_type"])
            slot = (row["corp_code"], row["year"], row["report_type"], row["metric_code"], row["fs_div"], row["scope"])
            inputs = record.get("inputs") or {}
            current_value = cumulative.get(slot)
            previous_value = cumulative.get(slot[:2] + (previous,) + slot[3:]) if previous else 0
            if inputs.get(row["report_type"]) != current_value or \
                    (previous and inputs.get(previous) != previous_value) or \
                    current_value is None or previous_value is None or value != current_value - previous_value:
                issues.append({**ident, "issue": "quarter is not the standardized cumulative difference"})
    return issues


def _profiles(db: sqlite3.Connection) -> dict[str, dict]:
    """Stored financial profiles plus the implicit general profiles.

    KRX-grouped companies without a stored profile get their KPI rows from
    ``industry_kpi_pipeline --transient-profiles``; the same function derives
    the profile those rows were built with.
    """
    from src.industry_kpi_pipeline import general_company_profiles

    db.row_factory = sqlite3.Row
    profiles = {r["corp_code"]: dict(r) for r in db.execute("SELECT * FROM company_financial_profiles")}
    db.row_factory = None
    for profile in general_company_profiles(db):
        profiles.setdefault(profile["corp_code"], profile)
    return profiles


def check_metric_layer(db: sqlite3.Connection) -> list[dict]:
    profiled = set(_profiles(db))
    db.row_factory = sqlite3.Row
    facts = {}
    for row in db.execute(
            "SELECT f.*,p.year,p.report_type FROM financial_facts f JOIN periods p USING(period_id)"):
        row = dict(row)
        if row["corp_code"] in profiled:
            facts[_fact_key(row)] = row
    metrics = [dict(r) for r in db.execute("SELECT * FROM financial_metric_values")]
    capex = {
        (r["corp_code"], r["year"], r["report_type"], r["fs_div"], r["value_type"]): r["cash_outflow_amount"]
        for r in db.execute(
            "SELECT c.*, p.year, p.report_type FROM capex_facts c JOIN periods p USING(period_id)")
    }
    db.row_factory = None
    direct_issues, derived_issues, document_issues = [], [], []
    standardized = []
    for row in metrics:
        # Bank expense metrics carry an analysis sign; every source check below
        # runs on the filed value it was derived from (exact, sign included).
        parsed_sources = json.loads(row["source_values"] or "{}")
        record = (parsed_sources.get(EXPENSE_SIGN_KEY)
                  if isinstance(parsed_sources, dict) else None)
        if record is not None:
            standardized.append(row)
            row = {**row, "value": record["original_value"]}
        if row["source_kind"] == "DIRECT" and row["source_type"] == "capex_facts":
            expected = capex.get((row["corp_code"], row["year"], row["report_type"], row["fs_div"],
                                  row["value_type"]))
            if expected != row["value"]:
                direct_issues.append({"metric_value_id": row["metric_value_id"], "metric": row["metric_code"],
                                      "stored": row["value"], "capex_value": expected})
        elif row["source_kind"] == "DIRECT":
            fact = facts.get(row["source_fact_key"])
            value = None if fact is None else (fact["calculated_value"] if fact["is_calculated"]
                                               else fact["normalized_value"])
            if fact is None or value != row["value"]:
                direct_issues.append({"metric_value_id": row["metric_value_id"], "metric": row["metric_code"],
                                      "stored": row["value"], "fact_value": value})
        elif row["source_kind"] == "DERIVED":
            inputs = json.loads(row["source_values"] or "{}")
            method = row["calculation_method"] or ""
            if row["source_type"] == "capex_facts":
                expected = capex.get((row["corp_code"], row["year"], row["report_type"], row["fs_div"],
                                      row["value_type"]))
            elif row["source_type"] == "kpi_calculation":
                expected = _recompute_kpi(method, inputs)
            elif " - " in method and "cumulative" not in method and "FY annual" not in method:
                income_code, expense = method.split(" - ")
                expense_code = expense.replace("ABS(", "").rstrip(")")
                expected = int(inputs[income_code]) - abs(int(inputs[expense_code]))
            else:
                numbers = [v for k, v in inputs.items() if isinstance(v, int) and not isinstance(v, bool)]
                aligned = inputs.get("sign_note") == "sign_aligned"
                expected = (numbers[0] + numbers[1] if aligned else numbers[0] - numbers[1]) \
                    if len(numbers) == 2 else numbers[0] if len(numbers) == 1 else None
            if expected != row["value"]:
                derived_issues.append({"metric_value_id": row["metric_value_id"], "metric": row["metric_code"],
                                       "stored": row["value"], "recomputed": expected, "method": method})
        else:
            sources = json.loads(row["source_values"] or "{}")
            if not (row["source_document_id"] and row["source_rcept_no"] and sources.get("locator")):
                document_issues.append({"metric_value_id": row["metric_value_id"]})
    kinds = Counter((r["source_kind"], r["review_status"]) for r in metrics)
    sign_issues = _check_expense_signs(standardized)
    result = [
        _finding("expense metric analysis sign follows filed-statement evidence", len(standardized), sign_issues),
        _finding("DIRECT metric = source fact value", kinds_total(kinds, "DIRECT"), direct_issues),
        _finding("DERIVED metric reproduces from inputs", kinds_total(kinds, "DERIVED"), derived_issues),
        _finding("DOCUMENT_EXTRACTED has document locator", kinds_total(kinds, "DOCUMENT_EXTRACTED"),
                 document_issues),
    ]
    result[0]["status_counts"] = {f"{k[0]}/{k[1]}": v for k, v in sorted(kinds.items())}
    by_slot = defaultdict(dict)
    for row in metrics:
        by_slot[(row["corp_code"], row["year"], row["report_type"], row["value_type"], row["fs_div"],
                 row["source_kind"])][row["metric_code"]] = row["value"]
    gaps = []
    for slot, values in by_slot.items():
        if {"NET_INTEREST_INCOME", "INTEREST_INCOME", "INTEREST_EXPENSE"} <= values.keys() \
                and slot[-1] in {"DIRECT", "DOCUMENT_EXTRACTED"}:
            computed = values["INTEREST_INCOME"] - abs(values["INTEREST_EXPENSE"])
            if computed != values["NET_INTEREST_INCOME"]:
                gaps.append({"slot": list(slot), "presented_nii": values["NET_INTEREST_INCOME"],
                             "income_minus_expense": computed,
                             "difference": values["NET_INTEREST_INCOME"] - computed})
    result.append(_finding("presented NII vs interest income - |expense|",
                           sum(1 for v in by_slot.values() if "NET_INTEREST_INCOME" in v), gaps,
                           "a difference is a definition gap (e.g. insurance finance items), not an error"))
    return result


def kinds_total(kinds: Counter, kind: str) -> int:
    return sum(v for k, v in kinds.items() if k[0] == kind)


def check_capex(db: sqlite3.Connection) -> list[dict]:
    rows = db.execute(
        """SELECT c.capex_id,c.corp_code,p.year,p.report_type,c.fs_div,c.value_type,c.cash_outflow_amount,
                  c.calculated_value,c.source_values,c.is_calculated,c.review_required
           FROM capex_facts c JOIN periods p USING(period_id)"""
    ).fetchall()
    cumulative = {(r[1], r[2], r[3], r[4]): r[6] for r in rows if r[5] != "quarterly"}
    issues, negative = [], []
    previous = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}
    for capex_id, corp, year, report, fs, value_type, outflow, calculated, sources, is_calc, review in rows:
        if value_type != "quarterly":
            continue
        current = cumulative.get((corp, year, report, fs))
        prior = cumulative.get((corp, year, previous.get(report), fs)) if report != "Q1" else 0
        if current is None or prior is None:
            continue
        expected = current - prior
        # A negative standalone quarter keeps its calculated value and has no confirmed outflow.
        if calculated != expected or outflow != (expected if expected >= 0 else None):
            issues.append({"capex_id": capex_id, "corp": corp, "period": f"{year} {report}",
                           "stored": calculated, "recomputed": expected})
        if expected < 0:
            negative.append({"capex_id": capex_id, "corp": corp, "period": f"{year} {report}",
                             "quarter": expected, "review_required": review})
    bank = db.execute(
        """SELECT COUNT(*) FROM capex_facts WHERE corp_code IN
           (SELECT corp_code FROM company_financial_profiles WHERE capex_applicable=0)"""
    ).fetchone()[0]
    return [
        _finding("CAPEX quarter = cumulative outflow difference", len(rows), issues),
        _finding("CAPEX negative quarter flagged for review", len(negative),
                 [n for n in negative if not n["review_required"]]),
        _finding("no CAPEX rows for banks", 1, [bank] if bank else []),
    ]


def check_mapping(db: sqlite3.Connection) -> dict:
    status = dict(db.execute("SELECT mapping_status,COUNT(*) FROM account_mapping GROUP BY 1").fetchall())
    conflicting = db.execute(
        """SELECT account_id,sj_div,COUNT(DISTINCT standard_account_name),GROUP_CONCAT(DISTINCT standard_account_name)
           FROM account_mapping WHERE account_id NOT LIKE '-%' AND COALESCE(account_id,'')<>''
           GROUP BY 1,2 HAVING COUNT(DISTINCT standard_account_name)>1"""
    ).fetchall()
    unmapped = db.execute(
        "SELECT COUNT(*) FROM financial_facts WHERE standard_account_nm IS NULL OR standard_account_nm=''"
    ).fetchone()[0]
    result = _finding("one standard name per standard account_id", len(status), [list(r) for r in conflicting])
    result["mapping_status"] = status
    result["facts_without_standard_name"] = unmapped
    return result


def check_coverage(db: sqlite3.Connection, bge_path: Path) -> dict:
    periods = db.execute("SELECT DISTINCT year, report_type FROM periods").fetchall()
    have = set(db.execute(
        "SELECT DISTINCT f.corp_code,p.year,p.report_type FROM financial_facts f JOIN periods p USING(period_id)"
    ).fetchall())
    have |= set(db.execute(
        "SELECT DISTINCT corp_code,year,report_type FROM financial_metric_values WHERE source_kind='DOCUMENT_EXTRACTED'"
    ).fetchall())
    chunks = set()
    if bge_path.is_file():
        with closing(sqlite3.connect(f"file:{bge_path.resolve()}?mode=ro", uri=True)) as bge:
            chunks = set(bge.execute("SELECT DISTINCT corp_code,year,report_type FROM filing_chunks").fetchall())
    missing = []
    for corp, name in db.execute("SELECT corp_code,company_name FROM companies ORDER BY company_name"):
        for year, report in sorted(periods):
            fin, filing = (corp, year, report) in have, (corp, year, report) in chunks
            if not (fin and filing):
                missing.append({"company": name, "period": f"{year} {report}",
                                "financial": fin, "filing_chunks": filing})
    return _finding("financial values and filing chunks per company-period",
                    len(periods) * db.execute("SELECT COUNT(*) FROM companies").fetchone()[0], missing)


PERIOD_VALUE_TYPES = {
    "point_in_time": {"point_in_time"},
    "flow": {"quarterly", "cumulative", "annual"},
    # "ratio": a ratio disclosed as such in the filing (healthcare R&D/revenue).
    "ratio": {"point_in_time", "quarterly", "cumulative", "annual", "ratio"},
}


def check_kpi_layer(db: sqlite3.Connection) -> list[dict]:
    """Structural checks of every metric row: definition, unit, period, scope, provenance."""
    db.row_factory = sqlite3.Row
    definitions = {r["metric_code"]: dict(r) for r in db.execute("SELECT * FROM financial_metric_definitions")}
    profiles = _profiles(db)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute("SELECT * FROM financial_metric_values")]
    db.row_factory = None
    by_key = {(r["corp_code"], r["year"], r["report_type"], r["metric_code"], r["value_type"], r["fs_div"],
               r["scope"], r["source_kind"]): r for r in rows}
    issues = {name: [] for name in ("definition_and_unit", "period", "scope", "provenance", "status", "kpi_inputs")}
    for r in rows:
        ident = {"metric_value_id": r["metric_value_id"], "metric": r["metric_code"], "corp": r["corp_code"],
                 "period": f"{r['year']} {r['report_type']}", "value_type": r["value_type"]}
        definition = definitions.get(r["metric_code"])
        if definition is None or r["value"] is None or (
                definition["unit_type"] != r["unit"] and not (definition["unit_type"] == "KRW" and r["unit"] == "KRW")):
            issues["definition_and_unit"].append({**ident, "unit": r["unit"],
                                                  "expected": definition and definition["unit_type"]})
        if definition and r["value_type"] not in PERIOD_VALUE_TYPES[definition["period_nature"]] \
                or (r["report_type"] == "FY" and r["value_type"] == "cumulative") \
                or (r["report_type"] != "FY" and r["value_type"] == "annual"):
            issues["period"].append(ident)
        profile = profiles.get(r["corp_code"])
        if profile is None or r["scope"] != profile["comparison_scope"] or \
                r["fs_div"] not in (profile["preferred_fs_div"], profile["fallback_fs_div"]):
            issues["scope"].append({**ident, "fs_div": r["fs_div"], "scope": r["scope"]})
        if r["source_kind"] == "DIRECT":
            complete = r["source_fact_key"] and r["source_rcept_no"] and r["source_account_name"]
        elif r["source_kind"] == "DERIVED":
            complete = r["calculation_method"] and r["source_values"]
        else:
            complete = r["source_document_id"] and r["source_rcept_no"] and r["source_values"]
        if not complete:
            issues["provenance"].append({**ident, "source_kind": r["source_kind"]})
        if r["review_status"] not in ("approved", "review_required"):
            issues["status"].append({**ident, "review_status": r["review_status"]})
        if r["source_type"] == "kpi_calculation":
            inputs = json.loads(r["source_values"])
            for role, ref in inputs.items():
                expected_year = r["year"] - 1 if role == "prior_year" else r["year"]
                if role == "previous":
                    offset, report = {"Q1": (-1, "FY"), "H1": (0, "Q1"), "Q3": (0, "H1"), "FY": (0, "Q3")}[
                        r["report_type"]]
                    expected = (r["year"] + offset, report, "point_in_time")
                else:
                    expected_type = "point_in_time" if r["metric_code"] == "INVENTORY_CHANGE" else r["value_type"]
                    expected = (expected_year, r["report_type"], expected_type)
                stored = by_key.get((r["corp_code"], ref["year"], ref["report_type"], ref["metric"],
                                     ref["value_type"], r["fs_div"], r["scope"], ref["source_kind"]))
                if (ref["year"], ref["report_type"], ref["value_type"]) != expected or stored is None \
                        or stored["value"] != ref["value"]:
                    issues["kpi_inputs"].append({**ident, "role": role, "input": ref})
    return [_finding(f"metric rows: {name}", len(rows), found) for name, found in issues.items()]


def run_audit(path: Path = FINANCIAL_DB_PATH, bge_path: Path | None = None) -> dict:
    bge_path = bge_path or BGE_DB_PATH
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as db:
        findings = [check_raw_values(db), *check_units_and_duplicates(db), check_statement_scope(db),
                    *check_quarter_consistency(db), check_calculated_quarters(db), check_balance_identity(db),
                    *check_metric_layer(db), *check_kpi_layer(db), *check_capex(db), check_mapping(db),
                    check_coverage(db, bge_path)]
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = len(db.execute("PRAGMA foreign_key_check").fetchall())
    return {"database": str(path), "integrity": integrity, "foreign_key_errors": fk_errors,
            "verified_sign_alignments": len(VERIFIED_SIGN_ALIGNMENTS), "findings": findings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=FINANCIAL_DB_PATH)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = run_audit(args.db)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"integrity={report['integrity']} fk_errors={report['foreign_key_errors']}")
    for finding in report["findings"]:
        print(f"- {finding['check']}: checked {finding['checked']}, issues {finding['issues']}")


if __name__ == "__main__":
    main()
