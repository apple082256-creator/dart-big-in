"""산업 인사이트 프롬프트 기준 검색 평가.

산업 인사이트 기능은 기업별로 매출·영업이익·설비투자·영업활동현금흐름 변화와
그 사업적 배경을 공시에서 찾는다. 검수 정답셋(v2_reviewed)에서 이 용도에 해당하는
유형만 고르고, 근거 청크는 그대로 둔 채 질의만 프롬프트 형태로 바꾼다.
질의 템플릿과 비교 방식은 결과를 보기 전에 고정했다.

비교 방식:
- feature_hashing_hybrid: 현행 (기존 filings.db)
- bge_m3_dense / bge_m3_hybrid: v2 DB, 기본 설정(0.60/0.40)
- feature_hashing_on_v2: 같은 v2 후보에 현행 점수식을 적용 (모델 효과 분리용 대조군)
- bge_m3_hybrid_w035: 현행과 같은 dense 0.35 / lexical 0.65 비율 (가중치 효과 분리용 대조군)
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd

from src.config import PROCESSED_DIR
from src.quality.vector_v2_policy import is_relevant
from src.retrieval.bge_m3_indexer import BGE_DB_PATH, search_bge_m3
from src.retrieval.lexical_scoring import effective_query, lexical_scores
from src.retrieval.local_embedding import embed_text
from src.retrieval.vector_retriever import VECTOR_DB_PATH, search_filing_context


GOLDSET_PATH = PROCESSED_DIR / "quality" / "vector_retrieval_goldset_v2_reviewed.json"
OUTPUT_DIR = PROCESSED_DIR / "quality"
DETAIL_CSV = OUTPUT_DIR / "vector_retrieval_benchmark_insight.csv"
SUMMARY_CSV = OUTPUT_DIR / "vector_retrieval_benchmark_summary_insight.csv"

# 유형 -> 프롬프트의 지표 표현
INSIGHT_TOPICS = {
    "실적 변화 원인": "매출과 영업이익",
    "현금흐름": "영업활동현금흐름",
    "설비투자": "설비투자",
    "CAPEX": "설비투자",
    "반도체 업황": "산업 업황",
    "사업 전략": "사업 전략",
}
QUERY_VARIANTS = {
    "prompt": "{corp} {year}년 {report} {metric} 변화와 관련된 사업적 배경이나 주요 이슈",
    "natural": "{corp}의 {year}년 {report} {metric}이 왜 달라졌는지 공시에서는 어떻게 설명하나?",
}
V2_COLUMNS = (
    "vector_id, chunk_id, corp_name, corp_code, year, report_type, rcept_no, section_name, "
    "subsection_name, source_file, chunk_text"
)


def _hashing_on_v2(query: str, *, corp_name, corp_code, year, report_type, top_k, _cache={}):
    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT {V2_COLUMNS} FROM filing_chunks WHERE is_searchable=1 AND corp_name=? "
            "AND corp_code=? AND year=? AND report_type=? ORDER BY vector_id",
            (corp_name, corp_code, year, report_type),
        ).fetchall()
    query_text = effective_query(query, corp_name, year)
    for row in rows:
        if row["chunk_id"] not in _cache:
            _cache[row["chunk_id"]] = embed_text(row["chunk_text"])
    matrix = np.vstack([_cache[row["chunk_id"]] for row in rows])
    scores = 0.35 * (matrix @ embed_text(query_text)) + 0.65 * lexical_scores(query_text, rows)
    return [dict(rows[int(i)]) for i in np.argsort(scores)[::-1][:top_k]]


def _methods():
    return {
        "feature_hashing_hybrid": lambda q, **kw: search_filing_context(q, **kw),
        "bge_m3_dense": lambda q, **kw: search_bge_m3(q, **kw, hybrid=False),
        "bge_m3_hybrid": lambda q, **kw: search_bge_m3(q, **kw, hybrid=True),
        "feature_hashing_on_v2": _hashing_on_v2,
        "bge_m3_hybrid_w035": lambda q, **kw: search_bge_m3(q, **kw, hybrid=True, dense_weight=0.35),
    }


def _subsections(path, chunk_ids: list[str]) -> set[tuple]:
    if not chunk_ids:
        return set()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        marks = ",".join("?" * len(chunk_ids))
        return set(connection.execute(
            f"SELECT rcept_no, section_name, subsection_name FROM filing_chunks WHERE chunk_id IN ({marks})",
            chunk_ids,
        ).fetchall())


def build_questions() -> list[dict]:
    goldset = json.loads(GOLDSET_PATH.read_text(encoding="utf-8"))
    questions = []
    for gold in goldset:
        metric = INSIGHT_TOPICS.get(gold["question_type"])
        if metric is None:
            continue
        legacy_ids = gold["acceptable_by_index"]["feature_hashing"]
        v2_ids = gold["acceptable_by_index"]["bge_m3_v2"]
        for variant, template in QUERY_VARIANTS.items():
            questions.append({
                **gold,
                "variant": variant,
                "query": template.format(corp=gold["corp_name"], year=gold["year"],
                                         report=gold["report_type"], metric=metric),
                "legacy_subsections": _subsections(VECTOR_DB_PATH, legacy_ids),
                "v2_subsections": _subsections(BGE_DB_PATH, v2_ids),
            })
    return questions


def run() -> pd.DataFrame:
    questions = build_questions()
    details = []
    for question in questions:
        kwargs = {"corp_name": question["corp_name"], "corp_code": question["corp_code"],
                  "year": question["year"], "report_type": question["report_type"], "top_k": 5}
        for method, search in _methods().items():
            subsections = question["legacy_subsections" if method == "feature_hashing_hybrid" else "v2_subsections"]
            first_hit = first_subsection = None
            for rank, result in enumerate(search(question["query"], **kwargs), start=1):
                if first_hit is None and is_relevant(result, question):
                    first_hit = rank
                key = (result.get("rcept_no"), result.get("section_name"), result.get("subsection_name"))
                if first_subsection is None and key in subsections:
                    first_subsection = rank
            details.append({
                "question_id": question["question_id"], "question_type": question["question_type"],
                "variant": question["variant"], "query": question["query"], "retrieval_method": method,
                "legacy_coverage": question["legacy_coverage"],
                "first_hit_rank": first_hit, "first_subsection_rank": first_subsection,
            })
    detail = pd.DataFrame(details)
    detail.to_csv(DETAIL_CSV, index=False, encoding="utf-8-sig")
    return detail


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    subsets = {"all": detail, "common_coverage": detail[detail["legacy_coverage"]]}
    for subset_name, frame in subsets.items():
        for (method, variant), group in frame.groupby(["retrieval_method", "variant"], sort=False):
            rank = group["first_hit_rank"]
            rows.append({
                "subset": subset_name, "retrieval_method": method, "variant": variant,
                "queries": len(group),
                "hit_at_1": float((rank == 1).mean()),
                "hit_at_3": float((rank <= 3).mean()),
                "hit_at_5": float(rank.notna().mean()),
                "mrr": float((1.0 / rank).fillna(0).mean()),
                "subsection_hit_at_5": float(group["first_subsection_rank"].notna().mean()),
            })
    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_CSV, index=False, encoding="utf-8-sig")
    return summary


if __name__ == "__main__":
    detail = run()
    summary = summarize(detail)
    pd.set_option("display.width", 200)
    print(summary.round(3).to_string(index=False))
    print("\n유형별 Hit@5 (두 질의 변형 합산):")
    print(detail.assign(hit=detail["first_hit_rank"].notna())
          .pivot_table(index="question_type", columns="retrieval_method", values="hit", aggfunc="sum")
          .to_string())
    print("\n문항 수:", detail["question_id"].nunique(), "/ 질의 수:", len(detail) // detail["retrieval_method"].nunique())
