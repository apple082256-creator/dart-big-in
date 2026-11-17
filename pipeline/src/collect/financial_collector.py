from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests

from src.config import RAW_DIR, REPORTS


class DartApiError(RuntimeError):
    pass


class DartClient:
    base_url = "https://opendart.fss.or.kr/api"

    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict[str, str]) -> requests.Response:
        response = self.session.get(
            f"{self.base_url}/{endpoint}",
            params={"crtfc_key": self.api_key, **params},
            timeout=self.timeout,
        )
        if not response.ok:
            raise DartApiError(f"OpenDART {endpoint} HTTP 오류: {response.status_code}")
        return response

    def get_corp_codes(self, refresh: bool = False) -> pd.DataFrame:
        raw_dir = RAW_DIR / "corp_codes"
        zip_path = raw_dir / "corpCode.zip"
        xml_path = raw_dir / "CORPCODE.xml"
        if refresh or not xml_path.exists():
            content = self._get("corpCode.xml", {}).content
            zip_path.write_bytes(content)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                xml_path.write_bytes(archive.read("CORPCODE.xml"))

        root = ElementTree.parse(xml_path).getroot()
        rows = [{child.tag: child.text for child in item} for item in root.findall("list")]
        frame = pd.DataFrame(rows)
        frame["corp_name"] = frame["corp_name"].str.strip()
        return frame

    def resolve_companies(self, names: tuple[str, ...]) -> pd.DataFrame:
        codes = self.get_corp_codes()
        candidates = codes[codes["corp_name"].isin(names)].copy()
        selected_rows: list[pd.Series] = []
        ambiguous: list[str] = []
        for name in names:
            matches = candidates[candidates["corp_name"].eq(name)]
            listed = matches[matches["stock_code"].fillna("").str.strip().ne("")]
            if len(listed) == 1:
                selected_rows.append(listed.iloc[0])
            elif len(matches) == 1:
                selected_rows.append(matches.iloc[0])
            elif len(listed) > 1 or len(matches) > 1:
                ambiguous.append(name)
        selected = pd.DataFrame(selected_rows, columns=codes.columns)
        missing = sorted(set(names) - set(selected["corp_name"]))
        if ambiguous:
            raise DartApiError(
                "DART 동명 법인을 하나로 확정할 수 없습니다: " + ", ".join(sorted(ambiguous))
            )
        if missing:
            raise DartApiError(f"DART 기업코드를 찾지 못했습니다: {', '.join(missing)}")
        order = {name: index for index, name in enumerate(names)}
        return selected.sort_values("corp_name", key=lambda s: s.map(order)).reset_index(drop=True)

    def resolve_companies_by_stock_code(self, stock_codes: tuple[str, ...]) -> pd.DataFrame:
        """Resolve listed companies by the exact six-digit stock code.

        This path is intended for verified constituent imports where a company
        name can be shared by a historical or similarly named legal entity.
        """
        normalized = tuple(str(code).strip().zfill(6) for code in stock_codes)
        if len(normalized) != len(set(normalized)):
            raise DartApiError("종목코드가 중복됩니다.")
        codes = self.get_corp_codes()
        listed = codes[codes["stock_code"].fillna("").str.strip().isin(normalized)].copy()
        counts = listed.groupby(listed["stock_code"].str.strip()).size()
        ambiguous = sorted(code for code, count in counts.items() if count != 1)
        found = set(listed["stock_code"].fillna("").str.strip())
        missing = sorted(set(normalized) - found)
        if ambiguous:
            raise DartApiError("종목코드가 복수 법인과 일치합니다: " + ", ".join(ambiguous))
        if missing:
            raise DartApiError("DART 종목코드를 찾지 못했습니다: " + ", ".join(missing))
        order = {code: index for index, code in enumerate(normalized)}
        return listed.sort_values(
            "stock_code", key=lambda series: series.str.strip().map(order)
        ).reset_index(drop=True)

    def get_financial_accounts(
        self,
        corp_code: str,
        company_name: str,
        year: int,
        quarter: str,
        fs_div: str = "CFS",
        refresh: bool = False,
    ) -> list[dict]:
        report_code, report_name = REPORTS[quarter]
        output_dir = RAW_DIR / "financial" / corp_code / str(year)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{quarter}_{fs_div}.json"

        if output_path.exists() and not refresh:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        else:
            response = self._get(
                "fnlttSinglAcntAll.json",
                {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": report_code,
                    "fs_div": fs_div,
                },
            )
            payload = response.json()
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        status = payload.get("status")
        if status == "013":  # 조회된 데이터 없음
            return []
        if status != "000":
            raise DartApiError(
                f"{company_name} {year} {report_name}: {payload.get('message', 'DART API 오류')} ({status})"
            )
        return payload.get("list", [])


def collect_financials(
    client: DartClient,
    companies: pd.DataFrame,
    years: tuple[int, ...],
    fs_div: str = "CFS",
    refresh: bool = False,
) -> pd.DataFrame:
    rows: list[dict] = []
    for company in companies.to_dict("records"):
        for year in years:
            for quarter, (report_code, report_name) in REPORTS.items():
                accounts = client.get_financial_accounts(
                    company["corp_code"], company["corp_name"], year, quarter, fs_div, refresh
                )
                for account in accounts:
                    rows.append(
                        {
                            **account,
                            "company_name": company["corp_name"],
                            "stock_code": company.get("stock_code"),
                            "year": year,
                            "quarter": quarter,
                            "report_code": report_code,
                            "report_name": report_name,
                            "fs_div_requested": fs_div,
                        }
                    )
    return pd.DataFrame(rows)
