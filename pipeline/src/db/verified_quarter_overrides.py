"""Quarter derivations that the automatic rules hold back but that were verified.

Each entry pins the exact FY and Q3 cumulative inputs.  The evidence is the
next-year (or same-year) reports restating both periods under one convention;
an entry only applies while the stored inputs still equal the pinned values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

EVIDENCE_MANIFEST_PATH = Path(__file__).resolve().parent / "verified_quarter_manifest.json"
SIGN_ALIGNMENT_MANIFEST_PATH = Path(__file__).resolve().parent / "verified_sign_alignment_manifest.json"


@dataclass(frozen=True)
class VerifiedFactQuarter:
    """Same account id in both reports; blocked by a name change or sign conflict."""
    corp_code: str
    year: int
    fs_div: str
    sj_div: str
    account_key: str
    fy_value: int
    q3_value: int
    align_sign: bool
    reason: str
    evidence: str


@dataclass(frozen=True)
class VerifiedMetricQuarter:
    """Same statement line tagged with different account ids in FY and Q3."""
    corp_code: str
    year: int
    metric_code: str
    fy_account_id: str
    fy_value: int
    q3_account_id: str
    q3_value: int
    align_sign: bool
    reason: str
    evidence: str


CREDIT_LOSS_DART = "dart_AdditionReversalOfCreditLossFinancialAssets"
CREDIT_LOSS_IFRS = "ifrs-full_AdditionalAllowanceRecognisedInProfitOrLossAllowanceAccountForCreditLossesOfFinancialAssets"

FACT_QUARTERS = (
    VerifiedFactQuarter("00382199", 2023, "CFS", "CIS", "ifrs-full_ProfitLossFromOperatingActivities",
                        6100850000000, 5196366000000, False,
                        "name '영업이익' vs '영업이익 (손실)'",
                        "2024 FY 20250318000993 / 2024 Q3 20241114001711 comparatives equal both inputs"),
    VerifiedFactQuarter("01350869", 2025, "CFS", "CIS",
                        "ifrs-full_ImpairmentLossImpairmentGainAndReversalOfImpairmentLossDeterminedInAccordanceWithIFRS9",
                        2102814000000, 1518969000000, False,
                        "name '신용손실에대한손상차손' vs '…손상차손환입(전입)'",
                        "2025 cumulative series consistent across relabelled reports: H1 943,310 = Q1 435,501 + Q2 507,809"),
    VerifiedFactQuarter("01350869", 2025, "CFS", "CIS", "ifrs-full_ProfitLoss",
                        3227508000000, 2885803000000, False,
                        "name '당기순이익' vs '분기순손익'",
                        "same tag and sign; 2025 cumulative series Q1→H1→Q3 consistent"),
    VerifiedFactQuarter("00382199", 2024, "CFS", "CIS", "ifrs-full_InterestExpense",
                        17807036000000, -13345401000000, True,
                        "sign convention differs between FY and Q3 reports",
                        "2025 FY 20261007000218 / 2025 Q3 20251114002208 comparatives: 17,807,036 and 13,345,401"),
    VerifiedFactQuarter("01133217", 2023, "OFS", "CIS", "ifrs-full_FeeAndCommissionExpense",
                        -269827000000, 200210000000, True,
                        "sign convention differs between FY and Q3 reports",
                        "2024 FY 20250411001905 / 2024 Q3 20241114001656 comparatives: 269,827 and 200,210"),
    VerifiedFactQuarter("01133217", 2023, "OFS", "CIS", "ifrs-full_InterestExpense",
                        -932200000000, 677346000000, True,
                        "sign convention differs between FY and Q3 reports",
                        "2024 FY 20250411001905 / 2024 Q3 20241114001656 comparatives: 932,200 and 677,346"),
)

METRIC_QUARTERS = (
    VerifiedMetricQuarter("00382199", 2024, "CREDIT_LOSS_EXPENSE", CREDIT_LOSS_DART, 2013274000000,
                          CREDIT_LOSS_IFRS, -1399739000000, True,
                          "same line '신용손실충당금 전입액' tagged differently; Q3 sign reversed",
                          "2025 FY 20261007000218 / 2025 Q3 20251114002208 restate both under one tag: "
                          "2,013,274 and 1,399,739"),
    VerifiedMetricQuarter("00688996", 2024, "CREDIT_LOSS_EXPENSE", CREDIT_LOSS_DART, 2044286000000,
                          CREDIT_LOSS_IFRS, 1479143000000, False,
                          "same line '신용손실충당금 전입액' tagged differently",
                          "2025 FY / 2025 Q3 comparatives under one tag: 2,044,286 and 1,479,143"),
    VerifiedMetricQuarter("01350869", 2024, "CREDIT_LOSS_EXPENSE", CREDIT_LOSS_DART, 1716295000000,
                          CREDIT_LOSS_IFRS, -1253770000000, True,
                          "same line '신용손실에대한손상차손' tagged differently; Q3 sign reversed",
                          "2025 FY 20260313001235 / 2025 Q3 20251114002781 restate both under one tag: "
                          "1,716,295 and 1,253,770"),
    VerifiedMetricQuarter("01133217", 2023, "OPERATING_INCOME", "dart_OperatingIncomeLoss", 478485000000,
                          "ifrs-full_ProfitLossFromOperatingActivities", 375692000000, False,
                          "same line 'IV. 영업이익' tagged differently",
                          "2024 FY 20250411001905 / 2024 Q3 20241114001656 comparatives under one tag: "
                          "478,485 and 375,692"),
)

@dataclass(frozen=True)
class RestatedMetricQuarter:
    """Q4 whose stored FY and Q3 cumulative were reported on different bases.

    The Q3 cumulative restated to the FY basis comes from a later report that
    presents it next to the same FY value; the result stays review_required
    because the stored Q1-Q3 quarters remain on the earlier basis.
    """
    corp_code: str
    year: int
    metric_code: str
    fy_value: int            # stored FY annual
    stored_q3_value: int     # stored Q3 cumulative (earlier basis)
    restated_q3_value: int   # Q3 cumulative restated to the FY basis
    fy_rcept_no: str
    restated_rcept_no: str
    evidence: str


KB_VFA = ("2024 Q3 report 20241114002984 '재무제표 재작성' note: variable fee approach first applied at "
          "2023 year end, 2023 Q3 restated; its summary income statement shows 제16기 3분기 and 제16기 side by side")

# KB Financial 2023: the 2023 annual report applies the variable fee approach
# adopted at year end; the original Q3 report does not.
RESTATED_METRIC_QUARTERS = (
    RestatedMetricQuarter("00688996", 2023, "OWNERS_NET_INCOME", 4631932000000, 4370442000000, 4376514000000,
                          "20240326000894", "20241114002984", KB_VFA),
    RestatedMetricQuarter("00688996", 2023, "PRETAX_INCOME", 6170449000000, 5899212000000, 5907413000000,
                          "20240326000894", "20241114002984", KB_VFA),
    RestatedMetricQuarter("00688996", 2023, "OPERATING_INCOME", 6435319000000, 6131146000000, 6139347000000,
                          "20240326000894", "20241114002984", KB_VFA),
)

FACT_QUARTER_INDEX = {
    (item.corp_code, item.year, "FY", item.fs_div, item.sj_div, item.account_key,
     item.fy_value, item.q3_value): item
    for item in FACT_QUARTERS
}


@lru_cache(maxsize=1)
def evidence_quarter_index() -> dict[tuple, dict]:
    """Held-back quarters verified against the filed statements (residual review v10).

    Keyed by the exact cumulative inputs, so a re-filed or different value is
    not derived and goes back to review.
    """
    if not EVIDENCE_MANIFEST_PATH.is_file():
        return {}
    entries = json.loads(EVIDENCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {(e["corp_code"], e["year"], e["report_type"], e["fs_div"], e["sj_div"], e["account_key"],
             e["current_value"], e["previous_value"]): e for e in entries}


def sign_alignment_entries() -> list[dict]:
    """Presentation flips of a new period verified against the filed statements.

    Same role as the code lists in ``sign_convention_corrections`` and
    ``residual_corrections``: only these exact cumulative inputs are aligned.
    """
    if not SIGN_ALIGNMENT_MANIFEST_PATH.is_file():
        return []
    return json.loads(SIGN_ALIGNMENT_MANIFEST_PATH.read_text(encoding="utf-8"))
