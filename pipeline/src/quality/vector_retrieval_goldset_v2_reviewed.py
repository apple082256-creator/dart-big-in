"""원문 대조로 검수한 검색 평가 정답셋(v2 reviewed).

자동 초안(vector_retrieval_goldset_v2.json)은 키워드 일치만으로 근거를 골라
질문에 답하지 않는 근거가 다수 섞여 있었다. 이 모듈은 다음 규칙으로 정답을 고정한다.

1. 주제별로 DART 표준 목차의 해당 소제목 안에서만 근거를 찾는다.
   (예: 수주 -> II-4 매출 및 수주상황, 실적/현금흐름/재무구조 -> IV 경영진단, FY 한정)
2. 그 소제목 본문에서 질문에 직접 답하는 문장(수치·원인·대상)을 근거 구절로 지정한다.
3. 근거 구절(공백 무시)을 포함하는 청크를 v2/기존/E5 DB 전체에서 찾아 허용 ID로 연결한다.
   청크 ID 체계와 청크 크기가 달라도 같은 근거를 담은 청크는 동일하게 정답 처리된다.
   기존 인덱스에 근거가 없으면 legacy_coverage=false로 표시하고 문항은 유지한다.

질문 문장은 주제별 고정 템플릿을 사용해 어느 검색 모델의 결과도 질문 생성에 관여하지 않는다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from src.config import PROCESSED_DIR
from src.preprocess.text_chunker_v2 import CHUNKS_V2_DIR
from src.quality.vector_retrieval_goldset_v2 import TOPICS
from src.retrieval.transformer_retriever import TRANSFORMER_DB_PATH
from src.retrieval.vector_retriever import VECTOR_DB_PATH


OUTPUT_PATH = PROCESSED_DIR / "quality" / "vector_retrieval_goldset_v2_reviewed.json"
TEMPLATES = {topic: template for topic, _, template in TOPICS}
REVIEW_NOTE = (
    "주제 소제목 안에서 질문에 직접 답하는 문장을 원문 대조로 지정함. "
    "근거 구절(공백 무시)을 포함한 v2/기존/E5 청크를 허용 ID로 연결함."
)

# (question_type, corp_name, year, report_type, 근거 구절들) - 첫 구절이 대표 근거
SPEC: tuple[tuple[str, str, int, str, tuple[str, ...]], ...] = (
    ("실적 변화 원인", "SK하이닉스", 2024, "FY", ("반도체 시황 개선에 따른 주요 제품 판가 상승 영향으로 전년 32조 7,657억원 대비 102% 증가",)),
    ("실적 변화 원인", "원익IPS", 2023, "FY", ("전방 IT 수요 위축에 따른 주요 반도체, 디스플레이 고객사설비 투자 감소 영향", "R&D 비용 증가 영향으로 적자 전환")),
    ("실적 변화 원인", "한미반도체", 2023, "FY", ("1,686억원(-51.5%) 감소한 1,590억원",)),
    ("실적 변화 원인", "삼성전자", 2024, "FY", ("DS 부문의 제56기 매출은 111조 660억원으로 전년 대비 44조 4,715억원(66.8%) 증가",)),
    ("실적 변화 원인", "SK하이닉스", 2025, "FY", ("전년 66조 1,930억원 대비 47% 증가",)),
    ("현금흐름", "삼성전자", 2025, "FY", ("당기 영업활동 현금흐름으로 85조 3,151억원이 유입",)),
    ("현금흐름", "원익IPS", 2024, "FY", ("당기 영업활동 현금흐름으로 783억원이 유입",)),
    ("현금흐름", "SK하이닉스", 2023, "FY", ("현금 및 현금성 자산 합계(*) 는 8조 9,209억원", "순차입금은 20조 5,477억원")),
    ("현금흐름", "한미반도체", 2024, "FY", ("현금 및 현금성 자산이 -758억원(-42.2%) 감소",)),
    ("재무구조", "삼성전자", 2023, "FY", ("연결기준 부채비율 25.4%, 자기자본비율 79.8%",)),
    ("재무구조", "원익IPS", 2025, "FY", ("연결기준 부채비율 20.1%, 자기자본비율 83.3%, 유동비율 371.7%",)),
    ("재무구조", "한미반도체", 2025, "FY", ("부채비율은 제45기(2024년말) 31.4%에서 당기(2025년말) 17.8%로",)),
    ("재무구조", "주성엔지니어링", 2025, "FY", ("부채총계는 전기 대비 29.2% 감소한 2,974억원",)),
    ("사업 전략", "삼성전자", 2025, "Q1", ("Foundry는 시장 변화에 맞추어 노드 및 응용별 전략을 수립", "DRAM은 HBM3E 개선 제품의 초기 수요 대응을 확대")),
    ("사업 전략", "SK하이닉스", 2024, "H1", ("게임 콘솔 업체들과의 전략적 협업과 관계 강화", "Time to Market을 실현하기 위해 역량을 집중")),
    ("사업 전략", "원익IPS", 2025, "Q3", ("매출처 다변화를 통하여",)),
    ("반도체 업황", "SK하이닉스", 2023, "Q3", ("수요 및 ASP가 크게 하락", "Server향 DRAM의 수요도 급증")),
    ("반도체 업황", "삼성전자", 2023, "H1", ("메모리는 하반기에 고객사 재고 조정이 마무리되며 구매 수요는 점차 회복될 것으로 전망",)),
    ("반도체 업황", "한미반도체", 2024, "Q1", ("HBM의 수요 확대에 따른 그 수혜가 기대됩니다",)),
    ("반도체 업황", "원익IPS", 2025, "H1", ("메모리 반도체 시장이 전체 반도체 시장의 성장을 견인",)),
    ("설비투자", "삼성전자", 2024, "Q1", ("11.3조원 의 시설투자가 이루어졌습니다",)),
    ("설비투자", "삼성전자", 2023, "FY", ("53.1조원 의 시설투자가 이루어졌습니다",)),
    ("설비투자", "주성엔지니어링", 2023, "H1", ("광주 본사의 신규시설투자(생산 Capa 확대 및 생산 효율성 증대)가 완료",)),
    ("설비투자", "원익IPS", 2024, "Q3", ("[설비의 신설ㆍ매입 계획 등]해당사항 없습니다",)),
    ("CAPEX", "SK하이닉스", 2025, "Q3", ("2025년 3분기 누적 당사의 투자집행 현황",)),
    ("CAPEX", "SK하이닉스", 2024, "Q1", ("2024년 1분기 말 기준 당사의 투자집행 현황",)),
    ("CAPEX", "주성엔지니어링", 2024, "FY", ("반도체클러스터 일반산업단지 토지 취득계약",)),
    ("연구개발", "삼성전자", 2025, "Q3", ("3분기 당사의 연구개발비용은 26조 8,881억원",)),
    ("연구개발", "SK하이닉스", 2023, "Q1", ("메모리연구소 및 제품개발연구소, Nand Solution & 미래기술 연구소",)),
    ("연구개발", "한미반도체", 2025, "FY", ("총 직원의 약 30% 정도를 연구개발 인력으로 구성",)),
    ("연구개발", "원익IPS", 2023, "Q3", ("차세대 공정 및 제품을 중점적으로 발굴하고 개발하는 부서",)),
    ("수주", "원익IPS", 2025, "Q1", ("수주총액은 기초 이월된 수주잔고를 포함하여 작성",)),
    ("수주", "주성엔지니어링", 2025, "Q3", ("상기 수주상황은 당사 개별기준 정보로 작성된 것으로",)),
    ("수주", "한미반도체", 2023, "H1", ("고객사의 투자 계획 변동에 따라 수주상황은 수시로 변동",)),
    ("수주", "삼성전자", 2025, "FY", ("상기 수주총액, 계약 종료일 등은 고객사 수요 및 사업 경과에 따라 변동",)),
    ("생산능력", "삼성전자", 2023, "Q1", ("1분기 가동률은 생산능력 대비 생산실적으로 산출하였으며, 영상기기 77.1%, HHP 65.2%",)),
    ("생산능력", "SK하이닉스", 2025, "H1", ("2025년 반기 생산능력은 18,658,013백만원", "평균가동률은 100%를 유지")),
    ("생산능력", "한미반도체", 2024, "H1", ("생산능력/가동률 등을 일률적으로 산출하는 것이 큰 의미가 없어",)),
    ("시장 위험", "삼성전자", 2024, "H1", ("환율변동위험에 노출되는 주요 통화로는 USD, EUR 등",)),
    ("시장 위험", "SK하이닉스", 2023, "Q3", ("달러화, 유로화, 위안화 및 엔화와 관련된 환율변동위험",)),
    ("시장 위험", "원익IPS", 2025, "Q3", ("USD와 JPY 및기타 외화의 환위험에 노출",)),
    ("시장 위험", "주성엔지니어링", 2024, "H1", ("시장위험은 이자율위험, 환위험 및 기타 가격위험의 세가지 유형",)),
    ("원재료", "SK하이닉스", 2024, "Q3", ("원재료는 크게 웨이퍼(Wafer), Substrate, PCB",)),
    ("원재료", "삼성전자", 2025, "Q1", ("모바일AP 솔루션 가격은 전년 연간평균대비 약 19% 상승",)),
    ("원재료", "원익IPS", 2023, "H1", ("주요 원재료는 Heater, Generator 등",)),
    ("원재료", "주성엔지니어링", 2025, "Q1", ("동일한 품목의 총매입액에서 품목의 수량을 나누어 평균단가를 산출",)),
    ("제품·서비스", "삼성전자", 2024, "H1", ("2024년 반기 매출은 DX 부문이 89조 3,636억원(61.2%)",)),
    ("제품·서비스", "SK하이닉스", 2025, "Q1", ("HBM3E와 DDR5 등 고부가가치 제품 판매 확대로 10% 미만의 소폭 감소",)),
    ("제품·서비스", "원익IPS", 2024, "FY", ("반도체, Display 및 Solar Cell 제조용 장비를 생산ㆍ판매",)),
    ("제품·서비스", "주성엔지니어링", 2023, "Q3", ("SDP System(ALD&CVD), Guidance Series(ALD&CVD)",)),
)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _load_v2_chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(CHUNKS_V2_DIR.rglob("*.jsonl")):
        chunks.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line)
    return chunks


def _legacy_rows(db_path) -> list[tuple[str, str, str, int, str, str]]:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT chunk_id, rcept_no, corp_name, year, report_type, chunk_text FROM filing_chunks"
        ).fetchall()


def build_reviewed_goldset() -> list[dict]:
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"기존 정답셋을 보존합니다: {OUTPUT_PATH}")
    v2_chunks = _load_v2_chunks()
    legacy = {"feature_hashing": _legacy_rows(VECTOR_DB_PATH), "e5": _legacy_rows(TRANSFORMER_DB_PATH)}
    goldset: list[dict] = []
    problems: list[str] = []
    for number, (topic, corp, year, report_type, evidence) in enumerate(SPEC, start=1):
        group = [c for c in v2_chunks
                 if c["corp_name"] == corp and int(c["year"]) == year and c["report_type"] == report_type]
        needles = [compact(text) for text in evidence]
        primary = [c for c in group if needles[0] in compact(c["chunk_text"])]
        if not primary:
            problems.append(f"{number}: v2 청크에서 대표 근거를 찾지 못함 ({corp} {year} {report_type})")
            continue
        rcept_no = primary[0]["rcept_no"]
        v2_ids = [c["chunk_id"] for c in group
                  if c["rcept_no"] == rcept_no and any(n in compact(c["chunk_text"]) for n in needles)]
        legacy_ids: dict[str, list[str]] = {}
        for name, rows in legacy.items():
            legacy_ids[name] = [chunk_id for chunk_id, receipt, *_, text in rows
                                if receipt == rcept_no and any(n in compact(text) for n in needles)]
        acceptable = list(dict.fromkeys(v2_ids + legacy_ids["feature_hashing"] + legacy_ids["e5"]))
        goldset.append({
            "question_id": f"V2R-{number:03d}",
            "query": TEMPLATES[topic].format(corp=corp, year=year, report=report_type),
            "question_type": topic,
            "corp_name": corp,
            "corp_code": primary[0]["corp_code"],
            "year": year,
            "report_type": report_type,
            "expected_rcept_no": rcept_no,
            "expected_chunk_ids": [c["chunk_id"] for c in primary],
            "expected_sections": sorted({f"{c['section_name']} / {c.get('subsection_name') or ''}".rstrip(" /")
                                         for c in primary}),
            "acceptable_chunk_ids": acceptable,
            "acceptable_by_index": {"bge_m3_v2": v2_ids, **legacy_ids},
            # 기존 청킹은 숫자·표 중심 청크를 품질 필터로 제외해 근거 자체가 인덱스에 없을 수 있다.
            "legacy_coverage": all(legacy_ids.values()),
            "evidence_text": evidence[0],
            "evidence_passages": list(evidence),
            "review_status": "reviewed",
            "review_method": "rule+source_text_check",
            "review_note": REVIEW_NOTE,
        })
    if problems:
        raise RuntimeError("정답셋 검증 실패:\n" + "\n".join(problems))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(goldset, ensure_ascii=False, indent=2), encoding="utf-8")
    return goldset


if __name__ == "__main__":
    rows = build_reviewed_goldset()
    print(f"검수 정답셋 생성: {len(rows)}개 질문 -> {OUTPUT_PATH}")
    print("유형:", dict(Counter(r["question_type"] for r in rows)))
    print("기업:", dict(Counter(r["corp_name"] for r in rows)))
    print("기존 인덱스 근거 없음:", [r["question_id"] for r in rows if not r["legacy_coverage"]])
    print("보고서:", dict(Counter(r["report_type"] for r in rows)), "연도:", dict(Counter(r["year"] for r in rows)))
