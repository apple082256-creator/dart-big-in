from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.retrieval import operational_retriever as router
from src.retrieval.bge_m3_indexer import validate_bge_operational_assets
from src.retrieval.operational_config import (
    BGE_M3_DENSE,
    DEFAULT_FILING_RETRIEVER,
    EXPECTED_BGE_COMPANIES,
    FEATURE_HASHING_HYBRID,
    FilingRetrieverSettings,
    get_filing_retriever_settings,
)


class OperationalRetrieverConfigTests(unittest.TestCase):
    def test_default_is_bge_m3_dense_and_fallback_is_off(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = get_filing_retriever_settings()
        self.assertEqual(DEFAULT_FILING_RETRIEVER, BGE_M3_DENSE)
        self.assertEqual(settings.retriever, BGE_M3_DENSE)
        self.assertFalse(settings.auto_fallback)

    def test_environment_can_explicitly_roll_back(self) -> None:
        with patch.dict(os.environ, {"FILING_RETRIEVER": FEATURE_HASHING_HYBRID}, clear=True):
            settings = get_filing_retriever_settings()
        self.assertEqual(settings.retriever, FEATURE_HASHING_HYBRID)

    def test_invalid_environment_value_fails_closed(self) -> None:
        with patch.dict(os.environ, {"FILING_RETRIEVER": "unknown"}, clear=True):
            with self.assertRaisesRegex(ValueError, "지원하지 않는 FILING_RETRIEVER"):
                get_filing_retriever_settings()

    def test_invalid_fallback_boolean_fails_closed(self) -> None:
        with patch.dict(os.environ, {"FILING_RETRIEVER_AUTO_FALLBACK": "sometimes"}, clear=True):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                get_filing_retriever_settings()


class OperationalRetrieverRoutingTests(unittest.TestCase):
    def test_bge_dense_is_routed_without_lexical_or_priority(self) -> None:
        row = {"chunk_id": "c1", "retrieval_method": BGE_M3_DENSE}
        with patch.object(router, "search_bge_m3", return_value=[row]) as search:
            result = router.retrieve_filing_context(
                "질문",
                corp_name="삼성전자",
                settings=FilingRetrieverSettings(BGE_M3_DENSE, False),
            )
        self.assertEqual(result.rows, [row])
        self.assertEqual(result.effective_retriever, BGE_M3_DENSE)
        self.assertIsNone(result.fallback_reason)
        self.assertFalse(search.call_args.kwargs["hybrid"])
        self.assertFalse(search.call_args.kwargs["apply_priority"])

    def test_multiple_company_codes_are_forwarded_to_retrievers(self) -> None:
        with patch.object(router, "search_bge_m3", return_value=[]) as search:
            router.retrieve_filing_context(
                "질문",
                corp_codes=["00126380", "00164779"],
                settings=FilingRetrieverSettings(BGE_M3_DENSE, False),
            )
        self.assertEqual(search.call_args.kwargs["corp_codes"], ["00126380", "00164779"])

    def test_index_scope_is_resolved_before_retrieval(self) -> None:
        companies = [{"corp_code": "00126380"}, {"corp_code": "00164779"}]
        expected = router.FilingRetrievalResult([], BGE_M3_DENSE, BGE_M3_DENSE)
        with (
            patch.object(router, "get_companies_by_index", return_value=companies) as resolve,
            patch.object(router, "retrieve_filing_context", return_value=expected) as retrieve,
        ):
            actual = router.retrieve_index_filing_context("질문", "KRX_SEMI", year=2025)
        self.assertIs(actual, expected)
        resolve.assert_called_once_with("KRX_SEMI", as_of=None)
        self.assertEqual(retrieve.call_args.kwargs["corp_codes"], ["00126380", "00164779"])

    def test_bge_failure_is_not_hidden_by_default(self) -> None:
        with patch.object(router, "search_bge_m3", side_effect=RuntimeError("broken")):
            with self.assertRaisesRegex(RuntimeError, "broken"):
                router.retrieve_filing_context(
                    "질문", settings=FilingRetrieverSettings(BGE_M3_DENSE, False)
                )

    def test_explicit_fallback_reports_effective_method_and_reason(self) -> None:
        legacy = [{"chunk_id": "legacy"}]
        with (
            patch.object(router, "search_bge_m3", side_effect=RuntimeError("broken")),
            patch.object(router, "search_filing_context", return_value=legacy),
        ):
            result = router.retrieve_filing_context(
                "질문", settings=FilingRetrieverSettings(BGE_M3_DENSE, True)
            )
        self.assertEqual(result.requested_retriever, BGE_M3_DENSE)
        self.assertEqual(result.effective_retriever, FEATURE_HASHING_HYBRID)
        self.assertIn("RuntimeError: broken", result.fallback_reason or "")
        self.assertIn("RuntimeError: broken", result.rows[0]["fallback_reason"])

    def test_legacy_rollback_routes_directly(self) -> None:
        with patch.object(router, "search_filing_context", return_value=[]) as search:
            result = router.retrieve_filing_context(
                "질문", settings=FilingRetrieverSettings(FEATURE_HASHING_HYBRID, False)
            )
        search.assert_called_once()
        self.assertEqual(result.effective_retriever, FEATURE_HASHING_HYBRID)


class OperationalAssetTests(unittest.TestCase):
    @unittest.skipUnless(
        Path("db/vector/filings_bge_m3.db").exists()
        and Path("db/vector/filings_bge_m3.faiss").exists(),
        "BGE-M3 운영 자산이 없습니다.",
    )
    def test_real_assets_match_promoted_contract(self) -> None:
        report = validate_bge_operational_assets()
        self.assertEqual(report["status"], "PASS")
        self.assertGreater(report["searchable_vectors"], 0)
        self.assertEqual(tuple(report["companies"]), EXPECTED_BGE_COMPANIES)


if __name__ == "__main__":
    unittest.main()
