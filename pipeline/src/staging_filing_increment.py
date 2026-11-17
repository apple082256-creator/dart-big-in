"""Parse and token-chunk a period_increment collection inside staging only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.healthcare_rnd_pipeline import document_id
from src.preprocess.text_chunker_v2 import chunk_sections_v2
from src.preprocess.text_cleaner import parse_filing_document
from src.retrieval.bge_m3_indexer import load_tokenizer


ALLOWED_REPORTS = {"Q1", "H1"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(collection_path: Path, parsed_dir: Path, chunks_dir: Path) -> dict[str, object]:
    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    statuses = collection.get("statuses") or []
    slots = {(row["corp_code"], int(row["year"]), row["report_type"]) for row in statuses}
    if len(statuses) != 50 or len(slots) != 50:
        raise ValueError(f"expected 50 unique slots, got rows={len(statuses)} unique={len(slots)}")
    if {row["report_type"] for row in statuses} != ALLOWED_REPORTS:
        raise ValueError("only 2026 Q1/H1 collections are allowed")
    if {int(row["year"]) for row in statuses} != {2026}:
        raise ValueError("only year 2026 is allowed")

    tokenizer = load_tokenizer()
    totals = {"filings": 0, "sections": 0, "chunks": 0}
    rows = []
    for status in sorted(statuses, key=lambda row: (row["corp_code"], row["report_type"])):
        documents = [Path(item["path"]).resolve() for item in status.get("documents") or [] if item.get("main")]
        if len(documents) != 1 or not documents[0].is_file():
            raise FileNotFoundError(f"one main XML required for {status['corp_code']} {status['report_type']}")
        source = documents[0]
        document_sha256 = _sha256(source)
        if document_sha256 != next(item["sha256"] for item in status["documents"] if item.get("main")):
            raise ValueError(f"document checksum changed: {source}")
        filing = {
            "corp_name": status["company"], "corp_code": status["corp_code"],
            "report_name": status["filings"][-1]["report_nm"],
            "report_type": status["report_type"], "year": int(status["year"]),
            "rcept_no": status["latest_rcept_no"],
        }
        sections = parse_filing_document(source, filing)
        for section in sections:
            # Canonical ID of MySQL filing_source_documents; the file digest is kept separately.
            # The ID follows source_file, so it must be recomputed if the XML is relocated.
            section["source_document_id"] = document_id(filing["rcept_no"], section["source_file"])
            section["source_document_sha256"] = document_sha256
        chunks = chunk_sections_v2(sections, tokenizer)
        if len({item["chunk_id"] for item in chunks}) != len(chunks):
            raise ValueError(f"duplicate chunk IDs in {source}")
        relative = Path(status["corp_code"]) / "2026" / status["report_type"]
        parsed_path = parsed_dir / relative / f"{status['latest_rcept_no']}.json"
        chunk_path = chunks_dir / relative / f"{status['latest_rcept_no']}.jsonl"
        parsed_path.parent.mkdir(parents=True, exist_ok=True)
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        parsed_path.write_text(json.dumps({"metadata": filing, "document_sha256": document_sha256,
                                           "sections": sections}, ensure_ascii=False, indent=2), encoding="utf-8")
        chunk_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in chunks),
                              encoding="utf-8")
        totals["filings"] += 1
        totals["sections"] += len(sections)
        totals["chunks"] += len(chunks)
        rows.append({"corp_code": status["corp_code"], "report_type": status["report_type"],
                     "rcept_no": status["latest_rcept_no"], "document_sha256": document_sha256,
                     "sections": len(sections), "chunks": len(chunks), "source_path": str(source)})
    return {**totals, "slots": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--parsed-dir", type=Path, required=True)
    parser.add_argument("--chunks-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.collection, args.parsed_dir, args.chunks_dir)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("filings", "sections", "chunks")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
