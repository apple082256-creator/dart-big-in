from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

import numpy as np


HIGH_VALUE_TERMS = (
    "사업", "제품", "서비스", "시장", "경쟁", "연구개발", "설비", "투자",
    "위험", "재무", "계약", "전략", "매출", "수주", "원재료", "생산",
)
STOP_TERMS = {"관련", "내용", "현황", "대한", "보고서"}


def _value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except (KeyError, IndexError):
        return ""


def effective_query(query: str, corp_name: str | None = None, year: int | None = None) -> str:
    text = query
    if corp_name:
        text = text.replace(corp_name, "")
    if year:
        text = text.replace(str(year), "")
    return re.sub(r"\s+", " ", text).strip()


def lexical_scores(query: str, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
    # sqlite3.Row supports key lookup but has no .get method.
    rows = [dict(row) for row in rows]
    terms = {
        term for term in re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}|[0-9]+", query.lower())
        if term not in STOP_TERMS
    }
    terms.update(term.lower() for term in HIGH_VALUE_TERMS if term in query)
    scores = np.zeros(len(rows), dtype=np.float32)
    if not terms or not rows:
        return scores

    documents = [
        " ".join(filter(None, (
            str(_value(row, "section_name") or ""),
            str(_value(row, "subsection_name") or ""),
            str(_value(row, "chunk_text") or ""),
        ))).lower()
        for row in rows
    ]
    document_frequency = {term: sum(term in document for document in documents) for term in terms}
    for index, (row, document) in enumerate(zip(rows, documents)):
        heading = " ".join(filter(None, (
            str(_value(row, "section_name") or ""), str(_value(row, "subsection_name") or ""),
        ))).lower()
        score = 0.0
        for term in terms:
            frequency = min(document.count(term), 5)
            heading_hit = 2.0 if term in heading else 0.0
            inverse_frequency = math.log((len(rows) + 1) / (document_frequency[term] + 1)) + 1.0
            score += inverse_frequency * (math.log1p(frequency) + heading_hit)
        scores[index] = score
    maximum = float(scores.max(initial=0.0))
    return scores / maximum if maximum else scores
