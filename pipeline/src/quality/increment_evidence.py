"""Filed-statement evidence for a newly loaded period (e.g. 2026 Q1/H1) in staging.

Runs the same checks used for 2023-2025 on the new filings downloaded by
``period_increment_pipeline --download-xml``:

1. calculated quarters whose two cumulatives have opposite API signs:
   same printed sign and no change of printing style -> presentation flip
   (pinned in ``verified_sign_alignment_manifest.json``); different printed
   sign -> real reversal (stored value kept); otherwise unresolved (review);
2. quarters the normalizer held back -> ``classify_held_by_documents``
   (pinned in ``verified_quarter_manifest.json`` when derivable);
3. bank expense metrics -> ``expense_sign_evidence.judge`` (pinned in
   ``expense_sign_manifest.json``).

Nothing is written without ``--write``; manifests only gain entries keyed by
the exact new values, so 2023-2025 results do not change.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.db import expense_sign
from src.db.quarter_backfill import compute_quarters
from src.db.verified_quarter_overrides import EVIDENCE_MANIFEST_PATH, SIGN_ALIGNMENT_MANIFEST_PATH
from src.quality import residual_verification as rv
from src.quality.expense_sign_evidence import EXPENSE_METRICS, judge

PREVIOUS = {"H1": "Q1", "Q3": "H1", "FY": "Q3"}


def register_collection(collection: dict) -> int:
    documents = {}
    for status in collection["statuses"]:
        main = [d["path"] for d in status.get("documents") or [] if d["main"]]
        if main:
            documents[(status["corp_code"], int(status["year"]), status["report_type"])] = main[0]
    rv.register_documents(documents)
    return len(documents)


def sign_conflicts(db: sqlite3.Connection, year: int) -> list[dict]:
    rows = db.execute(
        """SELECT f.fact_id, f.corp_code, p.report_type, f.fs_div, f.sj_div, f.account_key, f.account_id,
                  f.original_account_nm, f.calculated_value, f.source_values, f.rcept_no
           FROM financial_facts f JOIN periods p USING(period_id)
           WHERE p.year=? AND f.is_calculated=1 AND f.source_values LIKE '%sign_conflict_review%'""",
        (year,)).fetchall()
    results = []
    for fact_id, corp, report, fs, sj, key, account_id, name, stored, sources, rcept in rows:
        inputs = json.loads(sources)
        now, before = inputs[report], inputs[PREVIOUS[report]]
        previous = db.execute(
            """SELECT f.original_account_nm, f.rcept_no FROM financial_facts f JOIN periods p USING(period_id)
               WHERE f.corp_code=? AND p.year=? AND p.report_type=? AND f.fs_div=? AND f.sj_div=?
                 AND f.account_key=? AND f.is_calculated=0 AND f.normalized_value=?""",
            (corp, year, PREVIOUS[report], fs, sj, key, before)).fetchone()
        doc_now = rv.presented_sign(corp, year, report, fs, sj, name, now)
        doc_prev = rv.presented_sign(corp, year, PREVIOUS[report], fs, sj, previous[0] if previous else name, before)
        style = rv.presentation_style_changed(corp, year, report, PREVIOUS[report], fs, sj)
        if not (doc_now and doc_prev):
            verdict = "unresolved_not_located"
        elif style is not False:
            verdict = "unresolved_printing_style"
        else:
            verdict = "presentation_flip" if doc_now == doc_prev else "real_reversal"
        results.append({"fact_id": fact_id, "corp_code": corp, "year": year, "report_type": report, "fs_div": fs,
                        "sj_div": sj, "account_key": key, "account_name": name, "current_value": now,
                        "previous_value": before, "stored": stored, "verdict": verdict,
                        "corrected": now + before if verdict == "presentation_flip" else stored,
                        "document_signs": [doc_now, doc_prev], "style_changed": style,
                        "rcept_nos": [rcept, previous[1] if previous else None]})
    return results


def held_quarters(db: sqlite3.Connection, year: int) -> list[dict]:
    records = [{**item, "held_reason": item["reason"], "verdict": "NOT_VERIFIABLE", "finding": "unproven",
                "account_id": item["account_key"]}
               for item in compute_quarters(db)["review"] if item["year"] == year]
    return rv.classify_held_by_documents(db, records)


def expense_rows(db: sqlite3.Connection, year: int) -> list[dict]:
    rows = db.execute(
        f"""SELECT corp_code, year, report_type, fs_div, metric_code, value, source_account_name, source_values
            FROM financial_metric_values
            WHERE year=? AND value_type IN ('cumulative','annual')
              AND metric_code IN ({",".join("?" for _ in EXPENSE_METRICS)})""", (year, *EXPENSE_METRICS)).fetchall()
    results = []
    for corp, y, report, fs, metric, value, name, sources in rows:
        presented = expense_sign.filed_value(value, sources)
        evidence = judge(corp, y, report, fs, metric, name or "", presented)
        results.append({"corp_code": corp, "year": y, "report_type": report, "fs_div": fs, "metric_code": metric,
                        "presented_value": presented, "account_name": name, **evidence})
    return results


def _append(path: Path, entries: list[dict], key) -> int:
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    keys = {key(e) for e in existing}
    new = [e for e in entries if key(e) not in keys]
    if new:
        path.write_text(json.dumps(existing + new, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    return len(new)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True, help="period_increment_pipeline --report JSON")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="근거가 확인된 항목을 매니페스트에 추가한다.")
    args = parser.parse_args()
    documents = register_collection(json.loads(args.collection.read_text(encoding="utf-8")))
    with closing(sqlite3.connect(f"file:{args.db.resolve()}?mode=ro", uri=True)) as db:
        conflicts = sign_conflicts(db, args.year)
        held = held_quarters(db, args.year)
        expenses = expense_rows(db, args.year)
        quarter_entries = rv.build_quarter_manifest(db, held)
    alignments = [{**{k: c[k] for k in ("corp_code", "year", "report_type", "fs_div", "sj_div", "account_key",
                                        "account_name", "current_value", "previous_value")},
                   "previous_value_stored": c["stored"], "evidence_rcept_nos": c["rcept_nos"],
                   "presented": f"both printed {c['document_signs'][0]}"}
                  for c in conflicts if c["verdict"] == "presentation_flip"]
    expense_entries = [e for e in expenses if e["meaning"] in ("expense", "reversal")]
    result = {"documents_registered": documents, "sign_conflicts": conflicts, "held_quarters": held,
              "expenses": expenses, "alignments": alignments, "quarter_entries": quarter_entries}
    if args.write:
        result["written"] = {
            "sign_alignments": _append(SIGN_ALIGNMENT_MANIFEST_PATH, alignments, lambda e: (
                e["corp_code"], e["year"], e["report_type"], e["account_key"], e["current_value"], e["previous_value"])),
            "verified_quarters": _append(EVIDENCE_MANIFEST_PATH, quarter_entries, lambda e: (
                e["corp_code"], e["year"], e["report_type"], e["fs_div"], e["sj_div"], e["account_key"],
                e["current_value"], e["previous_value"])),
            "expense_signs": _append(expense_sign.MANIFEST_PATH, expense_entries, lambda e: (
                e["corp_code"], e["year"], e["report_type"], e["fs_div"], e["metric_code"], e["presented_value"])),
        }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    summary = {
        "documents": documents,
        "sign_conflicts": {v: sum(c["verdict"] == v for c in conflicts) for v in {c["verdict"] for c in conflicts}},
        "held": {v: sum(h["final"] == v for h in held) for v in {h["final"] for h in held}},
        "expenses": {v: sum(e["meaning"] == v for e in expenses) for v in {e["meaning"] for e in expenses}},
        **({"written": result["written"]} if args.write else {}),
    }
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
