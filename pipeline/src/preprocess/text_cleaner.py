from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

from src.config import PROCESSED_DIR, PROJECT_ROOT


TITLE_TAGS = {"title", "section-title", "toc-title"}
SKIP_TAGS = {"script", "style", "meta", "link", "head"}
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
MAJOR_SECTION = re.compile(r"^(?:[IVXLC]+\.|[가-힣]+\s*\.|【)", re.I)


def decode_document(content: bytes) -> str:
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_filing_document(xml_path: Path, filing: dict) -> list[dict]:
    """Extract title-delimited sections; source XML remains untouched."""
    source = decode_document(xml_path.read_bytes())
    soup = BeautifulSoup(source, "html.parser")
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()

    sections: list[dict] = []
    current_name = "문서 본문"
    current_subsection: str | None = None
    current_parts: list[str] = []

    def flush() -> None:
        original_text = "\n".join(part for part in current_parts if part.strip()).strip()
        cleaned_text = clean_text(original_text)
        if not cleaned_text:
            return
        sections.append(
            {
                **filing,
                "section_name": current_name,
                "subsection_name": current_subsection,
                "source_file": str(xml_path.relative_to(PROJECT_ROOT)),
                "original_text": original_text,
                "cleaned_text": cleaned_text,
            }
        )

    body = soup.body or soup
    for element in body.descendants:
        if isinstance(element, Tag) and element.name.lower() in TITLE_TAGS:
            title = clean_text(element.get_text(" ", strip=True))
            if title:
                flush()
                current_parts = []
                if MAJOR_SECTION.match(title):
                    current_name = title
                    current_subsection = None
                elif current_name == "문서 본문":
                    current_name = title
                    current_subsection = None
                else:
                    current_subsection = title
        elif isinstance(element, NavigableString):
            if element.parent and element.parent.name.lower() in TITLE_TAGS | SKIP_TAGS:
                continue
            value = str(element).strip()
            if value:
                current_parts.append(value)
    flush()
    return sections


def chunk_sections(sections: list[dict], max_chars: int = 1800, overlap_chars: int = 200) -> list[dict]:
    chunks: list[dict] = []
    for section_index, section in enumerate(sections):
        text = section["original_text"]
        start = 0
        chunk_index = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
                if boundary > start + max_chars // 2:
                    end = boundary + 1
            original_chunk = text[start:end].strip()
            cleaned_chunk = clean_text(original_chunk)
            if cleaned_chunk:
                chunks.append(
                    {
                        **{key: section[key] for key in (
                            "corp_name", "corp_code", "report_name", "report_type", "year",
                            "rcept_no", "section_name", "subsection_name", "source_file",
                        )},
                        "chunk_id": f"{section['rcept_no']}:{section_index}:{chunk_index}",
                        "chunk_index": chunk_index,
                        "original_text": original_chunk,
                        "cleaned_text": cleaned_chunk,
                        "chunk_text": cleaned_chunk,
                    }
                )
            if end >= len(text):
                break
            start = max(end - overlap_chars, start + 1)
            chunk_index += 1
    return chunks


def save_parsed_filing(filing: dict, sections: list[dict], chunks: list[dict]) -> tuple[Path, Path]:
    relative = Path(filing["corp_code"]) / str(filing["year"]) / filing["report_type"]
    parsed_path = PROCESSED_DIR / "filings" / "parsed" / relative / f"{filing['rcept_no']}.json"
    chunk_path = PROCESSED_DIR / "filings" / "chunks" / relative / f"{filing['rcept_no']}.jsonl"
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_path.parent.mkdir(parents=True, exist_ok=True)
    parsed_path.write_text(
        json.dumps({"metadata": filing, "sections": sections}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with chunk_path.open("w", encoding="utf-8") as output:
        for chunk in chunks:
            output.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return parsed_path, chunk_path
