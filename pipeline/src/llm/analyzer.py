from __future__ import annotations

import re
from collections.abc import Callable

from src.financial_units import extract_monetary_facts, format_eok, format_krw, format_trillion
from src.retrieval.financial_retriever import get_financial_context, get_financial_metrics
from src.retrieval.industry_kpi import get_industry_kpis_by_index
from src.retrieval.operational_retriever import retrieve_filing_context


NUMERIC_TERMS = ("얼마", "매출", "영업이익", "순이익", "자산", "부채", "현금흐름", "설비투자", "CAPEX", "연구개발", "R&D", "증가", "감소", "대비", "수치", "실적", "이자수익", "이자비용", "순이자", "수수료", "대출채권", "예수부채", "신용손실", "대손", "NIM", "BIS", "CET1", "고정이하여신", "연체율", "건전성")
EXPLANATION_TERMS = ("원인", "이유", "영향", "위험", "전략", "성장", "동력", "전망", "사업", "설명")
ACCOUNT_TERMS = ("매출액", "영업이익", "순이익", "자산총계", "부채총계", "자본총계", "현금흐름", "유형자산", "설비투자", "CAPEX", "연구개발", "R&D", "이자수익", "이자비용", "순이자", "수수료", "대출채권", "예수부채", "신용손실", "대손")
QUARTER_REPORTS = (("Q1", 1), ("H1", 2), ("Q3", 3), ("FY", 4))


def _finance_metric_context(
    corp_name: str,
    year: int,
    report_type: str,
    account_name: str | None = None,
) -> list[dict]:
    """Adapt the finance metric layer to the existing analyzer evidence shape."""
    metrics = get_financial_metrics(corp_name, year, report_type)
    if account_name:
        needle = account_name.casefold().replace(" ", "")
        aliases = {
            "연구개발": {"RND_EXPENSE", "RND_TO_REVENUE"},
            "r&d": {"RND_EXPENSE", "RND_TO_REVENUE"},
            "순이자": {"NET_INTEREST_INCOME"},
            "이자수익": {"INTEREST_INCOME"},
            "이자비용": {"INTEREST_EXPENSE"},
            "수수료": {"FEE_COMMISSION_INCOME", "FEE_COMMISSION_EXPENSE", "NET_FEE_COMMISSION_INCOME"},
            "대출채권": {"LOANS_AT_AMORTISED_COST", "LOANS_AT_AMORTISED_COST_NOTE"},
            "예수부채": {"CUSTOMER_DEPOSITS"},
            "신용손실": {"CREDIT_LOSS_EXPENSE", "LOSS_ALLOWANCE"},
            "대손": {"CREDIT_LOSS_EXPENSE", "LOSS_ALLOWANCE"},
            "순이익": {"NET_INCOME", "OWNERS_NET_INCOME"},
            "영업이익": {"OPERATING_INCOME"},
            "자산총계": {"TOTAL_ASSETS"},
            "부채총계": {"TOTAL_LIABILITIES"},
            "자본총계": {"TOTAL_EQUITY"},
        }
        codes = set().union(*(value for key, value in aliases.items() if key in needle))
        if codes:
            metrics = [row for row in metrics if row["metric_code"] in codes]
        else:
            metrics = [
                row for row in metrics
                if needle in str(row["metric_name"]).casefold().replace(" ", "")
            ]
    return _adapt_metric_rows(metrics)


def _adapt_metric_rows(metrics: list[dict]) -> list[dict]:
    adapted = []
    for row in metrics:
        derived = row["source_kind"] == "DERIVED"
        adapted.append({
            "corp_name": row.get("corp_name") or row.get("company_name"), "corp_code": row["corp_code"],
            "year": row["year"], "report_type": row["report_type"],
            "rcept_no": row["source_rcept_no"], "sj_div": row["statement_type"] or "NOTE",
            "account_id": row["source_account_id"] or row["metric_code"],
            "original_account_nm": row["source_account_name"] or row["metric_name"],
            "standard_account_nm": row["metric_name"],
            "period_type": row["period_nature"], "value_type": row["value_type"],
            "period_start": None, "period_end": None,
            "currency": row["unit"], "raw_value": row["value"],
            "normalized_value": None if derived else row["value"],
            "calculated_value": row["value"] if derived else None,
            "calculation_method": row["calculation_method"],
            "source_values": row["source_values"], "is_calculated": int(derived),
            "fs_div": row["fs_div"], "scope": row["scope"],
            "value_source": row["source_kind"].lower(),
            "is_derived": int(derived), "metric_code": row["metric_code"],
            "review_status": row["review_status"],
        })
    return adapted


def _financial_rows_for_account(
    corp_name: str, year: int, report_type: str, account_name: str | None,
) -> list[dict]:
    finance_rows = _finance_metric_context(corp_name, year, report_type, account_name)
    if finance_rows:
        return finance_rows
    return get_financial_context(corp_name, year, report_type, account_name=account_name)


def route_question(query: str) -> str:
    numeric = any(term in query for term in NUMERIC_TERMS)
    explanation = any(term in query for term in EXPLANATION_TERMS)
    if numeric and explanation:
        return "integrated"
    if numeric:
        return "financial"
    return "filing" if explanation else "integrated"


def _years(query: str, fallback: int | None) -> list[int]:
    mentions = re.findall(r"(?<!\d)(20\d{2}|\d{2})(?=\s*년)", query)
    found = sorted({int(value) if len(value) == 4 else 2000 + int(value) for value in mentions})
    if len(found) >= 2 and any(marker in query for marker in ("부터", "까지", "~", "∼")):
        found = list(range(min(found), max(found) + 1))
    years = found or ([fallback] if fallback else [])
    if len(found) <= 1 and ("전년" in query or "대비" in query or "변화" in query):
        years = sorted(set(years + [year - 1 for year in years]))
    return years


def _account_filter(query: str) -> list[str]:
    terms = [term for term in ACCOUNT_TERMS if term in query]
    if "매출" in query and "매출액" not in terms:
        terms.append("매출액")
    if "설비투자" in terms or "CAPEX" in terms:
        terms = [term for term in terms if term not in {"설비투자", "CAPEX"}]
        terms.append("유형자산취득(CAPEX)")
    if "실적" in query and not terms:
        terms.extend(["매출", "영업이익", "순이익"])
    return list(dict.fromkeys(terms))


def _financial_context(query: str, corp_name: str, years: list[int], report_type: str) -> list[dict]:
    rows: list[dict] = []
    accounts = _account_filter(query)
    healthcare_comparison = (
        ("헬스케어" in query or any(marker in query for marker in ("4사", "5개사", "5개 기업")))
        and any(term in query for term in ("연구개발", "R&D"))
    )
    if healthcare_comparison:
        metric_codes = ("RND_EXPENSE", "RND_TO_REVENUE")
        for year in years:
            rows.extend(_adapt_metric_rows(get_industry_kpis_by_index(
                "KRX_HEALTH", year=year, report_type=report_type, metric_codes=metric_codes,
            )))
        accounts = []
    for year in years:
        if healthcare_comparison:
            continue
        if accounts:
            for account in accounts:
                rows.extend(_financial_rows_for_account(corp_name, year, report_type, account))
        else:
            rows.extend(_financial_rows_for_account(corp_name, year, report_type, None))
    statement_order = {"CIS": 0, "IS": 1, "CF": 2, "BS": 3}
    if report_type == "FY" and not any(term in query for term in ("분기", "Q4", "4분기")):
        rows = [row for row in rows if row["value_type"] in {"annual", "point_in_time", "ratio"}]
    if any(term in query for term in ("2분기", "Q2", "2Q")):
        rows = [row for row in rows if row["value_type"] == "quarterly"]
    rows.sort(key=lambda row: (row["year"], statement_order.get(row["sj_div"], 9), row.get("standard_account_nm") or ""))
    unique: dict[tuple, dict] = {}
    for row in rows:
        statement_group = "income" if row["sj_div"] in {"IS", "CIS"} else row["sj_div"]
        key = (
            row["corp_code"], row["year"], row["report_type"], row["rcept_no"],
            statement_group, row["account_id"], row["value_type"], row["is_calculated"],
        )
        unique.setdefault(key, row)
    selected = list(unique.values())
    return selected[:80]


def _quarterly_financial_context(query: str, corp_name: str, years: list[int]) -> list[dict]:
    """Collect comparable standalone quarter values from Q1, H1, Q3, and FY reports."""
    rows: list[dict] = []
    accounts = _account_filter(query)
    for year in years:
        period_rows_by_report: dict[str, list[dict]] = {}
        for report_type, quarter in QUARTER_REPORTS:
            period_rows: list[dict] = []
            if accounts:
                for account in accounts:
                    period_rows.extend(
                        _financial_rows_for_account(corp_name, year, report_type, account)
                    )
            else:
                period_rows.extend(_financial_rows_for_account(corp_name, year, report_type, None))
            period_rows_by_report[report_type] = period_rows
            for row in period_rows:
                if row["value_type"] not in {"quarterly", "point_in_time"}:
                    continue
                enriched = dict(row)
                enriched["quarter"] = quarter
                enriched["quarter_label"] = f"{year} Q{quarter}"
                rows.append(enriched)

        # Older source data can lack a materialized Q4 row. Keep the derivation
        # traceable by calculating only when matching FY annual and Q3 cumulative
        # values are both present.
        existing_q4 = {
            (row["sj_div"], row["account_id"])
            for row in rows
            if row["year"] == year and row["quarter"] == 4 and row["value_type"] == "quarterly"
        }
        q3_cumulative = {
            (row["sj_div"], row["account_id"]): row
            for row in period_rows_by_report.get("Q3", [])
            if row["value_type"] == "cumulative" and row["normalized_value"] is not None
        }
        for annual in period_rows_by_report.get("FY", []):
            key = (annual["sj_div"], annual["account_id"])
            q3 = q3_cumulative.get(key)
            if (
                key in existing_q4
                or annual["value_type"] != "annual"
                or annual["normalized_value"] is None
                or q3 is None
            ):
                continue
            # Opposite signs usually mean the FY and Q3 reports present the
            # line with different conventions; subtracting would fabricate Q4.
            if annual["normalized_value"] and q3["normalized_value"] and (
                (annual["normalized_value"] > 0) != (q3["normalized_value"] > 0)
            ):
                continue
            q4 = dict(annual)
            q4.update({
                "period_type": "quarterly",
                "value_type": "quarterly",
                "normalized_value": None,
                "calculated_value": annual["normalized_value"] - q3["normalized_value"],
                "calculation_method": "FY annual - Q3 cumulative",
                "source_values": f"{annual['normalized_value']} - {q3['normalized_value']}",
                "is_calculated": 1,
                "quarter": 4,
                "quarter_label": f"{year} Q4",
            })
            rows.append(q4)

    statement_order = {"CIS": 0, "IS": 1, "CF": 2, "BS": 3}
    rows.sort(key=lambda row: (
        row["year"], row["quarter"], statement_order.get(row["sj_div"], 9),
        row.get("standard_account_nm") or "",
    ))
    unique: dict[tuple, dict] = {}
    for row in rows:
        statement_group = "income" if row["sj_div"] in {"IS", "CIS"} else row["sj_div"]
        key = (
            row["corp_code"], row["year"], row["quarter"], statement_group,
            row["account_id"], row["value_type"], row["is_calculated"],
        )
        unique.setdefault(key, row)
    return list(unique.values())[:200]


def _format_financial(rows: list[dict]) -> str:
    lines = []
    for row in rows:
        value = row["calculated_value"] if row["is_calculated"] else row["normalized_value"]
        unit = row.get("currency") or "KRW"
        display = f"{value / 100:.2f}%" if unit == "BPS" and value is not None else format_krw(value)
        lines.append(
            f"- {row['corp_name']} | {row.get('quarter_label') or row['year']} | "
            f"{row['report_type']} | {row['rcept_no']} | "
            f"{row['sj_div']} | {row['standard_account_nm']} ({row['original_account_nm']}) | "
            f"{row['value_type']} | normalized_value={value} {unit} | display_value={display} | "
            f"fs_div={row['fs_div']} | scope={row.get('scope')} | "
            f"is_calculated={bool(row['is_calculated'])} | "
            f"value_source={row.get('value_source', 'reported')} | "
            f"review_status={row.get('review_status')} | method={row['calculation_method']} | "
            f"source_values={row.get('source_values')}"
        )
    return "\n".join(lines) or "- 조회된 재무 데이터 없음"


def _format_filings(rows: list[dict]) -> str:
    formatted = []
    for row in rows:
        normalized = ""
        if row.get("monetary_facts"):
            fact_lines = [
                f"  - {fact.get('label') or '본문 금액'}: 원본={fact['original_text']} | "
                f"normalized_value={fact['normalized_value']} KRW | "
                f"display_krw={fact['display_value_krw']} | "
                f"display_trillion={fact['display_value_trillion']} | "
                f"formula={fact['conversion_formula']}"
                for fact in row["monetary_facts"]
            ]
            normalized = "\n[Python 코드로 확정한 금액 — 재계산 금지]\n" + "\n".join(fact_lines)
        formatted.append(
            f"- {row['corp_name']} | {row['year']} | {row['report_type']} | {row['rcept_no']} | "
            f"{row['section_name']} / {row.get('subsection_name')}"
            f"{normalized}\n[공시 원문 청크]\n  {row['chunk_text']}"
        )
    return "\n".join(formatted) or "- 검색된 공시 근거 없음"


def _enrich_evidence(financial_rows: list[dict], filing_rows: list[dict]) -> None:
    for row in financial_rows:
        value = row["calculated_value"] if row["is_calculated"] else row["normalized_value"]
        if value is not None:
            unit = row.get("currency") or "KRW"
            row["raw_unit"] = unit
            row["normalized_unit"] = unit
            row["unit_conversion_applied"] = False
            row["conversion_formula"] = "value × 1"
            if unit == "BPS":
                row["display_value_percent"] = f"{value / 100:.2f}%"
            else:
                row["display_value_krw"] = format_krw(value)
                row["display_value_eok"] = format_eok(value)
                row["display_value_trillion"] = format_trillion(value)
    for row in filing_rows:
        row["monetary_facts"] = extract_monetary_facts(row.get("chunk_text") or "")


def _grounded_preview(financial_rows: list[dict], filing_rows: list[dict]) -> str:
    parts = []
    if financial_rows:
        parts.append("재무 DB에서 확인된 항목:\n" + _format_financial(financial_rows[:8]))
    if filing_rows:
        parts.append("관련 공시 섹션:\n" + "\n".join(
            f"- {row['section_name']} / {row.get('subsection_name')} ({row['rcept_no']})"
            for row in filing_rows
        ))
    return "\n\n".join(parts) or "조회된 근거가 없습니다."


def _remove_unsupported_acronym_expansions(answer: str, filing_rows: list[dict]) -> tuple[str, list[str]]:
    evidence_text = "\n".join(str(row.get("chunk_text") or "") for row in filing_rows)
    removed: list[str] = []

    def replace(match: re.Match) -> str:
        acronym, expansion = match.group(1), match.group(2).strip()
        if expansion and re.search(r"[가-힣]", expansion) and not re.search(r"\d", expansion) and expansion not in evidence_text:
            removed.append(match.group(0))
            return acronym
        return match.group(0)

    cleaned = re.sub(r"\b([A-Z][A-Z0-9-]{1,})\s*\(([^)]+)\)", replace, answer)
    return cleaned, removed


def analyze_company(
    query: str,
    corp_name: str,
    year: int | None = None,
    report_type: str = "FY",
    retrieval_method: str | None = None,
    llm_client: Callable[[str], str] | None = None,
    analysis_period: str = "single_report",
) -> dict:
    if analysis_period not in {"single_report", "quarterly_trend"}:
        raise ValueError(f"지원하지 않는 분석 주기입니다: {analysis_period}")
    if any(term in query for term in ("분기별", "분기 추이", "분기별 추이")):
        analysis_period = "quarterly_trend"
    route = route_question(query)
    years = _years(query, year)
    use_financial = route in {"financial", "integrated"}
    use_vector = route in {"filing", "integrated"}
    if use_financial and analysis_period == "quarterly_trend":
        financial_rows = _quarterly_financial_context(query, corp_name, years)
    else:
        financial_rows = _financial_context(query, corp_name, years, report_type) if use_financial else []
    financial_fallback_reason = None
    if use_financial and not financial_rows:
        use_vector = True
        financial_fallback_reason = (
            "rnd_disclosure_exists_but_standard_metric_not_loaded_use_filing"
            if "연구개발" in query or "R&D" in query.upper()
            else "financial_metric_not_available_use_filing"
        )
    filing_year = max(years) if years else year
    retrieval = None
    if use_vector:
        retrieval = retrieve_filing_context(
            query,
            corp_name=corp_name,
            year=filing_year,
            report_type=report_type,
            top_k=5,
            retriever=retrieval_method,
        )
        filing_rows = retrieval.rows
    else:
        filing_rows = []
    if financial_rows and filing_rows:
        valid_receipts = {row["rcept_no"] for row in financial_rows if row["year"] == filing_year}
        filing_rows = [row for row in filing_rows if row["rcept_no"] in valid_receipts]
    _enrich_evidence(financial_rows, filing_rows)

    facility_rules = ""
    if "설비투자" in query:
        required_facts = [
            fact for row in filing_rows for fact in row.get("monetary_facts", [])
            if fact.get("source_type") == "facility_investment_table"
        ]
        required_lines = "\n".join(
            f"  - {fact['label']}: 원본 {fact['original_text']} = {fact['display_value_krw']} ({fact['display_value_trillion']})"
            for fact in required_facts
        )
        if required_facts:
            facility_rules = (
                "\n[설비투자 질문 특별 규칙]\n"
                "- 아래 네 금액을 하나도 생략하지 말고 표시된 문자열 그대로 답변에 포함한다.\n"
                f"{required_lines}\n"
                "- 전략 연결은 차세대 메모리 기술 경쟁력, 중장기 수요 대비, Advanced 노드 CAPA 확보, "
                "투자 효율성 제고라는 원문 표현 범위만 사용한다.\n"
                "- 공시에 직접 없는 수익성 효과, 시장 효과, 미래 산업 효과 또는 약어 풀이를 추가하지 않는다.\n"
            )

    capex_rules = ""
    if "CAPEX" in query or "설비투자" in query:
        capex_rules = (
            "\n[CAPEX DB 규칙]\n"
            "- value_source, is_derived, calculation_method를 근거로 직접 공시값과 파생값을 명확히 구분한다.\n"
            "- normalized_value로 제공된 양수 지출 규모를 사용하고 원본 부호를 임의 변경하지 않는다.\n"
            "- 분기 질문에는 value_type=quarterly 값만 답하고 누적값을 분기값으로 오인하지 않는다.\n"
        )

    quarterly_rules = ""
    if analysis_period == "quarterly_trend":
        quarterly_rules = (
            "\n[분기별 추이 규칙]\n"
            "- Q1·Q2·Q3·Q4의 value_type=quarterly 값만 서로 비교한다.\n"
            "- Q2는 H1 보고서, Q4는 FY 보고서에서 가져온 분기 단독값이다.\n"
            "- is_calculated=true인 Q4 등은 누적값 차감으로 계산된 값이라고 명시한다.\n"
        )

    rnd_rules = ""
    if "연구개발" in query or "R&D" in query.upper():
        rnd_rules = (
            "\n[헬스케어 연구개발비 규칙]\n"
            "- RND_EXPENSE는 사업보고서 연구개발활동 표의 총액이며 연결 기준을 우선한다.\n"
            "- RND_TO_REVENUE의 단위 BPS는 100 BPS = 1%이다.\n"
            "- scope, fs_div, source_values에서 총액·비용처리·자산화·보조금 포함 범위를 확인해 함께 밝힌다.\n"
            "- 표준 지표가 없고 공시 검색 결과가 있으면 기업이 공시하지 않았다고 단정하지 말고 "
            "현재 표준 KPI에 미적재됐다고 표현한다.\n"
        )

    prompt = f"""[사용자 질문]\n{query}\n\n[재무 데이터]\n{_format_financial(financial_rows)}\n\n[공시 근거]\n{_format_filings(filing_rows)}\n{facility_rules}{capex_rules}{quarterly_rules}{rnd_rules}\n[작성 규칙]\n- 제공된 공개 DART 근거만 사용해 한국어로 답한다.\n- 재무숫자를 임의 생성하지 말고 Financial DB 숫자만 사용한다.\n- normalized_value와 display_* 값은 Python 코드가 확정한 값이다. 제공된 값을 그대로 사용한다.\n- 이미 환산된 숫자를 다시 계산하지 말고 단위 변환을 임의로 수행하지 않는다.\n- 원본값과 환산값이 함께 있으면 normalized_value를 우선한다.\n- 금액을 답변에 쓸 때는 제공된 display_krw 또는 display_trillion 문자열을 그대로 복사한다.\n- 서로 다른 기업·연도·rcept_no·CFS/OFS를 섞지 않는다.\n- 계산값은 DART 직접 제공값과 구분한다.\n- 원인·전략 연결은 공시 원문이 직접 뒷받침하는 범위로만 설명한다.\n- 원문에 없는 약어 해석, 세부 투자 대상, 기술·시장 효과, ROI 또는 새 인과관계를 추가하지 않는다.\n- 추정이라고 표시하더라도 공시 근거에 없는 새 사실을 추가하지 않는다.\n- 원인·전략 문단에는 근거 rcept_no를 함께 표시한다.\n- 답변에서 사용한 근거의 rcept_no를 문장 또는 마지막 근거 목록에 표시한다.\n- 핵심 지표와 직접 근거만 사용해 1,200자 이내로 간결하게 답한다.\n- 근거가 부족하면 부족하다고 답한다.\n"""
    raw_answer = llm_client(prompt) if llm_client else _grounded_preview(financial_rows, filing_rows)
    answer, removed_expansions = _remove_unsupported_acronym_expansions(raw_answer, filing_rows)
    financial_sources = [
        {key: row.get(key) for key in (
            "corp_name", "corp_code", "year", "report_type", "rcept_no",
            "standard_account_nm", "value_type", "fs_div", "is_calculated",
            "value_source", "is_derived", "source_account_names", "scope", "review_status",
            "quarter", "quarter_label",
        )}
        for row in financial_rows
    ]
    filing_sources = [
        {key: row.get(key) for key in (
            "corp_name", "corp_code", "year", "report_type", "rcept_no", "section_name",
            "subsection_name", "source_file", "chunk_id", "retrieval_method", "dense_score",
            "lexical_score", "search_priority", "search_priority_applied",
            "priority_multiplier", "final_score", "score", "fallback_reason",
        )}
        for row in filing_rows
    ]
    return {
        "query": query, "corp_name": corp_name, "years": years, "report_type": report_type,
        "analysis_period": analysis_period,
        "route": route, "financial_db_used": use_financial, "vector_db_used": use_vector,
        "financial_fallback_reason": financial_fallback_reason,
        "retrieval_method": retrieval.effective_retriever if retrieval else retrieval_method,
        "requested_retrieval_method": retrieval.requested_retriever if retrieval else retrieval_method,
        "effective_retrieval_method": retrieval.effective_retriever if retrieval else None,
        "retrieval_fallback_reason": retrieval.fallback_reason if retrieval else None,
        "answer": answer,
        "raw_answer": raw_answer,
        "postprocessing": {"unsupported_acronym_expansions_removed": removed_expansions},
        "financial_evidence": financial_rows,
        "filing_evidence": filing_rows,
        "financial_sources": financial_sources, "filing_sources": filing_sources,
        "prompt": prompt, "llm_called": llm_client is not None,
    }
