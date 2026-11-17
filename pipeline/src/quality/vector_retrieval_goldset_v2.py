from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from src.config import DEFAULT_COMPANIES, PROCESSED_DIR
from src.preprocess.text_chunker_v2 import CHUNKS_V2_DIR
from src.retrieval.vector_retriever import VECTOR_DB_PATH


OUTPUT_PATH = PROCESSED_DIR / "quality" / "vector_retrieval_goldset_v2.json"

TOPICS = (
    ("실적 변화 원인", ("매출", "감소", "증가", "실적"), "{corp}의 {year}년 {report} 실적 변화 원인은 무엇인가?"),
    ("사업 전략", ("전략", "사업", "경쟁력"), "{corp}의 {year}년 {report} 사업 전략은 무엇인가?"),
    ("반도체 업황", ("반도체", "업황", "시장"), "{corp}의 {year}년 {report} 반도체 업황 설명은 무엇인가?"),
    ("설비투자", ("설비투자", "시설투자", "설비"), "{corp}의 {year}년 {report} 설비투자 내용은 무엇인가?"),
    ("CAPEX", ("유형자산", "취득", "투자"), "{corp}의 {year}년 {report} CAPEX 관련 공시 근거는 무엇인가?"),
    ("연구개발", ("연구개발", "R&D", "개발"), "{corp}의 {year}년 {report} 연구개발 활동은 무엇인가?"),
    ("수주", ("수주", "계약", "수주상황"), "{corp}의 {year}년 {report} 수주 현황은 무엇인가?"),
    ("생산능력", ("생산능력", "생산실적", "가동률"), "{corp}의 {year}년 {report} 생산능력과 가동 현황은 무엇인가?"),
    ("시장 위험", ("위험", "시장", "환율"), "{corp}의 {year}년 {report} 주요 시장 위험은 무엇인가?"),
    ("원재료", ("원재료", "조달", "가격"), "{corp}의 {year}년 {report} 원재료 조달과 가격 위험은 무엇인가?"),
    ("제품·서비스", ("제품", "서비스", "매출"), "{corp}의 {year}년 {report} 주요 제품과 서비스는 무엇인가?"),
    ("현금흐름", ("현금흐름", "영업활동", "투자활동"), "{corp}의 {year}년 {report} 현금흐름 관련 설명은 무엇인가?"),
    ("재무구조", ("부채", "자본", "재무구조"), "{corp}의 {year}년 {report} 재무구조 관련 설명은 무엇인가?"),
)


def _load_chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(CHUNKS_V2_DIR.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("corp_name") in DEFAULT_COMPANIES:
                chunks.append(chunk)
    return chunks


def build_goldset(limit: int = 50) -> list[dict]:
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"기존 정답셋을 보존합니다: {OUTPUT_PATH}")
    chunks = _load_chunks()
    if not chunks:
        raise RuntimeError("chunks_v2가 없습니다.")
    groups: dict[tuple[str, str, int, str, str], list[dict]] = {}
    for chunk in chunks:
        key = (
            chunk["corp_name"], chunk["corp_code"], int(chunk["year"]),
            chunk["report_type"], chunk["rcept_no"],
        )
        groups.setdefault(key, []).append(chunk)

    candidates: list[tuple[tuple, str, tuple[str, ...], str, dict, list[dict]]] = []
    for key, group in sorted(groups.items()):
        corp, _, year, report, _ = key
        for topic, terms, template in TOPICS:
            scored = []
            for chunk in group:
                document = chunk.get("chunk_text", "")
                hits = [term for term in terms if term.lower() in document.lower()]
                if not hits:
                    continue
                heading = f"{chunk.get('section_name', '')} {chunk.get('subsection_name') or ''}"
                score = len(hits) + sum(term.lower() in heading.lower() for term in hits) + float(chunk["search_priority"])
                scored.append((score, chunk, hits))
            if not scored:
                continue
            scored.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
            selected = scored[0][1]
            evidence_terms = tuple(scored[0][2])
            acceptable = [
                chunk for _, chunk, hits in scored
                if chunk["section_name"] == selected["section_name"] and set(hits) & set(evidence_terms)
            ][:5]
            query = template.format(corp=corp, year=year, report=report)
            candidates.append((key, topic, evidence_terms, query, selected, acceptable))

    # 기업·연도·보고서·질문 유형이 한쪽으로 쏠리지 않도록 라운드로빈 선택한다.
    selected_rows: list[tuple] = []
    used_groups: set[tuple] = set()
    # Interleave companies and rotate topics; lexicographic truncation biases coverage.
    by_company: dict[str, list[tuple]] = {}
    for key in sorted(groups):
        options = [candidate for candidate in candidates if candidate[0] == key]
        if options:
            company_options = by_company.setdefault(key[0], [])
            company_options.append(options[len(company_options) % len(options)])
    balanced = []
    for offset in range(max(map(len, by_company.values()), default=0)):
        for company in sorted(by_company):
            if offset < len(by_company[company]):
                balanced.append(by_company[company][offset])
    for candidate in balanced:
        group_key = candidate[0][:4]
        if group_key not in used_groups:
            selected_rows.append(candidate)
            used_groups.add(group_key)
            if len(selected_rows) >= limit:
                break
    if len(selected_rows) < limit:
        used = {(item[0], item[1]) for item in selected_rows}
        for candidate in candidates:
            marker = (candidate[0], candidate[1])
            if marker not in used:
                selected_rows.append(candidate)
                used.add(marker)
                if len(selected_rows) >= limit:
                    break

    goldset: list[dict] = []
    with sqlite3.connect(f"file:{VECTOR_DB_PATH}?mode=ro", uri=True) as connection:
        legacy_rows = connection.execute("SELECT chunk_id,rcept_no,chunk_text FROM filing_chunks").fetchall()
    def normalized(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()
    for number, (key, topic, terms, query, selected, acceptable) in enumerate(selected_rows, start=1):
        corp, corp_code, year, report_type, rcept_no = key
        body = selected["chunk_text"]
        anchor = next((line.strip() for line in body.splitlines()
                       if len(line.strip()) >= 80 and any(term.lower() in line.lower() for term in terms)), body)
        evidence = anchor[:180]
        needle = normalized(evidence)
        accepted_ids = [chunk["chunk_id"] for chunk in groups[key]
                        if needle and needle in normalized(chunk["chunk_text"])]
        accepted_ids.extend(chunk_id for chunk_id, receipt, text in legacy_rows
                            if receipt == rcept_no and needle and needle in normalized(text))
        goldset.append({
            "question_id": f"V2-{number:03d}",
            "query": query,
            "question_type": topic,
            "corp_name": corp,
            "corp_code": corp_code,
            "year": year,
            "report_type": report_type,
            "expected_rcept_no": rcept_no,
            "expected_chunk_ids": [selected["chunk_id"]],
            "expected_sections": [selected["section_name"]],
            "acceptable_chunk_ids": accepted_ids,
            "evidence_terms": list(terms),
            "evidence_text": evidence,
            "review_status": "draft",
            "review_note": "본문 용어로 생성한 검토 후보. 같은 근거 구절을 포함한 기존/v2 ID를 명시적으로 연결함. 질문에 대한 충분한 답인지 원문 검토 후 reviewed로 지정해야 승격 가능.",
        })
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(goldset, ensure_ascii=False, indent=2), encoding="utf-8")
    return goldset


if __name__ == "__main__":
    rows = build_goldset()
    print(f"검색 평가 정답셋 생성: {len(rows)}개 질문 -> {OUTPUT_PATH}")
