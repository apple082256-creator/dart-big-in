from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MAPPING_DIR = DATA_DIR / "mapping"
DB_DIR = PROJECT_ROOT / "db" / "financial"
FINANCIAL_DB_PATH = DB_DIR / "financial.db"
FINANCIAL_CAPEX_DB_PATH = DB_DIR / "financial_capex_v2.db"
FINANCIAL_FINAL_DB_PATH = DB_DIR / "financial_final.db"
PRE_PROMOTION_FINANCIAL_DB_PATH = PROJECT_ROOT / "db" / "archive" / "financial_before_capex_fix.db"

DEFAULT_COMPANIES = ("삼성전자", "SK하이닉스", "한미반도체", "주성엔지니어링", "원익IPS")
OPERATIONAL_COMPANIES = DEFAULT_COMPANIES + (
    "셀트리온", "삼성바이오로직스", "알테오젠", "HLB", "유한양행",
    "현대제철", "고려아연", "POSCO홀딩스", "풍산", "에이치브이엠",
    "기아", "현대자동차", "현대모비스", "한국타이어앤테크놀로지", "HL만도",
    "하나금융지주", "KB금융", "신한지주", "우리금융지주", "카카오뱅크",
)

BANK_COMPANY_PROFILES = {
    "하나금융지주": {"stock_code": "086790", "corp_code": "00547583", "industry_profile": "BANK_HOLDING", "preferred_fs_div": "CFS"},
    "KB금융": {"stock_code": "105560", "corp_code": "00688996", "industry_profile": "BANK_HOLDING", "preferred_fs_div": "CFS"},
    "신한지주": {"stock_code": "055550", "corp_code": "00382199", "industry_profile": "BANK_HOLDING", "preferred_fs_div": "CFS"},
    "우리금융지주": {"stock_code": "316140", "corp_code": "01350869", "industry_profile": "BANK_HOLDING", "preferred_fs_div": "CFS"},
    "카카오뱅크": {"stock_code": "323410", "corp_code": "01133217", "industry_profile": "INTERNET_BANK", "preferred_fs_div": "OFS"},
}
DEFAULT_YEARS = (2023, 2024, 2025)

REPORTS = {
    "Q1": ("11013", "1분기보고서"),
    "H1": ("11012", "반기보고서"),
    "Q3": ("11014", "3분기보고서"),
    "FY": ("11011", "사업보고서"),
}


@dataclass(frozen=True)
class Settings:
    api_key: str
    companies: tuple[str, ...] = DEFAULT_COMPANIES
    years: tuple[int, ...] = DEFAULT_YEARS
    fs_div: str = "CFS"


def load_settings(
    companies: tuple[str, ...] = DEFAULT_COMPANIES,
    years: tuple[int, ...] = DEFAULT_YEARS,
    fs_div: str = "CFS",
) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = (os.getenv("DART_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DART_API_KEY가 없습니다. 프로젝트 .env 파일을 확인하세요.")
    if fs_div not in {"CFS", "OFS"}:
        raise ValueError("fs_div는 CFS 또는 OFS여야 합니다.")
    return Settings(api_key=api_key, companies=companies, years=years, fs_div=fs_div)


def ensure_directories() -> None:
    for path in (
        RAW_DIR / "corp_codes",
        RAW_DIR / "financial",
        RAW_DIR / "filings" / "zip",
        RAW_DIR / "filings" / "xml",
        RAW_DIR / "filings" / "search",
        PROCESSED_DIR / "financial",
        PROCESSED_DIR / "filings" / "parsed",
        PROCESSED_DIR / "filings" / "chunks",
        MAPPING_DIR,
        DB_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
