from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from src.config import PROCESSED_DIR, RAW_DIR


REQUIRED_METADATA = {
    "corp_name", "corp_code", "year", "report_type", "rcept_no",
    "section_name", "subsection_name", "source_file",
}


def inspect_filing(filing: dict) -> dict:
    relative = Path(filing["corp_code"]) / str(filing["year"]) / filing["report_type"]
    zip_path = RAW_DIR / "filings" / "zip" / relative / f"{filing['rcept_no']}.zip"
    xml_dir = RAW_DIR / "filings" / "xml" / relative / filing["rcept_no"]
    parsed_path = PROCESSED_DIR / "filings" / "parsed" / relative / f"{filing['rcept_no']}.json"
    chunk_path = PROCESSED_DIR / "filings" / "chunks" / relative / f"{filing['rcept_no']}.jsonl"

    xml_files = list(xml_dir.glob("*")) if xml_dir.exists() else []
    sections: list[dict] = []
    chunks: list[dict] = []
    errors: list[str] = []
    if parsed_path.exists():
        try:
            sections = json.loads(parsed_path.read_text(encoding="utf-8")).get("sections", [])
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"parsed_error:{exc}")
    if chunk_path.exists():
        try:
            chunks = [json.loads(line) for line in chunk_path.read_text(encoding="utf-8").splitlines() if line]
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"chunk_error:{exc}")

    empty_sections = sum(not item.get("cleaned_text", "").strip() for item in sections)
    empty_chunks = sum(not item.get("cleaned_text", "").strip() for item in chunks)
    metadata_missing = sum(
        sum(key not in item or item.get(key) in (None, "") for key in REQUIRED_METADATA - {"subsection_name"})
        for item in chunks
    )
    hashes = [hashlib.sha256(item.get("cleaned_text", "").encode("utf-8")).hexdigest() for item in chunks if item.get("cleaned_text")]
    duplicate_chunks = len(hashes) - len(set(hashes))
    short_chunks = sum(0 < len(item.get("cleaned_text", "")) < 100 for item in chunks)
    long_chunks = sum(len(item.get("cleaned_text", "")) > 2200 for item in chunks)

    missing_files = []
    for exists, label in ((zip_path.exists(), "zip"), (bool(xml_files), "xml"), (parsed_path.exists(), "parsed"), (chunk_path.exists(), "chunks")):
        if not exists:
            missing_files.append(label)
    if missing_files:
        errors.append("missing:" + ",".join(missing_files))
    has_quality_warning = any((empty_sections, empty_chunks, long_chunks, metadata_missing))
    has_filterable_items = any((duplicate_chunks, short_chunks))
    status = "failed" if errors else (
        "warning" if has_quality_warning else ("ok_with_filters" if has_filterable_items else "ok")
    )
    return {
        **{key: filing[key] for key in ("corp_name", "corp_code", "year", "report_type", "rcept_no")},
        "zip_exists": zip_path.exists(),
        "xml_count": len(xml_files),
        "section_count": len(sections),
        "chunk_count": len(chunks),
        "empty_section_count": empty_sections,
        "empty_chunk_count": empty_chunks,
        "duplicate_chunk_count": duplicate_chunks,
        "short_chunk_count": short_chunks,
        "long_chunk_count": long_chunks,
        "metadata_missing_count": metadata_missing,
        "status": status,
        "reason": " | ".join(errors),
    }


def build_quality_report(filings: list[dict]) -> pd.DataFrame:
    report = pd.DataFrame(inspect_filing(filing) for filing in filings)
    output = PROCESSED_DIR / "quality" / "filing_quality_report.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(output, index=False, encoding="utf-8-sig")
    return report
