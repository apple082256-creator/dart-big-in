"""Central definition of industry KPIs for non-financial companies.

Industry profiles, metric definitions, official-tag mappings and the KPI set of
each profile live here so that adding an industry or a KPI is a data change in
one place.  Bank metrics stay defined in ``financial_metrics.METRIC_DEFINITIONS``.
"""
from __future__ import annotations

from dataclasses import dataclass

PROFILE_BY_INDEX = {
    "KRX_SEMI": "SEMICONDUCTOR",
    "KRX_HEALTH": "HEALTHCARE",
    "KRX_STEEL": "STEEL",
    "KRX_AUTO": "AUTOMOTIVE",
}
GENERAL_PROFILES = tuple(PROFILE_BY_INDEX.values())

# Metrics shared by banks and general companies keep one definition.
SHARED_METRICS = ("OPERATING_INCOME", "NET_INCOME", "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY")

RATIO_UNIT = "BPS"  # basis points: 1 = 0.01 %, so 1234 = 12.34 %

# (metric_code, name, industry_profile, statement, metric_type, period_nature, unit, description, is_core)
GENERAL_DEFINITIONS = (
    ("REVENUE", "매출액", "GENERAL", "CIS", "DIRECT", "flow", "KRW", "ifrs-full_Revenue", 1),
    ("OPERATING_CASH_FLOW", "영업활동현금흐름", "GENERAL", "CF", "DIRECT", "flow", "KRW",
     "공시는 누적(분기 3개월값은 누적 차이로 계산)", 1),
    ("CAPEX", "설비투자(CAPEX)", "GENERAL", "CF", "DIRECT", "flow", "KRW",
     "capex_facts 현금유출액(양수). 직접 계정 또는 승인된 유형자산 취득 구성요소 합산", 1),
    ("INVENTORIES", "재고자산", "GENERAL", "BS", "DIRECT", "point_in_time", "KRW", None, 1),
    ("INVENTORY_CHANGE", "재고자산 증감", "GENERAL", None, "DERIVED", "flow", "KRW",
     "직전 보고 시점 대비 재고자산 증감", 1),
    ("REVENUE_GROWTH_YOY", "매출 증가율(전년 동기 대비)", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "같은 값 구분(3개월·누적·연간)의 전년 동기 대비, bp", 1),
    ("OPERATING_MARGIN", "영업이익률", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "영업이익 / 매출액, 같은 기간·값 구분, bp", 1),
    ("DEBT_RATIO", "부채비율", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "부채총계 / 자본총계, 같은 시점, bp", 1),
    ("CAPEX_GROWTH_YOY", "CAPEX 증가율(전년 동기 대비)", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "같은 값 구분의 전년 동기 대비, bp", 1),
    ("CAPEX_TO_OCF", "영업현금흐름 대비 CAPEX", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "CAPEX / 영업활동현금흐름, 같은 기간·값 구분, 영업현금흐름 > 0, bp", 1),
    ("CAPEX_TO_REVENUE", "매출액 대비 CAPEX", "GENERAL", None, "RATIO", "ratio", RATIO_UNIT,
     "CAPEX / 매출액, 같은 기간·값 구분, bp", 1),
    ("RND_EXPENSE", "연구개발비", "HEALTHCARE", "NOTE", "DIRECT", "flow", "KRW",
     "사업보고서 연구개발활동 표의 연결 연구개발비용 총액(연결 부재 시 별도)", 1),
    ("RND_TO_REVENUE", "연구개발비/매출액 비율", "HEALTHCARE", None, "RATIO", "ratio", RATIO_UNIT,
     "사업보고서 공시 비율 우선; 미공시 시 동일 범위 연구개발비/매출액, bp", 1),
)

# (metric_code, account_id, sj_divs, priority, profiles)
GENERAL_MAPPINGS = (
    ("REVENUE", "ifrs-full_Revenue", ("IS", "CIS"), 10, GENERAL_PROFILES),
    ("OPERATING_INCOME", "dart_OperatingIncomeLoss", ("IS", "CIS"), 10, GENERAL_PROFILES),
    ("OPERATING_INCOME", "ifrs-full_ProfitLossFromOperatingActivities", ("IS", "CIS"), 20, GENERAL_PROFILES),
    ("NET_INCOME", "ifrs-full_ProfitLoss", ("IS", "CIS"), 10, GENERAL_PROFILES),
    ("OPERATING_CASH_FLOW", "ifrs-full_CashFlowsFromUsedInOperatingActivities", ("CF",), 10, GENERAL_PROFILES),
    ("TOTAL_ASSETS", "ifrs-full_Assets", ("BS",), 10, GENERAL_PROFILES),
    ("TOTAL_LIABILITIES", "ifrs-full_Liabilities", ("BS",), 10, GENERAL_PROFILES),
    ("TOTAL_EQUITY", "ifrs-full_Equity", ("BS",), 10, GENERAL_PROFILES),
    ("INVENTORIES", "ifrs-full_Inventories", ("BS",), 10, ("SEMICONDUCTOR", "AUTOMOTIVE")),
    ("INVENTORIES", "ifrs-full_InventoriesTotal", ("BS",), 20, ("SEMICONDUCTOR", "AUTOMOTIVE")),
)


@dataclass(frozen=True)
class DerivedKpi:
    metric_code: str
    kind: str                 # ratio / growth / change
    numerator: str
    denominator: str | None   # None for growth/change (compares the metric with itself)
    profiles: tuple[str, ...]
    positive_denominator: bool = True


COMMON_DERIVED = (
    DerivedKpi("OPERATING_MARGIN", "ratio", "OPERATING_INCOME", "REVENUE", GENERAL_PROFILES),
    DerivedKpi("DEBT_RATIO", "ratio", "TOTAL_LIABILITIES", "TOTAL_EQUITY", GENERAL_PROFILES),
    DerivedKpi("REVENUE_GROWTH_YOY", "growth", "REVENUE", None, GENERAL_PROFILES),
)
EXTENDED_DERIVED = (
    DerivedKpi("CAPEX_GROWTH_YOY", "growth", "CAPEX", None, ("SEMICONDUCTOR",)),
    DerivedKpi("CAPEX_TO_OCF", "ratio", "CAPEX", "OPERATING_CASH_FLOW", ("SEMICONDUCTOR",)),
    DerivedKpi("CAPEX_TO_REVENUE", "ratio", "CAPEX", "REVENUE", ("STEEL",)),
    DerivedKpi("INVENTORY_CHANGE", "change", "INVENTORIES", None, ("SEMICONDUCTOR", "AUTOMOTIVE")),
)
DERIVED_KPIS = COMMON_DERIVED + EXTENDED_DERIVED

# Point-in-time predecessor of each report end, for balance changes.
PREVIOUS_POINT = {"Q1": (-1, "FY"), "H1": (0, "Q1"), "Q3": (0, "H1"), "FY": (0, "Q3")}

# KPIs each profile exposes (for coverage reporting and the KPI query API).
KPI_SET = {
    profile: (
        ("REVENUE", "OPERATING_INCOME", "NET_INCOME", "OPERATING_CASH_FLOW", "CAPEX",
         "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY",
         "REVENUE_GROWTH_YOY", "OPERATING_MARGIN", "DEBT_RATIO")
        + {"SEMICONDUCTOR": ("CAPEX_GROWTH_YOY", "CAPEX_TO_OCF", "INVENTORIES", "INVENTORY_CHANGE"),
           "HEALTHCARE": ("RND_EXPENSE", "RND_TO_REVENUE"),
           "STEEL": ("CAPEX_TO_REVENUE",),
           "AUTOMOTIVE": ("INVENTORIES", "INVENTORY_CHANGE")}[profile]
    )
    for profile in GENERAL_PROFILES
}
