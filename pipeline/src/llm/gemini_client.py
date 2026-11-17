from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.config import PROJECT_ROOT


DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
)
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


class UnsafePromptError(ValueError):
    """Raised before a request when a prompt appears to contain private data."""


def _assert_public_prompt(prompt: str, secret: str) -> None:
    forbidden_literals = (
        str(PROJECT_ROOT),
        str(Path.home()),
        "/Users/",
        "file://",
        ".env",
        "DART_API_KEY",
        "GEMINI_API_KEY",
    )
    found = [value for value in forbidden_literals if value and value in prompt]
    if secret and secret in prompt:
        found.append("configured API secret")
    if re.search(r"(?:^|\s)(?:[A-Za-z]:\\|/(?:private|var|tmp|home)/)\S+", prompt):
        found.append("absolute local path")
    if found:
        raise UnsafePromptError("외부 전송 차단: 비공개 정보로 의심되는 문자열이 포함되었습니다.")


class GeminiClient:
    def __init__(self, model: str | None = None, timeout: int = 90) -> None:
        load_dotenv(PROJECT_ROOT / ".env")
        self._api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY가 없습니다. 프로젝트 .env 파일을 확인하세요.")
        self.model = (model or (os.getenv("GEMINI_MODEL") or DEFAULT_MODEL)).strip()
        if self.model.startswith("models/"):
            self.model = self.model.removeprefix("models/")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", self.model):
            raise ValueError("GEMINI_MODEL 이름이 올바르지 않습니다.")
        self.last_model = self.model
        self.last_finish_reason: str | None = None
        self.timeout = timeout

    def __call__(self, prompt: str) -> str:
        _assert_public_prompt(prompt, self._api_key)
        response = None
        last_network_error: requests.RequestException | None = None
        candidates = tuple(dict.fromkeys((self.model, *FALLBACK_MODELS)))
        for candidate in candidates:
            for attempt in range(3):
                try:
                    response = requests.post(
                        f"{API_ROOT}/{candidate}:generateContent",
                        headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                        json={
                            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
                        },
                        timeout=self.timeout,
                    )
                except requests.RequestException as error:
                    last_network_error = error
                    response = None
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                    continue
                if response.status_code not in {429, 500, 502, 503, 504}:
                    break
                if attempt < 2:
                    time.sleep(2 ** attempt)
            if response is not None and response.ok:
                self.last_model = candidate
                break
        if response is None:
            raise RuntimeError("Gemini API 네트워크 호출이 재시도 후에도 실패했습니다.") from last_network_error
        if not response.ok:
            raise RuntimeError(f"Gemini API 호출 실패: HTTP {response.status_code}")
        payload = response.json()
        self.last_finish_reason = payload.get("candidates", [{}])[0].get("finishReason")
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            answer = "".join(part.get("text", "") for part in parts).strip()
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError("Gemini API 응답에서 답변 텍스트를 찾지 못했습니다.") from error
        if not answer:
            raise RuntimeError("Gemini API가 빈 답변을 반환했습니다.")
        return answer
