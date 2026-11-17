from __future__ import annotations

import calendar
import json
import re
from datetime import date

import pandas as pd

from src.db import residual_corrections, sign_convention_corrections
from src.db.verified_quarter_overrides import FACT_QUARTER_INDEX, evidence_quarter_index, sign_alignment_entries
from src.preprocess.financial_cleaner import parse_amount


DURATION_STATEMENTS = {"IS", "CIS"}
CUMULATIVE_STATEMENTS = {"CF", "SCE"}
QUARTER_NUMBER = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
# Quarter differences whose two cumulative inputs were shown to carry the same
# presentation sign although the API returned opposite signs (next-year
# comparatives or the filings' own statements).  Only these exact inputs are
# sign-aligned, and the regenerated row carries the same correction record as
# the stored one; every other opposite-sign pair keeps the arithmetic
# difference and is marked for review, because a real reversal (tax benefit,
# provision write-back, gain turning into loss) looks identical without
# external evidence.
VERIFIED_SIGN_ALIGNMENTS: dict[tuple, dict] = {
    **{(item.corp_code, item.year, "FY", item.account_key, item.current_cumulative, item.previous_cumulative):
       sign_convention_corrections.correction_record(item)
       for item in sign_convention_corrections.FACT_CORRECTIONS},
    **{(item.corp_code, item.year, item.report_type, item.account_key, item.current_cumulative,
        item.previous_cumulative): residual_corrections.correction_record(item)
       for item in residual_corrections.FACT_CORRECTIONS},
    **{(e["corp_code"], e["year"], e["report_type"], e["account_key"], e["current_value"], e["previous_value"]):
       {"id": "increment-evidence", "previous_value": e["previous_value_stored"],
        "evidence_rcept_nos": e["evidence_rcept_nos"], "presented": e["presented"]}
       for e in sign_alignment_entries()},
}


CF_BALANCE_IDS = frozenset({"dart_CashAndCashEquivalentsAtEndOfPeriodCf",
                            "dart_CashAndCashEquivalentsAtBeginningOfPeriodCf", "ifrs-full_CashAndCashEquivalents"})


def is_cf_balance_line(account_id: object, name: object) -> bool:
    """Opening/closing cash in the cash flow statement: a balance, so no quarter is derived."""
    compact = "".join(str(name or "").split())
    return account_id in CF_BALANCE_IDS or ("현금" in compact and any(
        k in compact for k in ("기초", "기말", "분기말", "반기말", "당기말", "기간말")))


def _quarter_difference(
    corp_code: str, year: int, report_type: str, account_key: str,
    current: int, previous: int,
) -> tuple[int, str | None]:
    """Return the quarter value and a sign note when the cumulative inputs conflict."""
    if not current or not previous or (current > 0) == (previous > 0):
        return current - previous, None
    key = (str(corp_code), int(year), report_type, str(account_key), current, previous)
    if key in VERIFIED_SIGN_ALIGNMENTS:
        # Restate the earlier cumulative in the current report's sign convention.
        return current + previous, "sign_aligned"
    return current - previous, "sign_conflict_review"


def _period_dates(year: int, quarter: str, period_type: str) -> tuple[str | None, str]:
    ends = {"Q1": (3, 31), "H1": (6, 30), "Q3": (9, 30), "FY": (12, 31)}
    month, day = ends[quarter]
    period_end = date(year, month, day).isoformat()
    if period_type == "point_in_time":
        return None, period_end
    if period_type in {"cumulative", "annual"}:
        return date(year, 1, 1).isoformat(), period_end
    starts = {"Q1": (1, 1), "H1": (4, 1), "Q3": (7, 1), "FY": (10, 1)}
    start_month, start_day = starts[quarter]
    return date(year, start_month, start_day).isoformat(), period_end


def _base_record(row: pd.Series, period_type: str, value_type: str) -> dict:
    year = int(row["year"])
    period_start, period_end = _period_dates(year, row["quarter"], period_type)
    return {
        "corp_name": row["company_name"],
        "corp_code": row["corp_code"],
        "stock_code": row.get("stock_code"),
        "year": year,
        "report_type": row["quarter"],
        "report_code": row["report_code"],
        "report_name": row["report_name"],
        "sj_div": row["sj_div"],
        "sj_nm": row.get("sj_nm"),
        "account_key": row["account_key"],
        "account_id": row["account_id"],
        "original_account_nm": row["account_nm"],
        "standard_account_nm": row["standard_account_name"],
        "period_type": period_type,
        "value_type": value_type,
        "period_start": period_start,
        "period_end": period_end,
        "currency": row.get("currency"),
        "fs_div": row["fs_div"],
        "ord": row.get("ord"),
        "rcept_no": row.get("rcept_no"),
    }


def _raw_record(row: pd.Series, field: str, period_type: str, value_type: str) -> dict | None:
    raw_value = row.get(field)
    normalized = parse_amount(raw_value)
    if normalized is None:
        return None
    return {
        **_base_record(row, period_type, value_type),
        "source_value_field": field,
        "raw_value": str(raw_value),
        "normalized_value": normalized,
        "calculated_value": None,
        "calculation_method": None,
        "source_values": json.dumps({field: str(raw_value)}, ensure_ascii=False),
        "is_calculated": False,
    }


def normalize_period_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Expand one DART account row into auditable, period-aware value records."""
    records: list[dict] = []
    for _, row in frame.iterrows():
        sj_div = row["sj_div"]
        report_type = row["quarter"]
        candidates: list[dict | None]
        if sj_div == "BS":
            candidates = [_raw_record(row, "thstrm_amount", "point_in_time", "point_in_time")]
        elif sj_div in DURATION_STATEMENTS:
            if report_type == "FY":
                candidates = [_raw_record(row, "thstrm_amount", "annual", "annual")]
            else:
                candidates = [
                    _raw_record(row, "thstrm_amount", "quarterly", "quarterly"),
                    _raw_record(row, "thstrm_add_amount", "cumulative", "cumulative"),
                ]
        elif sj_div in CUMULATIVE_STATEMENTS:
            period_type = "annual" if report_type == "FY" else "cumulative"
            candidates = [_raw_record(row, "thstrm_amount", period_type, period_type)]
        else:
            period_type = "annual" if report_type == "FY" else "cumulative"
            candidates = [_raw_record(row, "thstrm_amount", period_type, period_type)]
        records.extend(candidate for candidate in candidates if candidate is not None)

    periodized = pd.DataFrame(records)
    if periodized.empty:
        return periodized
    return _add_calculated_quarterly_values(periodized)


PREVIOUS_REPORT = {"Q1": None, "H1": "Q1", "Q3": "H1", "FY": "Q3"}
OFFICIAL_ACCOUNT_PREFIXES = ("ifrs-full_", "ifrs_", "dart_")


_LINE_NUMBER = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?|[IVXL]+\.|\d+[.)]|\(\d+\)|[가-하][.)])")
_FORMULA = re.compile(r"\([^()]*[+\-][^()]*\)")
_PERIOD_WORD = re.compile(r"(연결)?(분기|반기|당기)")


def _line_name(name: object) -> str:
    """Line name without numbering, spacing, '의', formulas and period words."""
    value = re.sub(r"\s+", "", str(name or ""))
    for _ in range(2):
        value = _LINE_NUMBER.sub("", value)
    value = _FORMULA.sub("", value)
    value = _PERIOD_WORD.sub("당기", value)
    return value.replace("의", "")


def _plain_ord(value: object) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _is_official_account(account_key: object) -> bool:
    return str(account_key or "").startswith(OFFICIAL_ACCOUNT_PREFIXES)


def _quarter_record(current, report_type: str, value: int, method: str, inputs: str,
                    source_values: dict) -> dict:
    record = current._asdict()
    period_start, period_end = _period_dates(int(current.year), report_type, "quarterly")
    record.update(
        {
            "period_type": "quarterly",
            "value_type": "quarterly",
            "period_start": period_start,
            "period_end": period_end,
            "source_value_field": "calculated_from_cumulative",
            "raw_value": current.raw_value,
            "normalized_value": None,
            "calculated_value": value,
            "calculation_method": f"{method} [{inputs}]",
            "source_values": json.dumps(source_values, ensure_ascii=False),
            "is_calculated": True,
        }
    )
    return record


def _add_calculated_quarterly_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive missing quarterly flow values while retaining source cumulative records.

    Pass 1 pairs cumulative rows of the same line (same ``ord``), as before.
    Pass 2 handles lines whose display order changed between two reports: the
    pair is matched by official account id when each report holds exactly one
    cumulative row for that id and the two values do not disagree in sign.
    Anything else is listed in ``frame.attrs['quarter_match_review']``.
    """
    calculated: list[dict] = []
    review: list[dict] = []
    unmatched: list[tuple] = []
    group_columns = ["corp_code", "year", "fs_div", "sj_div", "account_key", "ord"]
    for _, group in frame.groupby(group_columns, dropna=False):
        sj_div = group["sj_div"].iloc[0]
        if sj_div not in DURATION_STATEMENTS | {"CF"}:
            continue
        first = group.iloc[0]
        if sj_div == "CF" and is_cf_balance_line(first.get("account_id"), first.get("original_account_nm")):
            continue
        raw_quarters = set(group.loc[group["value_type"].eq("quarterly"), "report_type"])
        cumulative = {
            row.report_type: row
            for row in group[group["value_type"].isin(["cumulative", "annual"])].itertuples()
        }
        for report_type in ("Q1", "H1", "Q3", "FY"):
            if report_type in raw_quarters or report_type not in cumulative:
                continue
            current = cumulative[report_type]
            previous_type = PREVIOUS_REPORT[report_type]
            if previous_type is None:
                calculated.append(_quarter_record(
                    current, report_type, current.normalized_value, "Q1 cumulative",
                    f"{report_type}:{current.normalized_value}", {report_type: current.normalized_value},
                ))
                continue
            previous = cumulative.get(previous_type)
            if previous is None:
                unmatched.append((current, report_type, previous_type))
                continue
            value, sign_note = _quarter_difference(
                current.corp_code, current.year, report_type, current.account_key,
                int(current.normalized_value), int(previous.normalized_value),
            )
            source_values = {
                report_type: current.normalized_value,
                previous_type: previous.normalized_value,
            }
            method = f"{report_type} cumulative - {previous_type} cumulative"
            if sign_note:
                source_values["sign_note"] = sign_note
                method += f" ({sign_note})"
            if sign_note == "sign_aligned":
                source_values["correction"] = VERIFIED_SIGN_ALIGNMENTS[(
                    str(current.corp_code), int(current.year), report_type, str(current.account_key),
                    int(current.normalized_value), int(previous.normalized_value))]
            inputs = (
                f"{report_type}:{current.normalized_value};"
                f"{previous_type}:{previous.normalized_value}"
            )
            calculated.append(_quarter_record(current, report_type, value, method, inputs, source_values))

    if unmatched:
        by_account = frame.groupby(["corp_code", "year", "fs_div", "sj_div", "account_key"], dropna=False)
        groups = {key: group for key, group in by_account}
        for current, report_type, previous_type in unmatched:
            key = (current.corp_code, current.year, current.fs_div, current.sj_div, current.account_key)
            group = groups[key]
            item = {"corp_code": current.corp_code, "year": int(current.year), "report_type": report_type,
                    "fs_div": current.fs_div, "sj_div": current.sj_div, "account_key": current.account_key,
                    "account_name": current.original_account_nm}
            cumulative_rows = group[group["value_type"].isin(["cumulative", "annual"])]
            current_rows = cumulative_rows[cumulative_rows["report_type"].eq(report_type)]
            previous_rows = cumulative_rows[cumulative_rows["report_type"].eq(previous_type)]
            if previous_rows.empty:
                continue  # no earlier cumulative report: nothing to derive
            if (group["value_type"].eq("quarterly") & group["report_type"].eq(report_type)).any():
                continue  # a reported quarter exists under another line order
            if len(current_rows) == 1 and len(previous_rows) == 1:
                previous = next(previous_rows.itertuples())
                now, before = int(current.normalized_value), int(previous.normalized_value)
                evidence = evidence_quarter_index().get((
                    str(current.corp_code), int(current.year), report_type, current.fs_div, current.sj_div,
                    str(current.account_key), now, before))
                if evidence is not None:
                    # Verified against the filed statements (and next-year comparatives
                    # where they exist); only these exact inputs are derived.
                    source_values = {
                        report_type: now,
                        previous_type: before,
                        "match": "verified_evidence",
                        "ord": {report_type: _plain_ord(current.ord), previous_type: _plain_ord(previous.ord)},
                        "verified_evidence": {k: evidence[k] for k in ("category", "basis", "rcept_nos")},
                        **({"sign_note": "sign_aligned"} if evidence["sign_aligned"] else {}),
                    }
                    note = "verified evidence; sign_aligned" if evidence["sign_aligned"] else "verified evidence"
                    calculated.append(_quarter_record(
                        current, report_type, now + before if evidence["sign_aligned"] else now - before,
                        f"{report_type} cumulative - {previous_type} cumulative ({note})",
                        f"{report_type}:{now};{previous_type}:{before}", source_values,
                    ))
                    continue
            if not _is_official_account(current.account_key):
                review.append({**item, "reason": "account_key_not_official_id"})
                continue
            if len(current_rows) != 1 or len(previous_rows) != 1:
                review.append({**item, "reason": "account_id_not_unique_in_report",
                               "rows": [len(current_rows), len(previous_rows)]})
                continue
            previous = next(previous_rows.itertuples())
            override = FACT_QUARTER_INDEX.get((
                current.corp_code, int(current.year), report_type, current.fs_div, current.sj_div,
                current.account_key, int(current.normalized_value), int(previous.normalized_value),
            ))
            if override is not None:
                now, before = int(current.normalized_value), int(previous.normalized_value)
                value = now + before if override.align_sign else now - before
                source_values = {
                    report_type: now,
                    previous_type: before,
                    "match": "account_id",
                    "ord": {report_type: _plain_ord(current.ord), previous_type: _plain_ord(previous.ord)},
                    "verified_override": {"reason": override.reason, "evidence": override.evidence},
                    **({"sign_note": "sign_aligned"} if override.align_sign else {}),
                }
                note = "verified; sign_aligned" if override.align_sign else "verified"
                calculated.append(_quarter_record(
                    current, report_type, value,
                    f"{report_type} cumulative - {previous_type} cumulative (matched by account id; {note})",
                    f"{report_type}:{now};{previous_type}:{before}", source_values,
                ))
                continue
            if _line_name(current.original_account_nm) != _line_name(previous.original_account_nm):
                review.append({**item, "reason": "account_name_changed",
                               "previous_account_name": previous.original_account_nm})
                continue
            now, before = int(current.normalized_value), int(previous.normalized_value)
            if now and before and (now > 0) != (before > 0):
                review.append({**item, "reason": "sign_conflict", report_type: now, previous_type: before})
                continue
            source_values = {
                report_type: now,
                previous_type: before,
                "match": "account_id",
                "ord": {report_type: _plain_ord(current.ord), previous_type: _plain_ord(previous.ord)},
            }
            method = (f"{report_type} cumulative - {previous_type} cumulative "
                      f"(matched by account id; ord {_plain_ord(current.ord)}/{_plain_ord(previous.ord)})")
            calculated.append(_quarter_record(
                current, report_type, now - before, method,
                f"{report_type}:{now};{previous_type}:{before}", source_values,
            ))
    result = frame.reset_index(drop=True) if not calculated else pd.concat(
        [frame, pd.DataFrame(calculated)], ignore_index=True)
    result.attrs["quarter_match_review"] = review
    return result
