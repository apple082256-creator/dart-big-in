from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

import pandas as pd
import requests

from src.collect.financial_collector import DartApiError
from src.config import RAW_DIR, REPORTS


class FilingClient:
    """OpenDART disclosure search and original-document client."""

    base_url = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str, timeout: int = 60) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()

    def search_filings(
        self,
        corp_code: str,
        begin_date: str,
        end_date: str,
        page_no: int = 1,
        page_count: int = 100,
        last_reprt_at: str = "Y",
    ) -> dict:
        response = self.session.get(
            f"{self.base_url}/list.json",
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": begin_date,
                "end_de": end_date,
                "last_reprt_at": last_reprt_at,
                "pblntf_ty": "A",
                "page_no": str(page_no),
                "page_count": str(page_count),
            },
            timeout=self.timeout,
        )
        if not response.ok:
            raise DartApiError(f"공시검색 HTTP 오류: {response.status_code}")
        payload = response.json()
        if payload.get("status") not in {"000", "013"}:
            raise DartApiError(
                f"공시검색 실패: {payload.get('message', 'DART API 오류')} ({payload.get('status')})"
            )
        output = RAW_DIR / "filings" / "search" / corp_code
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{begin_date}_{end_date}_{last_reprt_at}_p{page_no}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return payload

    def download_original_document(
        self,
        filing: dict,
        refresh: bool = False,
    ) -> list[Path]:
        """Download document.xml ZIP and preserve every XML file without modification."""
        rcept_no = str(filing["rcept_no"])
        corp_code = str(filing["corp_code"])
        year = str(filing["year"])
        report_type = str(filing["report_type"])
        zip_path = RAW_DIR / "filings" / "zip" / corp_code / year / report_type / f"{rcept_no}.zip"
        xml_dir = RAW_DIR / "filings" / "xml" / corp_code / year / report_type / rcept_no
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        xml_dir.mkdir(parents=True, exist_ok=True)

        if refresh or not zip_path.exists():
            response = self.session.get(
                f"{self.base_url}/document.xml",
                params={"crtfc_key": self.api_key, "rcept_no": rcept_no},
                timeout=self.timeout,
            )
            if not response.ok:
                raise DartApiError(f"원문 다운로드 HTTP 오류 {rcept_no}: {response.status_code}")
            try:
                with zipfile.ZipFile(io.BytesIO(response.content)):
                    pass
            except zipfile.BadZipFile as exc:
                message = _extract_error_message(response.content)
                raise DartApiError(f"원문 다운로드 실패 {rcept_no}: {message}") from exc
            zip_path.write_bytes(response.content)

        extracted: list[Path] = []
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                member_path = PurePosixPath(member.filename)
                if member.is_dir() or member_path.suffix.lower() not in {".xml", ".xhtml", ".html", ".htm"}:
                    continue
                safe_name = "__".join(part for part in member_path.parts if part not in {".", ".."})
                destination = xml_dir / safe_name
                if refresh or not destination.exists():
                    destination.write_bytes(archive.read(member))
                extracted.append(destination)
        if not extracted:
            raise DartApiError(f"원문 ZIP에 파싱 가능한 문서가 없습니다: {rcept_no}")
        return extracted


def build_filing_index_from_financial_raw(
    companies: pd.DataFrame,
    years: tuple[int, ...],
) -> list[dict]:
    """Use the exact receipt numbers returned with financial statements."""
    company_by_code = companies.set_index("corp_code")["corp_name"].to_dict()
    filings: dict[str, dict] = {}
    for path in RAW_DIR.glob("financial/*/*/*.json"):
        corp_code, year_text, filename = path.parts[-3:]
        year = int(year_text)
        if corp_code not in company_by_code or year not in years:
            continue
        report_type = filename.split("_", 1)[0]
        if report_type not in REPORTS:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("list", [])
        if not rows:
            continue
        rcept_no = str(rows[0].get("rcept_no", "")).strip()
        if not rcept_no:
            continue
        filings[rcept_no] = {
            "corp_name": company_by_code[corp_code],
            "corp_code": corp_code,
            "report_name": REPORTS[report_type][1],
            "report_type": report_type,
            "year": year,
            "rcept_no": rcept_no,
        }
    return sorted(filings.values(), key=lambda item: (item["corp_code"], item["year"], item["report_type"]))


def build_filing_index_with_search(
    client: FilingClient,
    companies: pd.DataFrame,
    years: tuple[int, ...],
) -> list[dict]:
    """Use financial receipt numbers first and fill API-missing slots from DART search.

    Financial institutions can have a published regular report while
    ``fnlttSinglAcntAll`` returns status 013.  The filing must still enter the
    XML/vector pipeline, so those slots are resolved independently here.
    """
    filings = {
        (row["corp_code"], int(row["year"]), row["report_type"]): row
        for row in build_filing_index_from_financial_raw(companies, years)
    }
    month_to_type = {"03": "Q1", "06": "H1", "09": "Q3", "12": "FY"}
    begin_date = f"{min(years)}0101"
    end_date = f"{max(years) + 1}0430"
    report_pattern = re.compile(
        r"^(?:\[[^]]+\])*\s*(?:분기|반기|사업)보고서\s*\((20\d{2})\.(03|06|09|12)\)"
    )
    for company in companies.to_dict("records"):
        payload = client.search_filings(
            company["corp_code"], begin_date, end_date, last_reprt_at="N"
        )
        candidates_by_slot: dict[tuple[str, int, str], list[str]] = {}
        for disclosure in payload.get("list", []):
            match = report_pattern.match(str(disclosure.get("report_nm") or "").strip())
            if not match:
                continue
            year = int(match.group(1))
            report_type = month_to_type[match.group(2)]
            if year not in years:
                continue
            key = (company["corp_code"], year, report_type)
            candidates_by_slot.setdefault(key, []).append(str(disclosure["rcept_no"]))
            filings.setdefault(key, {
                "corp_name": company["corp_name"],
                "corp_code": company["corp_code"],
                "report_name": REPORTS[report_type][1],
                "dart_report_name": disclosure.get("report_nm"),
                "report_type": report_type,
                "year": year,
                "rcept_no": str(disclosure["rcept_no"]),
                "rcept_dt": disclosure.get("rcept_dt"),
                "is_amended": str(disclosure.get("report_nm") or "").startswith("["),
                "receipt_source": "dart_list_fallback",
            })
        for key, candidates in candidates_by_slot.items():
            if key in filings:
                filings[key]["fallback_rcept_nos"] = list(dict.fromkeys(candidates))
    return sorted(filings.values(), key=lambda item: (
        item["corp_code"], item["year"], tuple(REPORTS).index(item["report_type"]),
    ))


def _extract_error_message(content: bytes) -> str:
    text = content.decode("utf-8", errors="replace")
    status = re.search(r"<status>(.*?)</status>", text, re.I | re.S)
    message = re.search(r"<message>(.*?)</message>", text, re.I | re.S)
    return " / ".join(
        value.group(1).strip() for value in (status, message) if value is not None
    ) or "ZIP이 아닌 응답"
