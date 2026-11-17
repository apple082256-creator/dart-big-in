"""Build the 25-company, Q1/H1 staging coverage gate CSV."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


INDUSTRY = {
    "KRX_SEMI": "반도체", "KRX_AUTO": "자동차", "KRX_STEEL": "철강",
    "KRX_HEALTH": "헬스케어", "KRX_BANK": "금융",
}


def build(financial: Path, vector: Path, collection: Path, output: Path) -> dict[str, int]:
    payload = json.loads(collection.read_text(encoding="utf-8"))
    statuses = payload["statuses"]
    if len(statuses) != 50 or {row["report_type"] for row in statuses} != {"Q1", "H1"}:
        raise ValueError("coverage input must contain exactly 50 Q1/H1 slots")
    with sqlite3.connect(f"file:{financial.resolve()}?mode=ro", uri=True) as fin, \
            sqlite3.connect(f"file:{vector.resolve()}?mode=ro", uri=True) as bge:
        fin.row_factory = bge.row_factory = sqlite3.Row
        industry = {row[0]: INDUSTRY[row[1]] for row in fin.execute(
            """SELECT corp_code,index_code FROM krx_index_constituents
               WHERE is_current=1 AND index_code IN ('KRX_SEMI','KRX_AUTO','KRX_STEEL','KRX_HEALTH','KRX_BANK')""")}
        profiles = {row[0]: (row[1], bool(row[2])) for row in fin.execute(
            "SELECT corp_code,industry_profile,capex_applicable FROM company_financial_profiles")}
        rows = []
        for status in sorted(statuses, key=lambda item: (item["corp_code"], item["report_type"])):
            corp, report = status["corp_code"], status["report_type"]
            document = next((item for item in status.get("documents") or [] if item.get("main")), None)
            chunks = bge.execute(
                """SELECT COUNT(*),SUM(is_searchable),SUM(embedding IS NOT NULL),
                          COUNT(DISTINCT source_document_id),MIN(source_document_id)
                   FROM filing_chunks WHERE corp_code=? AND year=2026 AND report_type=? AND rcept_no=?""",
                (corp, report, status["latest_rcept_no"]),
            ).fetchone()
            capex = fin.execute(
                """SELECT COUNT(*),SUM(review_required),SUM(cash_outflow_amount IS NULL)
                   FROM capex_facts c JOIN periods p USING(period_id)
                   WHERE c.corp_code=? AND p.year=2026 AND p.report_type=?""", (corp, report)).fetchone()
            metrics = fin.execute(
                """SELECT COUNT(*),SUM(review_status='approved'),SUM(review_status='review_required')
                   FROM financial_metric_values WHERE corp_code=? AND year=2026 AND report_type=?""",
                (corp, report),
            ).fetchone()
            profile, capex_applicable = profiles.get(corp, ("GENERAL", True))
            if not capex_applicable:
                capex_status = "NOT_APPLICABLE"
            elif capex[0] == 0:
                capex_status = "MISSING"
            else:
                capex_status = f"rows={capex[0]};review={capex[1] or 0};null_outflow={capex[2] or 0}"
            if profile == "HEALTHCARE":
                codes = ("RND_EXPENSE", "RND_TO_REVENUE")
                found = fin.execute(
                    """SELECT COUNT(*),SUM(review_status='approved'),SUM(review_status='review_required')
                       FROM financial_metric_values WHERE corp_code=? AND year=2026 AND report_type=?
                         AND metric_code IN (?,?)""", (corp, report, *codes)).fetchone()
                industry_status = f"R&D={found[0]}/2;approved={found[1] or 0};review={found[2] or 0}"
            elif profile in {"BANK_HOLDING", "INTERNET_BANK"}:
                found = fin.execute(
                    """SELECT COUNT(*),SUM(review_status='approved'),SUM(review_status='review_required')
                       FROM financial_metric_values WHERE corp_code=? AND year=2026 AND report_type=?""",
                    (corp, report)).fetchone()
                industry_status = f"BANK_KPI={found[0]};approved={found[1] or 0};review={found[2] or 0}"
            else:
                industry_status = "NOT_APPLICABLE"
            review = []
            if (metrics[2] or 0): review.append(f"metric_review={metrics[2]}")
            if capex_applicable and (capex[1] or 0): review.append(f"capex_review={capex[1]}")
            missing = []
            if not document: missing.append("XML")
            if not chunks[0]: missing.append("CHUNKS")
            rows.append({
                "corp_code": corp, "기업명": status["company"], "산업": industry.get(corp, "UNKNOWN"),
                "report_type": report, "재무 수집 결과": status["state"], "선택 fs_div": status["fs_div"],
                "rcept_no": status["latest_rcept_no"], "정정 여부": int(status["corrections"] > 0),
                "XML 존재": int(bool(document)), "XML SHA-256": document["sha256"] if document else "",
                # canonical SHA-256(rcept_no, source_path) shared with MySQL filing_source_documents
                "문서 ID": chunks[4] if chunks[3] == 1 else "", "청크 수": chunks[0] or 0,
                "검색 가능 청크 수": chunks[1] or 0, "임베딩 수": chunks[2] or 0,
                "CAPEX 상태": capex_status,
                "KPI 상태": f"rows={metrics[0]};approved={metrics[1] or 0};review={metrics[2] or 0}",
                "산업 KPI 상태(R&D/금융)": industry_status, "검토 사유": ";".join(review),
                "누락 사유": ";".join(missing),
            })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    return {"rows": len(rows), "missing_xml": sum(not row["XML 존재"] for row in rows),
            "missing_chunks": sum(not row["청크 수"] for row in rows),
            "review_slots": sum(bool(row["검토 사유"]) for row in rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--financial", type=Path, required=True)
    parser.add_argument("--vector", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.financial, args.vector, args.collection, args.output))


if __name__ == "__main__":
    main()
