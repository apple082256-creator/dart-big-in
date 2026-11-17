"""Wait for the independent index build, then validate and benchmark it."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time

from src.retrieval.bge_m3_indexer import BGE_DB_PATH, BGE_FAISS_PATH, BUILD_REPORT_PATH
from src.config import PROCESSED_DIR


def main():
    parser = argparse.ArgumentParser(description="진행 중인 BGE 인덱스 빌드 후 검증 및 비교")
    parser.add_argument("--timeout-hours", type=float, default=24)
    args = parser.parse_args()
    started = last_change = time.monotonic()
    previous_count = -1
    while not BUILD_REPORT_PATH.exists():
        if time.monotonic() - started > args.timeout_hours * 3600:
            raise TimeoutError("BGE 빌드 완료 대기 시간이 초과되었습니다.")
        if BGE_DB_PATH.exists():
            with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
                count = connection.execute("SELECT COUNT(*) FROM filing_chunks").fetchone()[0]
            if count != previous_count:
                print(f"BGE committed chunks: {count}", flush=True)
                previous_count = count
                last_change = time.monotonic()
        if time.monotonic() - last_change > 1800:
            raise TimeoutError("30분간 임베딩 진행이 없어 후속 검증을 중지합니다.")
        time.sleep(30)
    report = json.loads(BUILD_REPORT_PATH.read_text(encoding="utf-8"))
    if report.get("failed") or not BGE_FAISS_PATH.exists():
        raise RuntimeError("빌드 실패 또는 FAISS 미생성으로 평가하지 않습니다.")
    from src.quality.bge_m3_faiss_validation import run_validation
    validation = run_validation()
    if validation["status"] != "PASS":
        raise RuntimeError("무결성 검증 실패로 평가하지 않습니다.")
    from src.quality.vector_retrieval_benchmark_v2 import run_benchmark
    _, summary, decision = run_benchmark()
    print(summary.to_string(index=False), flush=True)
    print(f"최종 판단: {decision}", flush=True)
    output = PROCESSED_DIR / "quality" / "vector_v2_completion_report.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump({"status": "BENCHMARK_COMPLETED", "decision": decision,
                   "integrity_status": validation["status"], "streamlit_default_changed": False,
                   "goldset_review_required": True}, handle, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
