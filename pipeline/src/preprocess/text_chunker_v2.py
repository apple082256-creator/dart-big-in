from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from src.config import PROCESSED_DIR
from src.preprocess.text_cleaner import clean_text


CHUNKS_V2_DIR = PROCESSED_DIR / "filings" / "chunks_v2"
TARGET_TOKENS = 600
OVERLAP_TOKENS = 100
CHUNK_VERSION = "bge-m3-token-v2"

HIGH_VALUE_TERMS = (
    "사업", "제품", "서비스", "시장", "경쟁", "연구개발", "설비", "투자",
    "위험", "재무", "계약", "전략", "매출", "수주", "원재료", "생산",
)


def _units(text: str) -> list[str]:
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pieces = re.split(r"(?<=[.!?。])\s+", line)
        units.extend(piece.strip() for piece in pieces if piece.strip())
    return units or ([text.strip()] if text.strip() else [])


def _hard_split(text: str, tokenizer, target_tokens: int) -> list[str]:
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True, truncation=False)
    offsets = encoded["offset_mapping"]
    if len(offsets) <= target_tokens:
        return [text]
    parts: list[str] = []
    for start in range(0, len(offsets), target_tokens):
        end = min(start + target_tokens, len(offsets))
        char_start = offsets[start][0]
        char_end = offsets[end - 1][1]
        part = text[char_start:char_end].strip()
        if part:
            parts.append(part)
    return parts


def _token_count(text: str, tokenizer) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False, truncation=False))


def _classify(text: str, heading: str, token_count: int) -> tuple[str, float, str]:
    combined = f"{heading} {text}"
    if "목차" in heading.replace(" ", ""):
        return "toc", 0.15, "목차는 보존하되 검색 우선순위를 낮춤"
    if any(term in heading for term in ("임원", "주주총회", "이사회", "보수")):
        return "governance", 0.30, "지배구조·임원 정보로 분류해 낮은 우선순위로 보존"
    if any(term in heading for term in ("재무제표", "재무상태표", "손익계산서", "현금흐름표", "자본변동표")):
        return "financial_statement", 0.75, "재무제표 본문으로 보존"
    meaningful = sum(char.isalpha() or "가" <= char <= "힣" for char in text)
    digit_ratio = sum(char.isdigit() for char in text) / max(len(text), 1)
    if digit_ratio >= 0.25 or meaningful / max(len(text), 1) < 0.18:
        return "table", 0.40, "숫자·표 중심 청크로 보존"
    if len(text) < 100 or token_count < 30:
        return "short_statement", 0.35, "짧은 문장을 삭제하지 않고 낮은 우선순위로 보존"
    if any(term in combined for term in HIGH_VALUE_TERMS):
        return "narrative", 1.00, "핵심 사업·재무 용어가 포함된 서술형 청크"
    return "narrative", 0.70, "일반 서술형 청크"


def chunk_sections_v2(
    sections: list[dict], tokenizer, target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[dict]:
    if target_tokens <= 0 or not 0 <= overlap_tokens < target_tokens:
        raise ValueError("0 <= overlap_tokens < target_tokens is required")
    chunks: list[dict] = []
    for section_index, section in enumerate(sections):
        atoms: list[tuple[str, int]] = []
        for unit in _units(section.get("original_text") or section.get("cleaned_text") or ""):
            for part in _hard_split(unit, tokenizer, target_tokens):
                atoms.append((part, _token_count(part, tokenizer)))
        start = 0
        chunk_index = 0
        while start < len(atoms):
            end = start
            total = 0
            while end < len(atoms) and (total + atoms[end][1] <= target_tokens or end == start):
                total += atoms[end][1]
                end += 1
            original_text = "\n".join(text for text, _ in atoms[start:end]).strip()
            cleaned_text = clean_text(original_text)
            if cleaned_text:
                actual_tokens = _token_count(cleaned_text, tokenizer)
                heading = " ".join(filter(None, (
                    section.get("section_name"), section.get("subsection_name"),
                )))
                content_type, priority, reason = _classify(cleaned_text, heading, actual_tokens)
                chunk_id = f"{section['rcept_no']}:v2:{section_index}:{chunk_index}"
                chunks.append({
                    **{key: section.get(key) for key in (
                        "corp_name", "corp_code", "report_name", "report_type", "year",
                        "rcept_no", "section_name", "subsection_name", "source_file",
                        "source_document_id", "source_document_sha256",
                    )},
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "original_text": original_text,
                    "cleaned_text": cleaned_text,
                    "chunk_text": cleaned_text,
                    "content_hash": hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest(),
                    "chunk_version": CHUNK_VERSION,
                    "token_count": actual_tokens,
                    "content_type": content_type,
                    "search_priority": priority,
                    "selection_reason": reason,
                    "is_searchable": True,
                })
            if end >= len(atoms):
                break
            next_start = end
            overlap = 0
            while next_start > start + 1 and overlap < overlap_tokens:
                next_start -= 1
                overlap += atoms[next_start][1]
            start = next_start if next_start > start else end
            chunk_index += 1
    return chunks


def build_chunks_v2(
    tokenizer,
    output_dir: Path = CHUNKS_V2_DIR,
    corp_codes: set[str] | None = None,
) -> dict[str, int]:
    parsed_files = sorted((PROCESSED_DIR / "filings" / "parsed").rglob("*.json"))
    if corp_codes is not None:
        parsed_files = [
            path for path in parsed_files
            if path.relative_to(PROCESSED_DIR / "filings" / "parsed").parts[0] in corp_codes
        ]
    files = sections_count = chunks_count = 0
    type_counts: dict[str, int] = {}
    for parsed_path in parsed_files:
        payload = json.loads(parsed_path.read_text(encoding="utf-8"))
        sections = payload.get("sections", [])
        chunks = chunk_sections_v2(sections, tokenizer)
        relative = parsed_path.relative_to(PROCESSED_DIR / "filings" / "parsed").with_suffix(".jsonl")
        output_path = output_dir / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks)
        if output_path.exists():
            if output_path.read_text(encoding="utf-8") != content:
                raise FileExistsError(f"기존 청킹 결과를 보존합니다: {output_path}")
        else:
            output_path.write_text(content, encoding="utf-8")
        files += 1
        sections_count += len(sections)
        chunks_count += len(chunks)
        for chunk in chunks:
            key = chunk["content_type"]
            type_counts[key] = type_counts.get(key, 0) + 1
    return {"files": files, "sections": sections_count, "chunks": chunks_count, **type_counts}
