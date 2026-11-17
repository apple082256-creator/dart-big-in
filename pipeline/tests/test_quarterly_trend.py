from __future__ import annotations

import unittest

from src.llm.analyzer import _quarterly_financial_context, _years, analyze_company


class QuarterlyTrendTest(unittest.TestCase):
    def test_expands_full_year_range(self) -> None:
        self.assertEqual(_years("2023년부터 2025년까지 매출 변화", 2025), [2023, 2024, 2025])
        self.assertEqual(_years("23년도부터 25년도까지 매출 변화", 2025), [2023, 2024, 2025])

    def test_single_change_year_adds_previous_year(self) -> None:
        self.assertEqual(_years("2025년 매출 변화", 2025), [2024, 2025])

    def test_collects_four_standalone_revenue_quarters(self) -> None:
        rows = _quarterly_financial_context("분기별 매출 추이", "삼성전자", [2025])
        revenue = [row for row in rows if row["standard_account_nm"] == "매출액"]
        self.assertEqual([row["quarter"] for row in revenue], [1, 2, 3, 4])
        self.assertEqual([row["report_type"] for row in revenue], ["Q1", "H1", "Q3", "FY"])
        self.assertEqual([row["value_type"] for row in revenue], ["quarterly"] * 4)
        self.assertTrue(revenue[-1]["is_calculated"])

    def test_analyzer_uses_quarterly_mode_without_llm(self) -> None:
        result = analyze_company(
            "2023년부터 2025년까지 분기별 매출 추이",
            corp_name="삼성전자",
            year=2025,
            analysis_period="quarterly_trend",
        )
        revenue = [
            row for row in result["financial_evidence"]
            if row["standard_account_nm"] == "매출액"
        ]
        self.assertEqual(result["years"], [2023, 2024, 2025])
        self.assertEqual(result["analysis_period"], "quarterly_trend")
        self.assertEqual(len(revenue), 12)
        self.assertFalse(result["llm_called"])


if __name__ == "__main__":
    unittest.main()
