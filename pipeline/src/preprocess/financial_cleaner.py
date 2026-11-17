from __future__ import annotations

import re

import pandas as pd


AMOUNT_COLUMNS = ("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount")
NON_STANDARD_ACCOUNT_IDS = {"", "-표준계정코드 미사용-"}


def normalize_account_name(value: object) -> str:
    text = "" if pd.isna(value) else str(value).strip().lower()
    text = re.sub(r"\s+", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def parse_amount(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        number = int(text)
        return -number if negative else number
    except ValueError:
        return None


def clean_financials(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    cleaned = frame.copy()
    if "fs_div" not in cleaned and "fs_div_requested" in cleaned:
        cleaned["fs_div"] = cleaned["fs_div_requested"]
    cleaned["account_nm"] = cleaned["account_nm"].fillna("").str.strip()
    cleaned["account_id"] = cleaned["account_id"].fillna("").str.strip()
    cleaned["account_name_normalized"] = cleaned["account_nm"].map(normalize_account_name)
    valid_account_id = ~cleaned["account_id"].isin(NON_STANDARD_ACCOUNT_IDS)
    cleaned["account_key"] = cleaned["account_id"].where(
        valid_account_id, "name:" + cleaned["account_name_normalized"]
    )
    for column in AMOUNT_COLUMNS:
        if column in cleaned:
            cleaned[column + "_numeric"] = cleaned[column].map(parse_amount).astype("Int64")
    keys = ["corp_code", "year", "quarter", "fs_div", "sj_div", "account_key", "ord"]
    keys = [column for column in keys if column in cleaned]
    return cleaned.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
