from __future__ import annotations

import sys
from pathlib import Path

# Streamlit executes this file with ``app/`` as the first import location.
# Add the repository root so ``src`` imports work regardless of the shell cwd.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from src.llm.analyzer import analyze_company
from src.retrieval.bge_m3_indexer import validate_bge_operational_assets
from src.retrieval.operational_config import (
    BGE_M3_DENSE,
    EXPECTED_BGE_COMPANIES,
    FEATURE_HASHING_HYBRID,
    SUPPORTED_FILING_RETRIEVERS,
    get_filing_retriever_settings,
)


# value_source comes from CAPEX rows (direct/derived) and finance metric rows
# (direct/derived/document_extracted); plain financial_facts rows have none.
VALUE_SOURCE_LABELS = {
    "direct": "직접 공시",
    "derived": "계산값",
    "document_extracted": "공시 원문(XML) 추출",
}
REVIEW_STATUS_LABELS = {"approved": "승인", "review_required": "검토 필요"}
VALUE_TYPE_LABELS = {
    "quarterly": "3개월",
    "cumulative": "누적",
    "annual": "연간",
    "point_in_time": "시점 잔액",
}


st.set_page_config(page_title="DART Beginner", layout="wide")
st.title("DART 재무·공시 통합 분석")
st.caption("Financial DB 숫자와 동일 rcept_no의 공시 원문 근거를 함께 조회합니다.")

try:
    retriever_settings = get_filing_retriever_settings()
except ValueError as exc:
    st.error(str(exc))
    st.stop()

try:
    bge_assets = validate_bge_operational_assets()
except Exception as exc:
    bge_assets = None
    if retriever_settings.retriever == BGE_M3_DENSE and not retriever_settings.auto_fallback:
        st.error(f"BGE-M3 운영 자산 검증 실패: {type(exc).__name__}: {exc}")
        st.stop()

supported_companies = tuple(bge_assets["companies"]) if bge_assets else EXPECTED_BGE_COMPANIES
retrieval_options = SUPPORTED_FILING_RETRIEVERS
default_retriever_index = retrieval_options.index(retriever_settings.retriever)

with st.sidebar:
    corp_name = st.selectbox("기업", supported_companies)
    year = st.selectbox("사업연도", (2025, 2024, 2023))
    analysis_period = st.selectbox(
        "분석 주기",
        ("single_report", "quarterly_trend"),
        format_func=lambda value: {
            "single_report": "선택 보고서",
            "quarterly_trend": "분기별 추이 (Q1~Q4 자동)",
        }[value],
    )
    report_type = st.selectbox(
        "보고서", ("FY", "Q3", "H1", "Q1"),
        disabled=analysis_period == "quarterly_trend",
    )
    if analysis_period == "quarterly_trend":
        st.caption("Q1·H1·Q3·FY 보고서의 분기 단독값을 자동으로 결합합니다.")
    retrieval_method = st.selectbox(
        "검색 방식",
        retrieval_options,
        index=default_retriever_index,
        format_func=lambda value: {
            BGE_M3_DENSE: "BGE-M3 Dense (운영 기본)",
            FEATURE_HASHING_HYBRID: "Feature Hashing Hybrid (Legacy / rollback)",
        }[value],
    )
    st.caption(
        f"환경 기본값: {retriever_settings.retriever} · "
        f"자동 fallback: {'ON' if retriever_settings.auto_fallback else 'OFF'}"
    )

query = st.text_area("질문", "삼성전자 2025년 실적 변화와 그 원인을 공시 근거와 함께 설명해줘.")
if st.button("분석", type="primary"):
    with st.spinner("Financial DB와 공시 원문을 조회하고 있습니다."):
        result = analyze_company(
            query, corp_name=corp_name, year=year, report_type=report_type,
            retrieval_method=retrieval_method, analysis_period=analysis_period,
        )
    st.subheader("라우팅")
    st.json({
        "route": result["route"],
        "analysis_period": result["analysis_period"],
        "years": result["years"],
        "financial_db_used": result["financial_db_used"],
        "vector_db_used": result["vector_db_used"],
        "financial_fallback_reason": result["financial_fallback_reason"],
        "requested_retriever": result["requested_retrieval_method"],
        "effective_retriever": result["effective_retrieval_method"],
        "fallback_reason": result["retrieval_fallback_reason"],
        "llm_called": result["llm_called"],
    })
    if result["retrieval_fallback_reason"]:
        st.warning(
            "BGE-M3 검색 실패로 명시적으로 활성화된 legacy fallback을 사용했습니다: "
            + result["retrieval_fallback_reason"]
        )
    st.subheader("근거 기반 미리보기")
    st.markdown(result["answer"])
    with st.expander("재무 출처"):
        st.json(result["financial_sources"])
        financial_values = [
            {
                "분기": row.get("quarter_label"),
                "보고서": row.get("report_type"),
                "값 구분": VALUE_TYPE_LABELS.get(row.get("value_type"), row.get("value_type")),
                "계정": row.get("standard_account_nm"),
                "원본": f"{row.get('raw_value')} {row.get('raw_unit')}",
                "환산 원화": row.get("display_value_krw"),
                "비율": row.get("display_value_percent"),
                "조원 표시": row.get("display_value_trillion"),
                "범위": row.get("scope"),
                "산출 방식": VALUE_SOURCE_LABELS.get(row.get("value_source"), row.get("value_source")),
                "검토 상태": REVIEW_STATUS_LABELS.get(row.get("review_status"), row.get("review_status")),
                "계산 여부": bool(row.get("is_calculated")),
                "구성 계정": row.get("source_account_names"),
                "접수번호": row.get("rcept_no"),
            }
            for row in result["financial_evidence"]
            if row.get("display_value_krw") or row.get("display_value_percent")
        ]
        if financial_values:
            st.dataframe(financial_values, width="stretch", hide_index=True)
    with st.expander("공시 출처"):
        st.json(result["filing_sources"])
        filing_values = [
            {
                "항목": fact.get("label") or "본문 금액",
                "원본": fact.get("original_text"),
                "환산 원화": fact.get("display_value_krw"),
                "조원 표시": fact.get("display_value_trillion"),
                "접수번호": row.get("rcept_no"),
            }
            for row in result["filing_evidence"]
            for fact in row.get("monetary_facts", [])
        ]
        if filing_values:
            st.caption("원본 공시 단위와 Python 코드가 환산한 값을 함께 표시합니다.")
            st.dataframe(filing_values, width="stretch", hide_index=True)
    with st.expander("LLM 전달용 프롬프트"):
        st.code(result["prompt"])
