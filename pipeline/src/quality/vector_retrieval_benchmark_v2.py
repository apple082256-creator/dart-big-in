from __future__ import annotations

import argparse
import json
import resource
import statistics
import time
from pathlib import Path

import pandas as pd

from src.config import PROCESSED_DIR, PROJECT_ROOT
from src.quality.vector_retrieval_goldset_v2 import OUTPUT_PATH as GOLDSET_PATH, build_goldset
from src.retrieval.bge_m3_indexer import BGE_DB_PATH, BGE_FAISS_PATH, search_bge_m3
from src.retrieval.transformer_retriever import TRANSFORMER_DB_PATH, search_filing_context_transformer
from src.retrieval.vector_retriever import VECTOR_DB_PATH, search_filing_context
from src.quality.vector_v2_policy import is_relevant, promotion_decision


DETAIL_JSON = PROCESSED_DIR / "quality" / "vector_retrieval_benchmark_v2.json"
DETAIL_CSV = PROCESSED_DIR / "quality" / "vector_retrieval_benchmark_v2.csv"
SUMMARY_CSV = PROCESSED_DIR / "quality" / "vector_retrieval_benchmark_summary_v2.csv"


def _output_paths(tag: str) -> tuple[Path, Path, Path]:
    if not tag:
        return DETAIL_JSON, DETAIL_CSV, SUMMARY_CSV
    quality = PROCESSED_DIR / "quality"
    return (quality / f"vector_retrieval_benchmark_v2_{tag}.json",
            quality / f"vector_retrieval_benchmark_v2_{tag}.csv",
            quality / f"vector_retrieval_benchmark_summary_v2_{tag}.csv")


def _is_relevant(result: dict, gold: dict) -> bool:
    return is_relevant(result, gold)


def _methods():
    return {
        "feature_hashing_hybrid": lambda q, **kw: search_filing_context(q, **kw),
        "multilingual_e5_dense": lambda q, **kw: search_filing_context_transformer(q, **kw, hybrid=False),
        "multilingual_e5_hybrid": lambda q, **kw: search_filing_context_transformer(q, **kw, hybrid=True),
        "bge_m3_dense": lambda q, **kw: search_bge_m3(
            q, **kw, hybrid=False, apply_priority=False,
        ),
        "bge_m3_priority_dense": lambda q, **kw: search_bge_m3(
            q, **kw, hybrid=False, apply_priority=True,
        ),
        "bge_m3_hybrid": lambda q, **kw: search_bge_m3(
            q, **kw, hybrid=True, apply_priority=False,
        ),
        "bge_m3_priority_hybrid": lambda q, **kw: search_bge_m3(
            q, **kw, hybrid=True, apply_priority=True,
        ),
    }


def _size_for(method: str) -> int:
    if method.startswith("feature"):
        return VECTOR_DB_PATH.stat().st_size
    if method.startswith("multilingual"):
        return TRANSFORMER_DB_PATH.stat().st_size
    return BGE_DB_PATH.stat().st_size + BGE_FAISS_PATH.stat().st_size


def _summarize(detail_frame: pd.DataFrame, goldset: list[dict], latency: dict[str, list[float]],
               subset_name: str) -> list[dict]:
    summaries: list[dict] = []
    question_ids = [gold["question_id"] for gold in goldset]
    question_count = len(question_ids)
    for method in _methods():
        subset = detail_frame[detail_frame["retrieval_method"].eq(method)
                              & detail_frame["question_id"].isin(question_ids)]
        first_relevant: list[int | None] = []
        for question_id in question_ids:
            hits = subset[subset["question_id"].eq(question_id) & subset["is_relevant"]]["rank"].tolist()
            first_relevant.append(min(hits) if hits else None)
        times = latency[method]
        p95 = statistics.quantiles(times, n=20, method="inclusive")[18] if len(times) > 1 else times[0]
        summaries.append({
            "retrieval_method": method,
            "question_subset": subset_name,
            "question_count": question_count,
            "hit_at_1": sum(rank == 1 for rank in first_relevant) / question_count,
            "hit_at_3": sum(rank is not None and rank <= 3 for rank in first_relevant) / question_count,
            "hit_at_5": sum(rank is not None and rank <= 5 for rank in first_relevant) / question_count,
            "mrr": sum(1.0 / rank if rank else 0.0 for rank in first_relevant) / question_count,
            "rcept_no_match_rate": float(subset.groupby("question_id")["rcept_no_match"].max().mean()),
            "corp_match_rate": float(subset["corp_match"].mean()),
            "year_match_rate": float(subset["year_match"].mean()),
            "report_type_match_rate": float(subset["report_type_match"].mean()),
            "other_corp_count": int((~subset["corp_match"]).sum()),
            "other_year_count": int((~subset["year_match"]).sum()),
            "other_report_type_count": int((~subset["report_type_match"]).sum()),
            "average_latency_ms": statistics.mean(times),
            "p95_latency_ms": p95,
            "index_size_bytes": _size_for(method),
            "peak_process_memory_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        })
    return summaries


def run_benchmark(goldset_path: Path = GOLDSET_PATH, tag: str = "") -> tuple[pd.DataFrame, pd.DataFrame, str]:
    detail_json, detail_csv, summary_csv = _output_paths(tag)
    for path in (detail_json, detail_csv, summary_csv):
        if path.exists():
            raise FileExistsError(f"기존 검증 결과를 보존합니다: {path}")
    detail_json.parent.mkdir(parents=True, exist_ok=True)
    if goldset_path.exists():
        goldset = json.loads(goldset_path.read_text(encoding="utf-8"))
    elif goldset_path == GOLDSET_PATH:
        goldset = build_goldset()
    else:
        raise FileNotFoundError(goldset_path)
    if len(goldset) < 30:
        raise ValueError("평가셋은 최소 30개 질문이 필요합니다.")
    cold_start_ms: dict[str, float] = {}
    for method_name, search in _methods().items():
        first = goldset[0]
        started = time.perf_counter()
        search(first["query"], corp_name=first["corp_name"], corp_code=first["corp_code"],
               year=first["year"], report_type=first["report_type"], top_k=5)
        cold_start_ms[method_name] = (time.perf_counter() - started) * 1000
    details: list[dict] = []
    latency: dict[str, list[float]] = {name: [] for name in _methods()}
    for gold in goldset:
        kwargs = {
            "corp_name": gold["corp_name"], "year": gold["year"],
            "corp_code": gold["corp_code"],
            "report_type": gold["report_type"], "top_k": 5,
        }
        for method_name, search in _methods().items():
            started = time.perf_counter()
            results = search(gold["query"], **kwargs)
            elapsed_ms = (time.perf_counter() - started) * 1000
            latency[method_name].append(elapsed_ms)
            for rank, result in enumerate(results, start=1):
                details.append({
                    "question_id": gold["question_id"], "query": gold["query"],
                    "question_type": gold["question_type"], "retrieval_method": method_name,
                    "rank": rank, "is_relevant": _is_relevant(result, gold),
                    "expected_rcept_no": gold["expected_rcept_no"],
                    "corp_name_expected": gold["corp_name"], "year_expected": gold["year"],
                    "report_type_expected": gold["report_type"],
                    "chunk_id": result.get("chunk_id"), "corp_name": result.get("corp_name"),
                    "corp_code": result.get("corp_code"), "year": result.get("year"),
                    "report_type": result.get("report_type"), "rcept_no": result.get("rcept_no"),
                    "section_name": result.get("section_name"),
                    "subsection_name": result.get("subsection_name"),
                    "source_file": result.get("source_file"),
                    "dense_score": result.get("dense_score"),
                    "lexical_score": result.get("lexical_score"),
                    "search_priority": result.get("search_priority"),
                    "search_priority_applied": result.get("search_priority_applied"),
                    "priority_multiplier": result.get("priority_multiplier"),
                    "final_score": result.get("final_score", result.get("score")),
                    "latency_ms": elapsed_ms,
                    "corp_match": result.get("corp_name") == gold["corp_name"],
                    "year_match": result.get("year") == gold["year"],
                    "report_type_match": result.get("report_type") == gold["report_type"],
                    "rcept_no_match": result.get("rcept_no") == gold["expected_rcept_no"],
                })
    detail_frame = pd.DataFrame(details)
    summaries = _summarize(detail_frame, goldset, latency, "all")
    # 기존 청킹이 근거를 제외한 문항을 뺀, 세 인덱스 모두 근거를 가진 공통 문항 기준 지표
    common = [gold for gold in goldset if gold.get("legacy_coverage", True)]
    if len(common) < len(goldset):
        summaries += _summarize(detail_frame, common, latency, "common_coverage")
    summary_frame = pd.DataFrame(summaries)
    overall = summary_frame[summary_frame["question_subset"].eq("all")]
    current = overall[overall["retrieval_method"].eq("feature_hashing_hybrid")].iloc[0]
    bge = overall[overall["retrieval_method"].eq("bge_m3_dense")].iloc[0]
    validation_path = PROCESSED_DIR / "quality" / "bge_m3_faiss_test_report.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {}
    decision = promotion_decision(current, bge, validation=validation, goldset=goldset)
    detail_json.write_text(
        json.dumps({"decision": decision, "goldset_path": str(goldset_path.relative_to(PROJECT_ROOT)),
                    "cold_start_ms": cold_start_ms,
                    "goldset_reviewed": all(row.get("review_status") == "reviewed" for row in goldset),
                    "results": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    detail_frame.to_csv(detail_csv, index=False, encoding="utf-8-sig")
    summary_frame.assign(decision=decision).to_csv(summary_csv, index=False, encoding="utf-8-sig")
    return detail_frame, summary_frame, decision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="검색 방식별 정답셋 비교")
    parser.add_argument("--goldset", type=Path, default=GOLDSET_PATH)
    parser.add_argument("--tag", default="", help="결과 파일명 접미사 (기존 결과 보존용)")
    args = parser.parse_args()
    goldset_path = args.goldset if args.goldset.is_absolute() else PROJECT_ROOT / args.goldset
    _, summary, decision = run_benchmark(goldset_path, args.tag)
    print(summary.to_string(index=False))
    print(f"최종 판단: {decision}")
