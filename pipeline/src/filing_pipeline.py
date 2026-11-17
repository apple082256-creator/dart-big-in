from __future__ import annotations

import argparse
import csv
from pathlib import Path

from src.collect.filing_collector import FilingClient, build_filing_index_with_search
from src.collect.financial_collector import DartClient
from src.config import BANK_COMPANY_PROFILES, DEFAULT_COMPANIES, DEFAULT_YEARS, ensure_directories, load_settings
from src.preprocess.text_cleaner import chunk_sections, parse_filing_document, save_parsed_filing
from src.quality.filing_quality import build_quality_report
from src.config import PROCESSED_DIR


def run_filing_pipeline(
    companies: tuple[str, ...],
    years: tuple[int, ...],
    refresh: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    ensure_directories()
    settings = load_settings(companies, years)
    dart = DartClient(settings.api_key)
    if all(name in BANK_COMPANY_PROFILES for name in settings.companies):
        company_master = dart.resolve_companies_by_stock_code(tuple(
            BANK_COMPANY_PROFILES[name]["stock_code"] for name in settings.companies
        ))
    else:
        company_master = dart.resolve_companies(settings.companies)
    client = FilingClient(settings.api_key)
    filings = build_filing_index_with_search(client, company_master, settings.years)
    if limit is not None:
        filings = filings[:limit]
    if not filings:
        raise RuntimeError("재무 원본 JSON에서 공시 접수번호를 찾지 못했습니다. 재무 파이프라인을 먼저 실행하세요.")

    document_count = section_count = chunk_count = failed_count = 0
    collection_rows: list[dict] = []
    for index, filing in enumerate(filings, start=1):
        try:
            candidates = [filing["rcept_no"], *filing.get("fallback_rcept_nos", [])]
            xml_files = None
            selected_filing = filing
            errors: list[str] = []
            for rcept_no in dict.fromkeys(candidates):
                candidate = {**filing, "rcept_no": rcept_no}
                try:
                    xml_files = client.download_original_document(candidate, refresh=refresh)
                    selected_filing = candidate
                    if rcept_no != filing["rcept_no"]:
                        selected_filing["receipt_source"] = "downloadable_amendment_fallback"
                    break
                except Exception as exc:
                    errors.append(f"{rcept_no}: {exc}")
            if xml_files is None:
                raise RuntimeError("; ".join(errors))
            filing = selected_filing
            sections: list[dict] = []
            for xml_path in xml_files:
                sections.extend(parse_filing_document(xml_path, filing))
            chunks = chunk_sections(sections)
            save_parsed_filing(filing, sections, chunks)
            document_count += 1
            section_count += len(sections)
            chunk_count += len(chunks)
            collection_rows.append({**filing, "status": "ok", "reason": ""})
            print(f"[{index}/{len(filings)}] {filing['corp_name']} {filing['year']} {filing['report_type']}: {len(sections)} sections, {len(chunks)} chunks", flush=True)
        except Exception as exc:
            failed_count += 1
            collection_rows.append({**filing, "status": "failed", "reason": str(exc)})
            print(f"[{index}/{len(filings)}] FAILED {filing['rcept_no']}: {exc}", flush=True)
    collection_path = PROCESSED_DIR / "quality" / "filing_collection_report.csv"
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    with collection_path.open("w", encoding="utf-8-sig", newline="") as output:
        fieldnames = list(dict.fromkeys(
            key for row in collection_rows for key in row if key != "fallback_rcept_nos"
        ))
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(collection_rows)
    build_quality_report(filings)
    return {"filings": document_count, "sections": section_count, "chunks": chunk_count, "failed": failed_count}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DART 공시 원문 XML 수집·파싱 파이프라인")
    parser.add_argument("--companies", nargs="+", default=list(DEFAULT_COMPANIES))
    parser.add_argument("--years", nargs="+", type=int, default=list(DEFAULT_YEARS))
    parser.add_argument("--limit", type=int, help="검증 또는 분할 실행 시 처리할 최대 공시 수")
    parser.add_argument("--refresh", action="store_true", help="보존된 ZIP/XML을 새로 내려받음")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_filing_pipeline(tuple(args.companies), tuple(args.years), args.refresh, args.limit)
    print(
        f"완료: 공시 {result['filings']}건, 섹션 {result['sections']:,}건, "
        f"청크 {result['chunks']:,}건, 실패 {result['failed']}건"
    )


if __name__ == "__main__":
    main()
