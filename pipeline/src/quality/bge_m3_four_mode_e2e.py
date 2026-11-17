from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from src.config import PROJECT_ROOT
from src.retrieval.bge_m3_indexer import BGE_DB_PATH, BGE_FAISS_PATH, search_bge_m3


QUALITY_DIR = PROJECT_ROOT / "data" / "processed" / "quality"
BASELINE_OUTPUTS = QUALITY_DIR / "embedding_model_e2e_outputs.json"
BASELINE_COMPARISON = QUALITY_DIR / "embedding_model_e2e_comparison.csv"
BASELINE_RETRIEVAL = QUALITY_DIR / "embedding_model_retrieval_results.csv"

RETRIEVAL_CSV = QUALITY_DIR / "bge_m3_four_mode_e2e_retrieval.csv"
COMPARISON_CSV = QUALITY_DIR / "bge_m3_four_mode_e2e_comparison.csv"
OUTPUTS_JSON = QUALITY_DIR / "bge_m3_four_mode_e2e_outputs.json"
CHECKPOINT_JSON = QUALITY_DIR / "bge_m3_four_mode_e2e_checkpoint.json"
REPORT_MD = PROJECT_ROOT / "docs" / "vector" / "BGE_M3_FOUR_MODE_E2E_ABLATION.md"

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
EXPECTED_DB_SHA256 = "8b528ebf2c98bf9456325c90a9e6bf8366c3f6f0d8c6208ae9df7288df239dbb"
EXPECTED_FAISS_SHA256 = "17ddc13efd6488138c8b63f65698fc6b7ad37c39a7224e83e3266fd70e64a6c7"
TEST_IDS = ("I01", "I02", "I03", "I04", "I05", "K01", "K02", "K03", "K04", "K05")
METHODS = (
    "bge_m3_dense",
    "bge_m3_priority_dense",
    "bge_m3_hybrid",
    "bge_m3_priority_hybrid",
)
NEW_METHODS = ("bge_m3_priority_dense", "bge_m3_hybrid")
REUSED_METHODS = ("bge_m3_dense", "bge_m3_priority_hybrid")
METHOD_FORMULAS = {
    "bge_m3_dense": "dense_score",
    "bge_m3_priority_dense": "dense_score * (0.90 + 0.10 * search_priority)",
    "bge_m3_hybrid": "0.60 * dense_score + 0.40 * lexical_score",
    "bge_m3_priority_hybrid": (
        "(0.60 * dense_score + 0.40 * lexical_score) "
        "* (0.90 + 0.10 * search_priority)"
    ),
}
PROTECTED_RESULTS = (
    BASELINE_COMPARISON,
    BASELINE_OUTPUTS,
    BASELINE_RETRIEVAL,
    QUALITY_DIR / "vector_retrieval_benchmark_v2_reviewed.json",
    QUALITY_DIR / "vector_retrieval_benchmark_v2_reviewed.csv",
    QUALITY_DIR / "vector_retrieval_benchmark_summary_v2_reviewed.csv",
    QUALITY_DIR / "vector_v2_final_report.json",
    QUALITY_DIR / "vector_retrieval_benchmark_insight.csv",
    QUALITY_DIR / "vector_retrieval_benchmark_summary_insight.csv",
    QUALITY_DIR / "bge_m3_build_report.json",
    QUALITY_DIR / "bge_m3_faiss_test_report.json",
    QUALITY_DIR / "bge_m3_unit_test_report.json",
)
SCORE_FIELDS = (
    "evidence_relevance",
    "financial_number_accuracy",
    "directness",
    "causal_grounding",
    "company_isolation",
    "year_isolation",
    "report_type_isolation",
    "rcept_no_consistency",
    "unsupported_number_control",
    "hallucination_control",
    "completeness",
    "conciseness",
)


class EvaluationError(RuntimeError):
    pass


@dataclass
class ApiError(EvaluationError):
    message: str
    status_code: int | None = None
    attempts: int = 1
    quota_error: bool = False

    def __str__(self) -> str:
        return self.message


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_new_text(path: Path, text: str) -> None:
    if path.exists():
        raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_new_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8-sig", newline="", dir=path.parent,
        prefix=f".{path.name}.", delete=False,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    temporary.replace(path)


def _baseline_hashes() -> dict[str, str]:
    missing = [str(path) for path in PROTECTED_RESULTS if not path.exists()]
    if missing:
        raise FileNotFoundError(f"기존 평가 결과가 없습니다: {missing}")
    return {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in PROTECTED_RESULTS}


def _assert_assets() -> None:
    actual_db = _sha256(BGE_DB_PATH)
    actual_faiss = _sha256(BGE_FAISS_PATH)
    if actual_db != EXPECTED_DB_SHA256:
        raise EvaluationError(f"BGE SQLite 해시 불일치: {actual_db}")
    if actual_faiss != EXPECTED_FAISS_SHA256:
        raise EvaluationError(f"BGE FAISS 해시 불일치: {actual_faiss}")


def _assert_protected_unchanged(
    expected: dict[str, str], *, check_assets: bool = True
) -> None:
    actual = _baseline_hashes()
    changed = [path for path, digest in expected.items() if actual.get(path) != digest]
    if changed:
        raise EvaluationError(f"기존 평가 결과가 변경되었습니다: {changed}")
    if check_assets:
        _assert_assets()


def _assert_frozen_retrievals_complete(checkpoint: dict[str, Any]) -> None:
    expected = {_record_key(test_id, method) for test_id in TEST_IDS for method in METHODS}
    actual = set(checkpoint.get("retrievals", {}))
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise EvaluationError(
            f"고정 검색 결과가 완전하지 않습니다: missing={missing}, extra={extra}"
        )
    recorded_assets = checkpoint.get("asset_hashes") or {}
    expected_assets = {
        str(BGE_DB_PATH.relative_to(PROJECT_ROOT)): EXPECTED_DB_SHA256,
        str(BGE_FAISS_PATH.relative_to(PROJECT_ROOT)): EXPECTED_FAISS_SHA256,
    }
    if recorded_assets != expected_assets:
        raise EvaluationError(
            f"체크포인트 원본 자산 해시가 예상값과 다릅니다: {recorded_assets}"
        )


def _load_baseline() -> dict[str, Any]:
    return json.loads(BASELINE_OUTPUTS.read_text(encoding="utf-8-sig"))


def _record_key(test_id: str, method: str) -> str:
    return f"{test_id}:{method}"


def _read_priorities() -> dict[str, float]:
    with sqlite3.connect(f"file:{BGE_DB_PATH}?mode=ro", uri=True) as connection:
        return {
            str(chunk_id): float(priority)
            for chunk_id, priority in connection.execute(
                "SELECT chunk_id, search_priority FROM filing_chunks"
            )
        }


def _compact_chunk(chunk: dict[str, Any], *, method: str, priority: float | None = None) -> dict[str, Any]:
    search_priority = float(chunk.get("search_priority", priority if priority is not None else 0.0))
    apply_priority = method in {"bge_m3_priority_dense", "bge_m3_priority_hybrid"}
    lexical = chunk.get("lexical_score", chunk.get("keyword_score"))
    return {
        "rank": int(chunk.get("rank", 0)),
        "corp_name": chunk.get("corp_name"),
        "year": chunk.get("year"),
        "report_type": chunk.get("report_type"),
        "rcept_no": chunk.get("rcept_no"),
        "section_name": chunk.get("section_name"),
        "subsection_name": chunk.get("subsection_name"),
        "chunk_id": chunk.get("chunk_id"),
        "chunk_text": chunk.get("chunk_text"),
        "dense_score": chunk.get("dense_score"),
        "lexical_score": lexical,
        "search_priority": search_priority,
        "search_priority_applied": apply_priority,
        "priority_multiplier": (0.90 + 0.10 * search_priority) if apply_priority else 1.0,
        "final_score": chunk.get("final_score", chunk.get("score")),
    }


def _format_chunks(chunks: list[dict[str, Any]]) -> str:
    blocks = []
    for chunk in chunks:
        blocks.append(
            "[rank={rank} | corp_name={corp_name} | year={year} | report_type={report_type} "
            "| rcept_no={rcept_no} | section={section_name} | chunk_id={chunk_id}]\n{chunk_text}".format(
                **chunk
            )
        )
    return "\n\n".join(blocks)


def replace_prompt_chunks(prompt: str, chunks: list[dict[str, Any]], feature_type: str) -> str:
    heading = "[키워드 관련 공시 검색 결과]" if feature_type == "keyword_insight" else "[공시 검색 결과]"
    suffix_marker = (
        "\n\n위 재무 데이터와 공시 정보를 바탕으로"
        if feature_type == "keyword_insight"
        else "\n\n위 데이터를 바탕으로"
    )
    if heading not in prompt or suffix_marker not in prompt:
        raise EvaluationError("기존 user prompt에서 공시 근거 경계를 찾지 못했습니다.")
    prefix = prompt.split(heading, 1)[0] + heading + "\n"
    suffix = suffix_marker + prompt.split(suffix_marker, 1)[1]
    return prefix + _format_chunks(chunks) + suffix


def _strip_json_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _parse_json(text: str) -> dict[str, Any]:
    payload = json.loads(_strip_json_fence(text))
    if not isinstance(payload, dict):
        raise ValueError("JSON 응답 최상위 값은 object여야 합니다.")
    return payload


def _sentence_count(text: str) -> int:
    pieces = [part.strip() for part in re.split(r"(?<=[.!?。])\s+|(?<=다\.)", text) if part.strip()]
    return max(1, len(pieces)) if text.strip() else 0


def _answer_text(parsed: dict[str, Any]) -> str:
    return " ".join(str(value) for value in parsed.values() if isinstance(value, str))


def auto_validate(record: dict[str, Any]) -> dict[str, Any]:
    parsed = record.get("parsed_response") or {}
    text = _answer_text(parsed)
    context = record.get("financial_data", "") + "\n" + "\n".join(
        str(chunk.get("chunk_text") or "") for chunk in record.get("retrieved_chunks", [])
    )
    numbers = set(re.findall(r"(?<![A-Za-z가-힣])\d+(?:[.,]\d+)*%?", text))
    unsupported_numbers = sorted(number for number in numbers if number not in context)
    companies = ("삼성전자", "SK하이닉스", "한미반도체", "주성엔지니어링", "원익IPS")
    context_companies = {company for company in companies if company in context}
    unexpected_companies = sorted(company for company in companies if company in text and company not in context_companies)
    receipts_in_context = set(re.findall(r"\b\d{14}\b", context))
    receipts_in_answer = set(re.findall(r"\b\d{14}\b", text))
    sentence_count = _sentence_count(text)
    sentence_limit = 2 if record.get("feature_type") == "keyword_insight" else 3
    forbidden = [
        term for term in ("매수", "매도", "목표주가", "투자 추천", "확실히 상승") if term in text
    ]
    required = set((record.get("json_schema") or {}).get("required", []))
    json_valid = bool(parsed) and required.issubset(parsed)
    checks = {
        "json_valid": json_valid,
        "sentence_count": sentence_count,
        "sentence_limit_ok": sentence_count <= sentence_limit,
        "forbidden_expressions": forbidden,
        "unsupported_numbers": unsupported_numbers,
        "unexpected_companies": unexpected_companies,
        "rcept_no_match": receipts_in_answer.issubset(receipts_in_context),
        "year_match": all(year == "2025" or year in context for year in re.findall(r"\b20\d{2}\b", text)),
        "report_type_match": True,
        "length_ok": len(text) <= 1200,
    }
    checks["status"] = "PASS" if (
        checks["json_valid"] and checks["sentence_limit_ok"] and not forbidden
        and not unsupported_numbers and not unexpected_companies and checks["rcept_no_match"]
        and checks["year_match"] and checks["length_ok"]
    ) else "REVIEW"
    return checks


class ExactGeminiClient:
    def __init__(self, api_key: str, timeout: int = 120, max_attempts: int = 2) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max_attempts

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict[str, Any]]:
        last_error: Exception | None = None
        started = time.perf_counter()
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(
                    f"{API_ROOT}/{model}:generateContent",
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "maxOutputTokens": max_tokens,
                            "responseMimeType": "application/json",
                            "responseSchema": schema,
                        },
                    },
                    timeout=self.timeout,
                )
            except requests.RequestException as error:
                last_error = error
                if attempt < self.max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise ApiError(
                    f"Gemini 네트워크 오류: {type(error).__name__}", attempts=attempt
                ) from error
            if response.ok:
                payload = response.json()
                candidate = (payload.get("candidates") or [{}])[0]
                parts = ((candidate.get("content") or {}).get("parts") or [])
                text = "".join(str(part.get("text") or "") for part in parts).strip()
                if not text:
                    raise ApiError("Gemini가 빈 응답을 반환했습니다.", attempts=attempt)
                return text, {
                    "model": model,
                    "finish_reason": candidate.get("finishReason"),
                    "usage": payload.get("usageMetadata") or {},
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "attempts": attempt,
                }
            status = response.status_code
            quota = status == 429
            retryable = status in {500, 502, 503, 504}
            message = f"Gemini API HTTP {status}"
            try:
                detail = response.json().get("error", {}).get("message")
                if detail:
                    message += f": {detail[:300]}"
            except (ValueError, AttributeError):
                pass
            if retryable and attempt < self.max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue
            raise ApiError(message, status_code=status, attempts=attempt, quota_error=quota)
        raise ApiError(f"Gemini 호출 실패: {last_error}", attempts=self.max_attempts)


def _judge_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {
        "method_label": {"type": "STRING"},
        **{field: {"type": "INTEGER", "minimum": 1, "maximum": 5} for field in SCORE_FIELDS},
        "company_contamination_found": {"type": "BOOLEAN"},
        "year_contamination_found": {"type": "BOOLEAN"},
        "report_type_contamination_found": {"type": "BOOLEAN"},
        "rcept_no_mismatch_found": {"type": "BOOLEAN"},
        "unsupported_number_found": {"type": "BOOLEAN"},
        "hallucination_found": {"type": "BOOLEAN"},
        "key_information_omitted": {"type": "BOOLEAN"},
        "judge_note": {"type": "STRING"},
    }
    return {
        "type": "OBJECT",
        "properties": {
            "evaluations": {
                "type": "ARRAY",
                "items": {"type": "OBJECT", "properties": properties, "required": list(properties)},
            },
            "ranking": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["evaluations", "ranking"],
    }


def _label_map(test_id: str) -> dict[str, str]:
    methods = list(METHODS)
    seed = int(hashlib.sha256(f"bge-m3-four-mode:{test_id}".encode()).hexdigest()[:16], 16)
    random.Random(seed).shuffle(methods)
    return {f"Method {chr(65 + index)}": method for index, method in enumerate(methods)}


def _judge_prompt(test_id: str, records: dict[str, dict[str, Any]], label_map: dict[str, str]) -> str:
    reference = records[_record_key(test_id, "bge_m3_dense")]
    blocks = []
    for label, method in label_map.items():
        record = records[_record_key(test_id, method)]
        evidence = _format_chunks(record["retrieved_chunks"])
        blocks.append(
            f"[{label}]\n공시 근거:\n{evidence}\n\n후보 답변:\n"
            f"{json.dumps(record['parsed_response'], ensure_ascii=False)}"
        )
    rubric = "\n".join(f"- {field}: 1~5점" for field in SCORE_FIELDS)
    return f"""다음은 동일한 질문과 재무 입력으로 생성된 네 후보 답변입니다.
검색 방식의 실제 이름은 공개되지 않았습니다. Method A/B/C/D를 독립적으로 평가하세요.

[test_id]
{test_id}

[질문]
{reference['query']}

[동일 재무 입력]
{reference['financial_data']}

[평가 점수]
{rubric}

[오류 플래그]
- 기업, 연도, 보고서, rcept_no 혼입/불일치
- 입력에 없는 숫자
- 근거 없는 추론 또는 환각
- 핵심 정보 누락

각 점수와 플래그는 각 Method가 제공받은 공시 근거와 재무 입력에 대해서만 판단하세요.
ranking은 가장 좋은 Method부터 네 개를 모두 포함하세요.

{chr(10).join(blocks)}"""


def _initial_checkpoint(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "experiment": copy.deepcopy(baseline["experiment"]),
        "asset_hashes": {
            str(BGE_DB_PATH.relative_to(PROJECT_ROOT)): EXPECTED_DB_SHA256,
            str(BGE_FAISS_PATH.relative_to(PROJECT_ROOT)): EXPECTED_FAISS_SHA256,
        },
        "protected_result_hashes": _baseline_hashes(),
        "retrievals": {},
        "candidates": {},
        "judges": {},
        "failures": [],
        "call_stats": {
            "candidate_successes": 0,
            "judge_successes": 0,
            "http_attempts": 0,
            "retries": 0,
            "quota_errors": 0,
            "errors": 0,
        },
    }


def _load_or_create_checkpoint(
    baseline: dict[str, Any], *, check_assets: bool = True
) -> dict[str, Any]:
    if CHECKPOINT_JSON.exists():
        checkpoint = json.loads(CHECKPOINT_JSON.read_text(encoding="utf-8"))
        _assert_protected_unchanged(
            checkpoint["protected_result_hashes"], check_assets=check_assets
        )
        return checkpoint
    checkpoint = _initial_checkpoint(baseline)
    _atomic_json(CHECKPOINT_JSON, checkpoint)
    return checkpoint


def _baseline_by_key(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _record_key(record["test_id"], record["retrieval_method"]): record
        for record in baseline["outputs"]
    }


def _prepare_reused(
    checkpoint: dict[str, Any], baseline_records: dict[str, dict[str, Any]], priorities: dict[str, float]
) -> None:
    for test_id in TEST_IDS:
        for method in REUSED_METHODS:
            source_method = "bge_m3_hybrid" if method == "bge_m3_priority_hybrid" else method
            source = baseline_records[_record_key(test_id, source_method)]
            key = _record_key(test_id, method)
            if key in checkpoint["candidates"]:
                continue
            record = copy.deepcopy(source)
            record["retrieval_method"] = method
            record["retrieval_formula"] = METHOD_FORMULAS[method]
            record["candidate_source"] = "reused_baseline"
            record["legacy_retrieval_method"] = source_method
            record["retrieval_latency_ms"] = None
            record["llm_latency_ms"] = None
            record["retrieved_chunks"] = [
                _compact_chunk(chunk, method=method, priority=priorities[chunk["chunk_id"]])
                for chunk in source["retrieved_chunks"]
            ]
            record["api_metadata"] = {**record.get("api_metadata", {}), "reused": True}
            checkpoint["retrievals"][key] = {
                "method": method,
                "formula": METHOD_FORMULAS[method],
                "latency_ms": None,
                "source": "reused_baseline",
                "chunks": record["retrieved_chunks"],
            }
            checkpoint["candidates"][key] = record
            _atomic_json(CHECKPOINT_JSON, checkpoint)


def _prepare_new_retrievals(
    checkpoint: dict[str, Any], baseline_records: dict[str, dict[str, Any]]
) -> None:
    for test_id in TEST_IDS:
        reference = baseline_records[_record_key(test_id, "bge_m3_dense")]
        for method in NEW_METHODS:
            key = _record_key(test_id, method)
            if key in checkpoint["retrievals"]:
                continue
            hybrid = method == "bge_m3_hybrid"
            apply_priority = method == "bge_m3_priority_dense"
            started = time.perf_counter()
            results = search_bge_m3(
                reference["query"], year=2025, report_type="FY", top_k=5,
                hybrid=hybrid, apply_priority=apply_priority,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            if len(results) != 5:
                raise EvaluationError(f"{key}: Top-K 결과가 5개가 아닙니다: {len(results)}")
            chunks = [_compact_chunk(result, method=method) for result in results]
            checkpoint["retrievals"][key] = {
                "method": method,
                "formula": METHOD_FORMULAS[method],
                "latency_ms": elapsed_ms,
                "source": "new_retrieval",
                "chunks": chunks,
            }
            _atomic_json(CHECKPOINT_JSON, checkpoint)


def _candidate_record(
    reference: dict[str, Any], method: str, retrieval: dict[str, Any], raw: str,
    parsed: dict[str, Any], metadata: dict[str, Any],
) -> dict[str, Any]:
    record = {
        "test_id": reference["test_id"],
        "feature_type": reference["feature_type"],
        "retrieval_method": method,
        "retrieval_formula": METHOD_FORMULAS[method],
        "candidate_source": "new_gemini_call",
        "query": reference["query"],
        "financial_data": reference["financial_data"],
        "retrieved_chunks": retrieval["chunks"],
        "model": reference["model"],
        "temperature": reference["temperature"],
        "max_tokens": reference["max_tokens"],
        "json_schema": reference["json_schema"],
        "prompt_version": reference["prompt_version"],
        "system_prompt": reference["system_prompt"],
        "user_prompt": replace_prompt_chunks(
            reference["user_prompt"], retrieval["chunks"], reference["feature_type"]
        ),
        "raw_response": raw,
        "parsed_response": parsed,
        "retrieval_latency_ms": retrieval["latency_ms"],
        "llm_latency_ms": metadata["latency_ms"],
        "api_metadata": metadata,
    }
    record["auto_validation"] = auto_validate(record)
    return record


def _record_failure(
    checkpoint: dict[str, Any], *, stage: str, test_id: str, method: str | None, error: Exception
) -> None:
    attempts = error.attempts if isinstance(error, ApiError) else 0
    quota = bool(error.quota_error) if isinstance(error, ApiError) else False
    checkpoint["call_stats"]["http_attempts"] += attempts
    checkpoint["call_stats"]["retries"] += max(0, attempts - 1)
    checkpoint["call_stats"]["errors"] += 1
    checkpoint["call_stats"]["quota_errors"] += int(quota)
    checkpoint["failures"].append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stage": stage,
        "test_id": test_id,
        "method": method,
        "error_type": type(error).__name__,
        "message": str(error),
        "status_code": error.status_code if isinstance(error, ApiError) else None,
        "attempts": attempts,
        "quota_error": quota,
    })
    _atomic_json(CHECKPOINT_JSON, checkpoint)


def _generate_new_candidates(
    checkpoint: dict[str, Any], baseline_records: dict[str, dict[str, Any]], client: ExactGeminiClient
) -> set[str]:
    blocked_models: set[str] = set()
    for test_id in TEST_IDS:
        reference = baseline_records[_record_key(test_id, "bge_m3_dense")]
        for method in NEW_METHODS:
            key = _record_key(test_id, method)
            if key in checkpoint["candidates"]:
                continue
            if reference["model"] in blocked_models:
                continue
            retrieval = checkpoint["retrievals"][key]
            user_prompt = replace_prompt_chunks(
                reference["user_prompt"], retrieval["chunks"], reference["feature_type"]
            )
            metadata: dict[str, Any] | None = None
            try:
                raw, metadata = client.generate(
                    model=reference["model"],
                    system_prompt=reference["system_prompt"],
                    user_prompt=user_prompt,
                    schema=reference["json_schema"],
                    max_tokens=int(reference["max_tokens"]),
                    temperature=float(reference["temperature"]),
                )
                checkpoint["call_stats"]["http_attempts"] += int(metadata.get("attempts", 1))
                checkpoint["call_stats"]["retries"] += max(0, int(metadata.get("attempts", 1)) - 1)
                parsed = _parse_json(raw)
                record = _candidate_record(reference, method, retrieval, raw, parsed, metadata)
            except Exception as error:
                _record_failure(
                    checkpoint, stage="candidate", test_id=test_id, method=method, error=error
                )
                if isinstance(error, ApiError) and error.quota_error:
                    blocked_models.add(reference["model"])
                continue
            checkpoint["candidates"][key] = record
            checkpoint["call_stats"]["candidate_successes"] += 1
            _atomic_json(CHECKPOINT_JSON, checkpoint)
    return blocked_models


def _validate_judge(payload: dict[str, Any], label_map: dict[str, str]) -> None:
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list) or len(evaluations) != 4:
        raise EvaluationError("Judge evaluations는 정확히 4개여야 합니다.")
    labels = {item.get("method_label") for item in evaluations}
    if labels != set(label_map):
        raise EvaluationError(f"Judge method label 불일치: {labels}")
    for item in evaluations:
        for field in SCORE_FIELDS:
            score = item.get(field)
            if not isinstance(score, int) or not 1 <= score <= 5:
                raise EvaluationError(f"Judge 점수 오류: {field}={score}")
    if set(payload.get("ranking") or []) != set(label_map):
        raise EvaluationError("Judge ranking은 Method A/B/C/D를 모두 포함해야 합니다.")


def _generate_judges(
    checkpoint: dict[str, Any], client: ExactGeminiClient, blocked_models: set[str] | None = None
) -> None:
    blocked_models = blocked_models if blocked_models is not None else set()
    records = checkpoint["candidates"]
    for test_id in TEST_IDS:
        if test_id in checkpoint["judges"]:
            continue
        needed = [_record_key(test_id, method) for method in METHODS]
        if any(key not in records for key in needed):
            continue
        label_map = _label_map(test_id)
        model = checkpoint["experiment"]["judge_models"][test_id]
        if model in blocked_models:
            continue
        prompt = _judge_prompt(test_id, records, label_map)
        metadata: dict[str, Any] | None = None
        raw = ""
        try:
            raw, metadata = client.generate(
                model=model,
                system_prompt=(
                    "당신은 DART 공시 기반 답변을 비교하는 엄격한 blind judge입니다. "
                    "검색 방식 이름을 추측하지 말고 제공된 근거와 답변만 평가합니다. "
                    "지정된 JSON 스키마만 출력합니다."
                ),
                user_prompt=prompt,
                schema=_judge_schema(),
                max_tokens=8192 if test_id == "K04" else 4096,
                temperature=float(checkpoint["experiment"]["temperature"]),
            )
            checkpoint["call_stats"]["http_attempts"] += int(metadata.get("attempts", 1))
            checkpoint["call_stats"]["retries"] += max(0, int(metadata.get("attempts", 1)) - 1)
            parsed = _parse_json(raw)
            _validate_judge(parsed, label_map)
        except Exception as error:
            if raw:
                finish_reason = (metadata or {}).get("finish_reason")
                error = EvaluationError(
                    f"{type(error).__name__}: {error}; raw_length={len(raw)}; "
                    f"finish_reason={finish_reason}; raw_tail={raw[-160:]!r}"
                )
            _record_failure(
                checkpoint, stage="judge", test_id=test_id, method=None, error=error
            )
            if isinstance(error, ApiError) and error.quota_error:
                blocked_models.add(model)
            continue
        checkpoint["judges"][test_id] = {
            "label_map": label_map,
            "judge_model": model,
            "judge": parsed,
            "raw_response": raw,
            "judge_api_metadata": metadata,
        }
        checkpoint["call_stats"]["judge_successes"] += 1
        _atomic_json(CHECKPOINT_JSON, checkpoint)


def _mapped_evaluations(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for test_id in TEST_IDS:
        judged = checkpoint["judges"].get(test_id)
        if not judged:
            continue
        for evaluation in judged["judge"]["evaluations"]:
            label = evaluation["method_label"]
            method = judged["label_map"][label]
            record = checkpoint["candidates"][_record_key(test_id, method)]
            row = {
                "test_id": test_id,
                "feature_type": record["feature_type"],
                "retrieval_method": method,
                "method_label": label,
                "candidate_source": record["candidate_source"],
                **{field: evaluation[field] for field in SCORE_FIELDS},
                "company_contamination_found": evaluation["company_contamination_found"],
                "year_contamination_found": evaluation["year_contamination_found"],
                "report_type_contamination_found": evaluation["report_type_contamination_found"],
                "rcept_no_mismatch_found": evaluation["rcept_no_mismatch_found"],
                "unsupported_number_found": evaluation["unsupported_number_found"],
                "hallucination_found": evaluation["hallucination_found"],
                "key_information_omitted": evaluation["key_information_omitted"],
                "judge_note": evaluation["judge_note"],
                "json_valid": record["auto_validation"]["json_valid"],
                "auto_validation_status": record["auto_validation"]["status"],
                "finish_reason": record["api_metadata"].get("finish_reason"),
                "candidate_model": record["model"],
                "judge_model": judged["judge_model"],
                "retrieval_latency_ms": record.get("retrieval_latency_ms"),
                "llm_latency_ms": record.get("llm_latency_ms"),
                "candidate_input_tokens": record["api_metadata"].get("usage", {}).get("promptTokenCount"),
                "candidate_output_tokens": record["api_metadata"].get("usage", {}).get("candidatesTokenCount"),
                "judge_input_tokens": judged["judge_api_metadata"].get("usage", {}).get("promptTokenCount"),
                "judge_output_tokens": judged["judge_api_metadata"].get("usage", {}).get("candidatesTokenCount"),
            }
            row["overall_score"] = sum(float(row[field]) for field in SCORE_FIELDS) / len(SCORE_FIELDS)
            rows.append(row)
    return rows


def _retrieval_rows(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for test_id in TEST_IDS:
        for method in METHODS:
            record = checkpoint["candidates"][_record_key(test_id, method)]
            for chunk in record["retrieved_chunks"]:
                rows.append({
                    "test_id": test_id,
                    "feature_type": record["feature_type"],
                    "retrieval_method": method,
                    "retrieval_formula": METHOD_FORMULAS[method],
                    "candidate_source": record["candidate_source"],
                    "retrieval_latency_ms": record.get("retrieval_latency_ms"),
                    **chunk,
                })
    return rows


def _mean(rows: list[dict[str, Any]], method: str, feature: str | None = None) -> float:
    values = [
        float(row["overall_score"]) for row in rows
        if row["retrieval_method"] == method and (feature is None or row["feature_type"] == feature)
    ]
    return sum(values) / len(values) if values else float("nan")


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    means = {
        "overall": {method: _mean(rows, method) for method in METHODS},
        "industry_insight": {method: _mean(rows, method, "industry_insight") for method in METHODS},
        "keyword_insight": {method: _mean(rows, method, "keyword_insight") for method in METHODS},
    }
    effects = {
        "priority_dense": means["overall"]["bge_m3_priority_dense"] - means["overall"]["bge_m3_dense"],
        "priority_hybrid": means["overall"]["bge_m3_priority_hybrid"] - means["overall"]["bge_m3_hybrid"],
        "lexical_pure": means["overall"]["bge_m3_hybrid"] - means["overall"]["bge_m3_dense"],
        "lexical_priority": means["overall"]["bge_m3_priority_hybrid"] - means["overall"]["bge_m3_priority_dense"],
    }
    winners: dict[str, list[str]] = {}
    for test_id in TEST_IDS:
        selected = [row for row in rows if row["test_id"] == test_id]
        best = max(row["overall_score"] for row in selected)
        winners[test_id] = [row["retrieval_method"] for row in selected if row["overall_score"] == best]
    feature_winners = {
        group: max(scores, key=scores.get) for group, scores in means.items() if group != "overall"
    }
    if len(set(feature_winners.values())) > 1:
        recommendation = "USE_DIFFERENT_METHOD_BY_FEATURE"
    else:
        winner = max(means["overall"], key=means["overall"].get)
        recommendation = {
            "bge_m3_dense": "PROMOTE_BGE_DENSE",
            "bge_m3_priority_dense": "PROMOTE_BGE_PRIORITY_DENSE",
            "bge_m3_hybrid": "PROMOTE_BGE_HYBRID",
            "bge_m3_priority_hybrid": "PROMOTE_BGE_PRIORITY_HYBRID",
        }[winner]
    return {
        "mean_scores": means,
        "effects": effects,
        "test_id_winners": winners,
        "hallucination_count": sum(bool(row["hallucination_found"]) for row in rows),
        "auto_review_count": sum(row["auto_validation_status"] == "REVIEW" for row in rows),
        "recommendation": recommendation,
    }


def _report(summary: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    mean_lines = []
    for method in METHODS:
        mean_lines.append(
            f"| `{method}` | {summary['mean_scores']['overall'][method]:.3f} | "
            f"{summary['mean_scores']['industry_insight'][method]:.3f} | "
            f"{summary['mean_scores']['keyword_insight'][method]:.3f} |"
        )
    winner_lines = [
        f"| {test_id} | {', '.join(methods)} |"
        for test_id, methods in summary["test_id_winners"].items()
    ]
    failures = checkpoint["failures"]
    return f"""# BGE-M3 Four-Mode E2E Ablation

## 실험 조건

- 기존 `bge_m3_dense` 후보 10개와, 과거 명칭 `bge_m3_hybrid`였던
  `bge_m3_priority_hybrid` 후보 10개를 재사용했다.
- 신규 후보는 `bge_m3_priority_dense`, `bge_m3_hybrid`만 생성했다.
- 2025 FY, 기업 필터 없음, Top-K=5, temperature=0.1 조건을 유지했다.
- 후보 및 Judge 모델은 기존 test_id별 매핑을 그대로 사용했고 모델 fallback은 금지했다.
- 네 후보는 test_id별 결정적 순서의 Method A/B/C/D로 익명화해 새 Judge 평가를 수행했다.

## 점수 공식

| 방식 | 공식 |
|---|---|
{chr(10).join(f'| `{method}` | `{formula}` |' for method, formula in METHOD_FORMULAS.items())}

## 평균 결과

| 방식 | 전체 | 산업 인사이트 | 키워드 인사이트 |
|---|---:|---:|---:|
{chr(10).join(mean_lines)}

## 분리 효과

- Dense에서 priority 효과: {summary['effects']['priority_dense']:+.3f}
- Hybrid에서 priority 효과: {summary['effects']['priority_hybrid']:+.3f}
- Pure 조건에서 lexical 효과: {summary['effects']['lexical_pure']:+.3f}
- Priority 조건에서 lexical 효과: {summary['effects']['lexical_priority']:+.3f}

## test_id별 최고 방식

| test_id | 최고 방식 |
|---|---|
{chr(10).join(winner_lines)}

## 품질 및 호출 현황

- 기존 후보 재사용: 20개
- 신규 Gemini 후보 성공: {checkpoint['call_stats']['candidate_successes']}개
- 신규 Judge 성공: {checkpoint['call_stats']['judge_successes']}개
- 실패 기록: {len(failures)}건
- 재시도: {checkpoint['call_stats']['retries']}회
- quota 오류: {checkpoint['call_stats']['quota_errors']}건
- Judge 환각 플래그: {summary['hallucination_count']}건
- 자동 REVIEW: {summary['auto_review_count']}건

## 제한사항

- 기존 재사용 후보에는 과거 실행 당시 검색/LLM 지연시간이 저장되지 않아 해당 값은 null이다.
- 40개 검색 결과는 원래 BGE 자산에서 체크포인트에 고정된 값을 사용했다. 이후 운영
  DB와 FAISS가 교체되어 재개 실행에서는 현재 운영 자산을 읽거나 재검색하지 않았다.
- K04 Judge는 4,096 출력 토큰에서 반복적으로 JSON이 잘려(`MAX_TOKENS`) 동일 모델·
  프롬프트·스키마를 유지하고 출력 상한만 8,192로 높여 완료했다.
- 표본은 산업 인사이트 5개와 키워드 인사이트 5개, 총 10개다.
- Streamlit 통합 질문에서 재무 계정 과다 조회, 순이익 중복, CF/SCE 편중,
  사업 내용·부문별 실적 근거 부족 문제가 알려져 있다. 기존 후보와의 조건 통제를 위해
  이번 실험 중 질의 확장이나 섹션 우선순위는 변경하지 않았다. 이는 별도 후속 과제다.

## 최종 추천

**{summary['recommendation']}**
"""


def _finalize(checkpoint: dict[str, Any], *, check_assets: bool = True) -> None:
    if len(checkpoint["candidates"]) != 40 or len(checkpoint["judges"]) != 10:
        raise EvaluationError(
            f"평가 미완료: candidates={len(checkpoint['candidates'])}/40, "
            f"judges={len(checkpoint['judges'])}/10"
        )
    for path in (RETRIEVAL_CSV, COMPARISON_CSV, OUTPUTS_JSON, REPORT_MD):
        if path.exists():
            raise FileExistsError(f"기존 결과를 덮어쓰지 않습니다: {path}")
    _assert_protected_unchanged(
        checkpoint["protected_result_hashes"], check_assets=check_assets
    )
    comparison = _mapped_evaluations(checkpoint)
    summary = _summary(comparison)
    output = {
        "experiment": {
            **checkpoint["experiment"],
            "methods": METHOD_FORMULAS,
            "reused_legacy_mapping": {"bge_m3_hybrid": "bge_m3_priority_hybrid"},
            "asset_hashes": checkpoint["asset_hashes"],
            "protected_result_hashes": checkpoint["protected_result_hashes"],
        },
        "outputs": [
            checkpoint["candidates"][_record_key(test_id, method)]
            for test_id in TEST_IDS for method in METHODS
        ],
        "blind_judging": checkpoint["judges"],
        "call_stats": checkpoint["call_stats"],
        "failures": checkpoint["failures"],
        "summary": summary,
    }
    _write_new_csv(RETRIEVAL_CSV, _retrieval_rows(checkpoint))
    _write_new_csv(COMPARISON_CSV, comparison)
    _write_new_text(OUTPUTS_JSON, json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    _write_new_text(REPORT_MD, _report(summary, checkpoint))
    _assert_protected_unchanged(
        checkpoint["protected_result_hashes"], check_assets=check_assets
    )


def run(
    *, prepare_only: bool = False, resume_frozen_retrievals: bool = False
) -> dict[str, Any]:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    if not resume_frozen_retrievals:
        _assert_assets()
    baseline = _load_baseline()
    checkpoint = _load_or_create_checkpoint(
        baseline, check_assets=not resume_frozen_retrievals
    )
    baseline_records = _baseline_by_key(baseline)
    if resume_frozen_retrievals:
        _assert_frozen_retrievals_complete(checkpoint)
    else:
        _prepare_reused(checkpoint, baseline_records, _read_priorities())
        _prepare_new_retrievals(checkpoint, baseline_records)
    _assert_protected_unchanged(
        checkpoint["protected_result_hashes"],
        check_assets=not resume_frozen_retrievals,
    )
    if prepare_only:
        return checkpoint

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise EvaluationError(
            "GEMINI_API_KEY가 없습니다. 체크포인트와 검색 결과는 보존됐으며, 키 설정 후 재실행하면 이어집니다."
        )
    client = ExactGeminiClient(api_key)
    blocked_models = _generate_new_candidates(checkpoint, baseline_records, client)
    _generate_judges(checkpoint, client, blocked_models)
    if len(checkpoint["candidates"]) == 40 and len(checkpoint["judges"]) == 10:
        _finalize(checkpoint, check_assets=not resume_frozen_retrievals)
    _assert_protected_unchanged(
        checkpoint["protected_result_hashes"],
        check_assets=not resume_frozen_retrievals,
    )
    return checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description="BGE-M3 네 검색 방식 E2E blind ablation")
    parser.add_argument(
        "--prepare-only", action="store_true",
        help="기존 후보 재사용과 신규 검색만 체크포인트에 저장하고 Gemini는 호출하지 않습니다.",
    )
    parser.add_argument(
        "--resume-frozen-retrievals", action="store_true",
        help=(
            "운영 BGE 자산이 교체된 경우에도 완성된 체크포인트의 40개 검색 결과만 "
            "사용해 후보 생성과 Judge를 재개합니다. 현재 DB/FAISS는 읽지 않습니다."
        ),
    )
    args = parser.parse_args()
    checkpoint = run(
        prepare_only=args.prepare_only,
        resume_frozen_retrievals=args.resume_frozen_retrievals,
    )
    print(json.dumps({
        "candidate_records": len(checkpoint["candidates"]),
        "retrieval_records": len(checkpoint["retrievals"]),
        "judge_records": len(checkpoint["judges"]),
        "call_stats": checkpoint["call_stats"],
        "checkpoint": str(CHECKPOINT_JSON.relative_to(PROJECT_ROOT)),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
