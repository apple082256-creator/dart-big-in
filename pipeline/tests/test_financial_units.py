from __future__ import annotations

import unittest

from src.financial_units import (
    extract_monetary_facts,
    detect_scale_factor,
    format_eok,
    format_krw,
    format_korean_large,
    format_trillion,
    normalize_financial_value,
)


class FinancialUnitTest(unittest.TestCase):
    def test_required_unit_conversions(self) -> None:
        cases = (
            ("1", "억원", 100_000_000),
            ("10", "억원", 1_000_000_000),
            ("1,000", "억원", 100_000_000_000),
            ("474,764", "억원", 47_476_400_000_000),
            ("1", "조원", 1_000_000_000_000),
            ("2.5", "조원", 2_500_000_000_000),
            ("1", "백만원", 1_000_000),
            ("1", "천원", 1_000),
        )
        for value, unit, expected in cases:
            with self.subTest(value=value, unit=unit):
                self.assertEqual(normalize_financial_value(value, unit), expected)

    def test_display_formats(self) -> None:
        value = 47_476_400_000_000
        self.assertEqual(format_krw(value), "47,476,400,000,000원")
        self.assertEqual(format_eok(value), "474,764억원")
        self.assertEqual(format_trillion(value), "47.48조원")
        self.assertEqual(format_korean_large(value), "47조 4,764억원")

    def test_facility_investment_table_extraction(self) -> None:
        text = """(시설투자 현황)
(단위 : 억원)
DS 부문
신ㆍ증설 및 보완
2025.01~2025.12
건물ㆍ설비 등
474,764
SDC
신ㆍ증설 및 보완
27,970
기 타
21,225
합 계
526,511
"""
        facts = {fact["label"]: fact for fact in extract_monetary_facts(text)}
        self.assertEqual(facts["DS 부문"]["normalized_value"], 47_476_400_000_000)
        self.assertEqual(facts["SDC"]["normalized_value"], 2_797_000_000_000)
        self.assertEqual(facts["기 타"]["normalized_value"], 2_122_500_000_000)
        self.assertEqual(facts["합 계"]["normalized_value"], 52_651_100_000_000)
        self.assertEqual(facts["합 계"]["conversion_formula"], "value × 100,000,000")

    def test_unsupported_unit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_financial_value("1", "달러")

    def test_common_scale_errors_are_detected(self) -> None:
        expected = 100_000_000_000
        for factor in (10, 100, 1_000, 100_000_000):
            with self.subTest(factor=factor):
                self.assertEqual(detect_scale_factor(expected // factor, expected), factor)
        self.assertIsNone(detect_scale_factor(expected, expected))


if __name__ == "__main__":
    unittest.main()
