from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timezone

from src.config import FINANCIAL_DB_PATH, PROCESSED_DIR, PROJECT_ROOT
from src.llm.analyzer import analyze_company
from src.llm.gemini_client import DEFAULT_MODEL, GeminiClient
from src.quality.final_db_llm_validation import QUESTION_CASES, _add_final_db_checks
from src.retrieval.financial_retriever import get_financial_context
from src.retrieval.operational_retriever import retrieve_filing_context


JSON_PATH = PROCESSED_DIR / "quality" / "post_promotion_smoke_test.json"
CSV_PATH = PROCESSED_DIR / "quality" / "post_promotion_smoke_test_summary.csv"
DISPLAY_DB_PATH = "db/financial/financial.db"


def _test_row(
    name: str,
    status: str,
    note: str,
    corp_name: str = "",
    year: int | str = "",
    report_type: str = "",
    rcept_no: str = "",
) -> dict:
    return {
        "test_name": name,
        "status": status,
        "db_path": DISPLAY_DB_PATH,
        "corp_name": corp_name,
        "year": year,
        "report_type": report_type,
        "rcept_no": rcept_no,
        "note": note,
    }


def _streamlit_health() -> tuple[bool, str]:
    command = [
        sys.executable, "-m", "streamlit", "run", "app/app.py",
        "--server.headless", "true", "--server.port", "8767",
        "--browser.gatherUsageStats", "false",
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(30):
            if process.poll() is not None:
                return False, f"Streamlit exited with code {process.returncode}"
            try:
                with urllib.request.urlopen("http://127.0.0.1:8767/_stcore/health", timeout=1) as response:
                    body = response.read().decode("utf-8").strip()
                return body == "ok", f"/_stcore/health={body}"
            except Exception:
                time.sleep(0.25)
        return False, "Streamlit health check timed out"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_backend_smoke_tests() -> list[dict]:
    rows: list[dict] = []
    with closing(sqlite3.connect(FINANCIAL_DB_PATH)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    rows.append(_test_row("financial_db_integrity", "PASS" if integrity == "ok" else "FAIL", integrity))

    financial = get_financial_context("삼성전자", 2025, "FY", "매출액")
    financial_ok = any(row.get("normalized_value") == 333_605_938_000_000 for row in financial)
    rows.append(_test_row(
        "financial_db_numeric_lookup", "PASS" if financial_ok else "FAIL",
        "2025 annual revenue found", "삼성전자", 2025, "FY",
        ",".join(sorted({str(row["rcept_no"]) for row in financial})),
    ))

    vector_result = retrieve_filing_context(
        "반도체 사업 위험요인", corp_name="삼성전자", year=2025, report_type="FY", top_k=5,
    )
    vector = vector_result.rows
    vector_ok = bool(vector) and all(row["corp_name"] == "삼성전자" and row["year"] == 2025 for row in vector)
    rows.append(_test_row(
        "vector_db_search", "PASS" if vector_ok else "FAIL",
        f"retriever={vector_result.effective_retriever}, matching chunks={len(vector)}",
        "삼성전자", 2025, "FY",
        ",".join(sorted({str(row["rcept_no"]) for row in vector})),
    ))

    integrated = analyze_company(
        "삼성전자의 2025년 실적 변화와 그 원인을 공시 근거와 함께 설명해줘.",
        corp_name="삼성전자", year=2025, report_type="FY",
    )
    integrated_ok = (
        integrated["route"] == "integrated"
        and bool(integrated["financial_evidence"])
        and bool(integrated["filing_evidence"])
    )
    rows.append(_test_row(
        "integrated_backend_routing", "PASS" if integrated_ok else "FAIL",
        f"financial={len(integrated['financial_evidence'])}, filing={len(integrated['filing_evidence'])}",
        "삼성전자", 2025, "FY",
        ",".join(sorted({
            str(row["rcept_no"])
            for row in integrated["financial_evidence"] + integrated["filing_evidence"]
        })),
    ))

    derived = get_financial_context("주성엔지니어링", 2024, "H1", "유형자산취득(CAPEX)")
    derived_ok = any(
        row["value_type"] == "quarterly" and row["normalized_value"] == 5_023_179_778
        and row["value_source"] == "derived" and row["is_derived"] == 1
        for row in derived
    )
    rows.append(_test_row(
        "capex_derived_lookup", "PASS" if derived_ok else "FAIL",
        "Q2 CAPEX=5,023,179,778 KRW, value_source=derived",
        "주성엔지니어링", 2024, "H1",
        ",".join(sorted({str(row["rcept_no"]) for row in derived})),
    ))

    direct = get_financial_context("원익IPS", 2024, "FY", "유형자산취득(CAPEX)")
    direct_ok = any(
        row["value_type"] == "annual" and row["normalized_value"] == 21_432_773_671
        and row["value_source"] == "direct" and row["is_derived"] == 0
        for row in direct
    )
    rows.append(_test_row(
        "capex_direct_lookup", "PASS" if direct_ok else "FAIL",
        "FY CAPEX=21,432,773,671 KRW, value_source=direct",
        "원익IPS", 2024, "FY",
        ",".join(sorted({str(row["rcept_no"]) for row in direct})),
    ))

    health_ok, health_note = _streamlit_health()
    rows.append(_test_row("streamlit_health", "PASS" if health_ok else "FAIL", health_note))
    return rows


def _llm_summary(result: dict) -> dict:
    return {
        "question_id": result["question_id"],
        "query": result["query"],
        "corp_name": result["corp_name"],
        "years": result["years"],
        "report_type": result["report_type"],
        "route": result["route"],
        "answer": result["answer"],
        "model": result["model"],
        "generation_finish_reason": result["generation_finish_reason"],
        "validation": result["validation"],
        "financial_sources": result["financial_sources"],
        "filing_sources": result["filing_sources"],
    }


def _write_outputs(backend_tests: list[dict], llm_results: list[dict]) -> None:
    llm_tests = []
    for result in llm_results:
        receipts = sorted({
            str(row.get("rcept_no"))
            for row in result["financial_sources"] + result["filing_sources"]
            if row.get("rcept_no")
        })
        llm_tests.append(_test_row(
            f"gemini_question_{result['question_id']}",
            result["validation"]["validation_status"],
            f"route={result['route']}, model={result['model']}",
            result["corp_name"], ",".join(map(str, result["years"])),
            result["report_type"], ",".join(receipts),
        ))
    all_tests = backend_tests + llm_tests
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": DISPLAY_DB_PATH,
        "all_passed": len(llm_results) == len(QUESTION_CASES) and all(
            row["status"] == "PASS" for row in all_tests
        ),
        "tests": all_tests,
        "llm_results": [_llm_summary(result) for result in llm_results],
    }
    JSON_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_tests[0]))
        writer.writeheader()
        writer.writerows(all_tests)


def run_post_promotion_smoke_test() -> dict:
    backend_tests = run_backend_smoke_tests()
    saved_results: list[dict] = []
    if JSON_PATH.exists():
        saved_results = json.loads(JSON_PATH.read_text(encoding="utf-8")).get("llm_results", [])
        if not all(
            item.get("query") == QUESTION_CASES[index]["query"]
            for index, item in enumerate(saved_results)
            if index < len(QUESTION_CASES)
        ):
            saved_results = []

    results = saved_results[:len(QUESTION_CASES)]
    client = GeminiClient(model=DEFAULT_MODEL)
    for index, case in enumerate(QUESTION_CASES[len(results):], start=len(results) + 1):
        result = analyze_company(
            case["query"], corp_name=case["corp_name"], year=case["year"],
            report_type=case["report_type"], retrieval_method="bge_m3_dense", llm_client=client,
        )
        result["generation_finish_reason"] = client.last_finish_reason
        result["question_id"] = index
        result["model"] = client.last_model
        result["validation"] = _add_final_db_checks(result, case)
        results.append(result)
        _write_outputs(backend_tests, results)

    _write_outputs(backend_tests, results)
    return {
        "backend_passed": sum(row["status"] == "PASS" for row in backend_tests),
        "backend_total": len(backend_tests),
        "llm_passed": sum(result["validation"]["validation_status"] == "PASS" for result in results),
        "llm_total": len(results),
        "all_passed": len(results) == len(QUESTION_CASES) and all(
            row["status"] == "PASS" for row in backend_tests
        ) and all(result["validation"]["validation_status"] == "PASS" for result in results),
        "json_path": str(JSON_PATH), "csv_path": str(CSV_PATH),
    }


if __name__ == "__main__":
    print(run_post_promotion_smoke_test())
