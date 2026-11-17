from __future__ import annotations

import pandas as pd


def build_account_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "account_key", "account_id", "sj_div", "account_nm", "company_name",
        "corp_code", "year", "quarter", "report_name", "fs_div",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    available = [column for column in columns if column in frame]
    return (
        frame[available]
        .drop_duplicates()
        .sort_values(["sj_div", "account_key", "company_name", "year", "quarter"])
        .reset_index(drop=True)
    )


def build_mapping_draft(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "account_key", "account_id", "sj_div", "standard_account_name",
        "source_account_names", "company_count", "observation_count", "mapping_status",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)

    records: list[dict] = []
    for (account_key, sj_div), group in frame.groupby(["account_key", "sj_div"], dropna=False):
        names = sorted({str(value).strip() for value in group["account_nm"] if str(value).strip()})
        counts = group["account_nm"].value_counts()
        standard_name = str(counts.index[0]) if not counts.empty else ""
        account_ids = [
            value for value in group["account_id"].dropna().unique()
            if value and value != "-표준계정코드 미사용-"
        ]
        report_types = sorted(group["quarter"].dropna().astype(str).unique())
        fs_divs = sorted(group["fs_div"].dropna().astype(str).unique())
        review_required = len(names) > 1 or not account_ids
        records.append(
            {
                "account_key": account_key,
                "account_id": account_ids[0] if account_ids else "",
                "sj_div": sj_div,
                "standard_account_name": standard_name,
                "source_account_names": " | ".join(names),
                "company_count": group["corp_code"].nunique(),
                "observation_count": len(group),
                "mapping_status": "review_required" if review_required else "auto_account_id",
                "review_required": review_required,
                "name_variant_count": len(names),
                "report_types": " | ".join(report_types),
                "fs_divs": " | ".join(fs_divs),
            }
        )
    extended_columns = columns + ["review_required", "name_variant_count", "report_types", "fs_divs"]
    return pd.DataFrame(records, columns=extended_columns).sort_values(
        ["sj_div", "standard_account_name"]
    ).reset_index(drop=True)


def build_mapping_anomalies(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict] = []
    valid = frame[~frame["account_id"].isin(["", "-표준계정코드 미사용-"])].copy()
    for (account_id, sj_div, fs_div), group in valid.groupby(["account_id", "sj_div", "fs_div"]):
        names = sorted(group["account_nm"].dropna().astype(str).unique())
        if len(names) > 1:
            records.append({
                "issue_type": "same_id_multiple_names", "account_id": account_id,
                "account_nm": " | ".join(names), "sj_div": sj_div, "fs_div": fs_div,
                "report_types": " | ".join(sorted(group["quarter"].unique())),
                "corp_names": " | ".join(sorted(group["company_name"].unique())),
                "review_required": True,
            })
    for (_, sj_div, fs_div), group in valid.groupby(["account_name_normalized", "sj_div", "fs_div"]):
        ids = sorted(group["account_id"].unique())
        if len(ids) > 1:
            records.append({
                "issue_type": "same_name_multiple_ids", "account_id": " | ".join(ids),
                "account_nm": " | ".join(sorted(group["account_nm"].unique())),
                "sj_div": sj_div, "fs_div": fs_div,
                "report_types": " | ".join(sorted(group["quarter"].unique())),
                "corp_names": " | ".join(sorted(group["company_name"].unique())),
                "review_required": True,
            })
    return pd.DataFrame(records)


def apply_mapping(frame: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    selected = mapping[["account_key", "sj_div", "standard_account_name", "mapping_status"]]
    return frame.merge(selected, on=["account_key", "sj_div"], how="left", validate="many_to_one")
