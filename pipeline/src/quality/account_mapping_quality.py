from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from src.config import MAPPING_DIR, PROCESSED_DIR
from src.preprocess.financial_cleaner import normalize_account_name


@dataclass(frozen=True)
class CoreAccount:
    standard_name: str
    sj_divs: tuple[str, ...]
    account_ids: tuple[str, ...]
    aliases: tuple[str, ...]


CORE_ACCOUNTS = (
    CoreAccount("매출액", ("IS", "CIS"), ("ifrs-full_Revenue",), ("매출액", "수익매출액", "영업수익")),
    CoreAccount("매출원가", ("IS", "CIS"), ("ifrs-full_CostOfSales",), ("매출원가",)),
    CoreAccount("매출총이익", ("IS", "CIS"), ("ifrs-full_GrossProfit",), ("매출총이익", "매출총이익손실")),
    CoreAccount("영업이익", ("IS", "CIS"), ("dart_OperatingIncomeLoss",), ("영업이익", "영업손익", "영업순손익")),
    CoreAccount("당기순이익", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), ("당기순이익", "당기순손익", "당기순이익손실", "분기순이익", "반기순이익")),
    CoreAccount("법인세비용차감전순이익", ("IS", "CIS"), ("ifrs-full_ProfitLossBeforeTax",), ("법인세비용차감전순이익", "법인세비용차감전순손익")),
    CoreAccount("자산총계", ("BS",), ("ifrs-full_Assets",), ("자산총계",)),
    CoreAccount("부채총계", ("BS",), ("ifrs-full_Liabilities",), ("부채총계",)),
    CoreAccount("자본총계", ("BS",), ("ifrs-full_Equity",), ("자본총계",)),
    CoreAccount("유동자산", ("BS",), ("ifrs-full_CurrentAssets",), ("유동자산",)),
    CoreAccount("유동부채", ("BS",), ("ifrs-full_CurrentLiabilities",), ("유동부채",)),
    CoreAccount("이익잉여금", ("BS",), ("ifrs-full_RetainedEarnings",), ("이익잉여금", "이익잉여금결손금")),
    CoreAccount("현금및현금성자산", ("BS",), ("ifrs-full_CashAndCashEquivalents",), ("현금및현금성자산",)),
    CoreAccount("유형자산", ("BS",), ("ifrs-full_PropertyPlantAndEquipment",), ("유형자산",)),
    CoreAccount("무형자산", ("BS",), ("ifrs-full_IntangibleAssetsOtherThanGoodwill", "ifrs-full_IntangibleAssetsAndGoodwill"), ("무형자산", "영업권이외의무형자산")),
    CoreAccount("재고자산", ("BS",), ("ifrs-full_Inventories", "ifrs-full_InventoriesTotal"), ("재고자산", "유동재고자산")),
    CoreAccount("매출채권", ("BS",), ("dart_ShortTermTradeReceivable", "ifrs-full_CurrentTradeReceivables"), ("매출채권", "유동매출채권")),
    CoreAccount("영업활동현금흐름", ("CF",), ("ifrs-full_CashFlowsFromUsedInOperatingActivities",), ("영업활동현금흐름", "영업활동으로인한현금흐름")),
    CoreAccount("투자활동현금흐름", ("CF",), ("ifrs-full_CashFlowsFromUsedInInvestingActivities",), ("투자활동현금흐름", "투자활동으로인한현금흐름")),
    CoreAccount("재무활동현금흐름", ("CF",), ("ifrs-full_CashFlowsFromUsedInFinancingActivities",), ("재무활동현금흐름", "재무활동으로인한현금흐름")),
    CoreAccount("유형자산취득(CAPEX)", ("CF",), ("ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",), ("유형자산의취득", "유형자산취득")),
    CoreAccount("무형자산취득", ("CF",), ("ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",), ("무형자산의취득", "무형자산취득")),
    CoreAccount("감가상각비", ("CF",), ("ifrs-full_AdjustmentsForDepreciationExpense", "dart_AdjustmentsForDepreciationExpense"), ("감가상각비",)),
    CoreAccount("연구개발비", ("IS", "CIS"), (), ("연구개발비", "경상연구개발비")),
)


def prioritize_mapping_review() -> tuple[pd.DataFrame, pd.DataFrame]:
    draft = pd.read_csv(MAPPING_DIR / "account_mapping_draft.csv").fillna("")
    review = pd.read_csv(MAPPING_DIR / "account_mapping_review.csv").fillna("")
    statement_count = draft.groupby("standard_account_name")["sj_div"].nunique().to_dict()
    issue_by_id = set(review.loc[review["issue_type"].eq("same_name_multiple_ids"), "account_id"])

    priorities: list[str] = []
    candidates: list[bool] = []
    notes: list[str] = []
    for row in draft.itertuples():
        high = statement_count.get(row.standard_account_name, 1) > 1 or " | " in str(row.fs_divs)
        valid_id = bool(row.account_id)
        if high:
            priority, candidate, note = "high", False, "statement/fs_div 의미 혼동 가능"
        elif valid_id and int(row.company_count) >= 2 and row.account_id not in issue_by_id:
            priority, candidate, note = "low", True, "동일 account_id·sj_div·fs_div가 여러 기업에서 반복"
        else:
            priority, candidate, note = "medium", False, "확장 태그·단일기업·다중 ID 가능성 검토"
        priorities.append(priority); candidates.append(candidate); notes.append(note)
    draft["review_priority"] = priorities
    draft["auto_mapping_candidate"] = candidates
    draft["priority_note"] = notes
    draft.to_csv(MAPPING_DIR / "account_mapping_prioritized.csv", index=False, encoding="utf-8-sig")

    review_priority: list[str] = []
    for row in review.itertuples():
        name = str(row.account_nm).split(" | ")[0]
        cross_statement = draft.loc[draft["source_account_names"].str.contains(re.escape(name), regex=True), "sj_div"].nunique() > 1
        review_priority.append("high" if cross_statement else ("medium" if row.issue_type == "same_name_multiple_ids" else "low"))
    review["review_priority"] = review_priority
    review["auto_mapping_candidate"] = review["review_priority"].eq("low")
    review.to_csv(MAPPING_DIR / "account_mapping_review_prioritized.csv", index=False, encoding="utf-8-sig")
    return draft, review


def build_core_mapping() -> pd.DataFrame:
    frame = pd.read_csv(PROCESSED_DIR / "financial" / "financial_accounts_wide.csv", low_memory=False).fillna("")
    records: list[dict] = []
    for spec in CORE_ACCOUNTS:
        for row in frame.itertuples():
            if row.sj_div not in spec.sj_divs:
                continue
            normalized_name = normalize_account_name(row.account_nm)
            id_match = row.account_id in spec.account_ids
            name_match = normalized_name in spec.aliases
            if not (id_match or name_match):
                continue
            confidence = "high" if id_match else "medium"
            records.append({
                "standard_account_nm": spec.standard_name,
                "original_account_nm": row.account_nm,
                "account_id": row.account_id,
                "sj_div": row.sj_div,
                "fs_div": row.fs_div,
                "report_type": row.quarter,
                "corp_name": row.company_name,
                "mapping_status": "approved" if id_match else "review_required",
                "mapping_confidence": confidence,
                "review_required": not id_match,
                "review_note": "공인 XBRL/DART ID와 제표구분 일치" if id_match else "계정명 기반 후보—확장 태그 여부 확인",
            })
    raw_core = pd.DataFrame(records).drop_duplicates()
    group_columns = [
        "standard_account_nm", "original_account_nm", "account_id", "sj_div", "fs_div",
        "mapping_status", "mapping_confidence", "review_required", "review_note",
    ]
    core = (
        raw_core.groupby(group_columns, dropna=False)
        .agg(
            corp_names=("corp_name", lambda values: " | ".join(sorted(set(values)))),
            report_types=("report_type", lambda values: " | ".join(sorted(set(values)))),
        )
        .reset_index()
        .sort_values(["standard_account_nm", "account_id", "original_account_nm"])
    )
    core.to_csv(MAPPING_DIR / "account_mapping_core.csv", index=False, encoding="utf-8-sig")
    return core


if __name__ == "__main__":
    prioritized, review = prioritize_mapping_review()
    core = build_core_mapping()
    print(
        f"우선순위 분류 {len(prioritized)}건, 충돌 {len(review)}건, "
        f"핵심 표준계정 {core['standard_account_nm'].nunique()}개"
    )
