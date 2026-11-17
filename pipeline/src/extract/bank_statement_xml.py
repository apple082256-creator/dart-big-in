"""Deterministic extraction of bank statement lines from DART document XML.

DART regular-report XML (dart3.xsd) is an HTML-like container, not an XBRL
instance, and is often not well-formed (unescaped ``<정정전>`` text), so it is
parsed with the lenient HTML parser.  Statements are located by the section
codes DART assigns (``D-0-3-2-0`` consolidated, ``D-0-3-4-0`` separate), and
only the current-period column is read.  Nothing is inferred: a line, unit or
period that cannot be confirmed from the document is reported, not guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

EXTRACTOR_VERSION = "bank-statement-xml-v1"
SECTION_BY_FS_DIV = {"CFS": "D-0-3-2-0", "OFS": "D-0-3-4-0"}
PERIOD_END = {"Q1": "3월31일", "H1": "6월30일", "Q3": "9월30일", "FY": "12월31일"}
UNIT_SCALE = {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}

_TITLE = re.compile(r'<TITLE\b[^>]*ATOC="Y"[^>]*>', re.S)
_ASSOC = re.compile(r'AASSOCNOTE="([^"]*)"')
_LINE_PREFIX = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?|[IVXL]+\.|\d+[.)]|\(\d+\)|[가-하][.)])")
_UNIT = re.compile(r"단위[:：]?\(?(원|천원|백만원|억원)")
_CELL_TAGS = {"td", "th", "te", "tu"}

# Exact (normalized) statement line names per metric.  The first matching
# line in statement order is the total line; sub-lines repeat the name below it.
BS_LINES = {
    "TOTAL_ASSETS": {"자산총계"},
    "TOTAL_LIABILITIES": {"부채총계"},
    "TOTAL_EQUITY": {"자본총계"},
    "LOANS_AT_AMORTISED_COST": {"상각후원가측정대출채권", "대출채권",
                                "상각후원가측정대출채권및기타금융자산"},
    "CUSTOMER_DEPOSITS": {"예수부채"},
}
CIS_LINES = {
    "NET_INCOME": {"당기순이익", "반기순이익", "분기순이익",
                   "연결당기순이익", "연결반기순이익", "연결분기순이익"},
    "INTEREST_INCOME": {"이자수익"},
    "INTEREST_EXPENSE": {"이자비용"},
    "NET_INTEREST_INCOME": {"순이자손익", "순이자이익"},
    "FEE_COMMISSION_INCOME": {"수수료수익"},
    "FEE_COMMISSION_EXPENSE": {"수수료비용"},
    "NET_FEE_COMMISSION_INCOME": {"순수수료손익", "순수수료이익"},
    "CREDIT_LOSS_EXPENSE": {"신용손실충당금전입액", "금융자산신용손실충당금전입액",
                            "신용손실에대한손상차손"},
}
COMBINED_LOAN_LINES = {"상각후원가측정대출채권및기타금융자산"}
# Lines read only to cross-check other values; they are not stored as metrics.
AUX_CIS_LINES = {
    "PRETAX_INCOME": {"법인세비용차감전순이익", "법인세비용차감전당기순이익", "법인세비용차감전분기순이익",
                      "법인세비용차감전반기순이익", "법인세차감전순이익", "연결법인세비용차감전순이익"},
    "INCOME_TAX": {"법인세비용", "법인세비용(수익)", "법인세비용(이익)", "법인세수익(비용)"},
}
SUMMARY_SECTION = "D-0-3-1-0"


@dataclass(frozen=True)
class StatementLine:
    metric_code: str
    statement: str            # BS / CIS
    presented_text: str
    presented_value: int      # in the document unit, presented sign
    unit: str
    scale: int
    account_name: str
    section_code: str
    table_line: int           # 1-based line of the data table in the XML
    row_index: int            # row within the data table (header row = 0)
    column_header: str
    layout: str               # single / paired (subtotal-detail columns)

    @property
    def krw(self) -> int:
        return self.presented_value * self.scale


@dataclass
class StatementExtraction:
    path: Path
    fs_div: str
    report_type: str
    lines: dict[str, StatementLine] = field(default_factory=dict)
    aux: dict[str, StatementLine] = field(default_factory=dict)
    summary: dict[str, dict] = field(default_factory=dict)
    statements: dict[str, dict] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


class _Blocks(HTMLParser):
    """Paragraph texts and tables (rows of cells) in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, object, int]] = []
        self._table: list[list[dict]] | None = None
        self._row: list[dict] | None = None
        self._cell: dict | None = None
        self._para: list[str] | None = None
        self._table_line = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table, self._table_line = [], self.getpos()[0]
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in _CELL_TAGS and self._row is not None:
            self._cell = {"tag": tag, "text": []}
        elif tag == "p" and self._table is None:
            self._para = []

    def handle_endtag(self, tag):
        if tag in _CELL_TAGS and self._cell is not None and self._row is not None:
            self._cell["text"] = " ".join("".join(self._cell["text"]).split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.blocks.append(("table", self._table, self._table_line))
            self._table = None
        elif tag == "p" and self._para is not None:
            text = " ".join("".join(self._para).split())
            if text:
                self.blocks.append(("p", text, self.getpos()[0]))
            self._para = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell["text"].append(data)
        elif self._para is not None:
            self._para.append(data)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def line_name(text: str) -> str:
    """Statement line name without numbering such as 'Ⅰ.', '(1)', '2.'."""
    value = _compact(text)
    for _ in range(2):
        value = _LINE_PREFIX.sub("", value)
    return value


def parse_amount(text: str) -> int | None:
    """'1,234' / '(1,234)' / '-1,234' -> int; blank or '-' -> None (not zero)."""
    value = _compact(text).replace(",", "")
    if value in {"", "-", "–"}:
        return None
    negative = (value.startswith("(") and value.endswith(")")) or value.startswith(("-", "△"))
    digits = value.strip("()-△")
    if not digits.isdigit():
        return None
    return -int(digits) if negative else int(digits)


def _sections(raw: str, coded_only: bool = False) -> dict[str, tuple[int, int]]:
    """Section spans by AASSOCNOTE code.

    With ``coded_only`` a section ends at the next title that carries a section
    code, so uncoded sub-titles inside long notes do not cut the section short.
    """
    titles = list(_TITLE.finditer(raw))
    if coded_only:
        titles = [t for t in titles if (m := _ASSOC.search(t.group(0))) and m.group(1)]
    found = {}
    for index, match in enumerate(titles):
        assoc = _ASSOC.search(match.group(0))
        end = titles[index + 1].start() if index + 1 < len(titles) else len(raw)
        if assoc and assoc.group(1):
            found[assoc.group(1)] = (match.start(), end)
    return found


def _blocks(raw: str, start: int, end: int) -> list[tuple[str, object, int]]:
    parser = _Blocks()
    parser.feed(raw[start:end])
    parser.close()
    offset = raw.count("\n", 0, start)
    return [(kind, value, line + offset) for kind, value, line in parser.blocks]


def _locate_statements(blocks: list) -> dict[str, dict]:
    """Find the BS and CIS data tables and the header text/unit preceding them."""
    found: dict[str, dict] = {}
    pending = None
    for kind, value, line in blocks:
        is_header_table = kind == "table" and value and value[0] and value[0][0]["tag"] != "th" \
            and _compact(value[0][0]["text"]) not in {"과목", "구분"}
        if kind == "p" or is_header_table:
            text = _compact(value if kind == "p" else " ".join(c["text"] for r in value for c in r))
            if ("재무상태표" in text or "대차대조표" in text) and "BS" not in found:
                pending = {"statement": "BS", "header": ""}
            elif "손익계산서" in text and "자본변동" not in text and "CIS" not in found:
                pending = {"statement": "CIS", "header": ""}
            if pending is not None:
                pending["header"] += text
            continue
        if kind == "table" and pending and value and value[0] \
                and _compact(value[0][0]["text"]) in {"과목", "구분"}:
            unit = _UNIT.search(pending["header"])
            found[pending["statement"]] = {
                "table": value, "line": line, "header": pending["header"],
                "unit": unit.group(1) if unit else None,
            }
            pending = None
    return found


def _current_column(table: list[list[dict]]) -> tuple[str, list[dict], list[str]]:
    headers = [c["text"] for c in table[0]][1:]
    rows, issues = [], []
    for index, row in enumerate(table[1:], start=1):
        if not row or row[0]["tag"] == "th":
            continue
        cells = [c["text"] for c in row[1:]]
        if len(cells) == len(headers):
            text, layout = cells[0], "single"
        elif len(cells) == 2 * len(headers):
            filled = [c for c in cells[:2] if _compact(c)]
            if len(filled) > 1:
                issues.append(f"row {index}: both current sub-columns filled ({row[0]['text']})")
                continue
            text, layout = (filled[0] if filled else ""), "paired"
        else:
            continue
        rows.append({"row": index, "name": line_name(row[0]["text"]),
                     "account": row[0]["text"], "text": text, "layout": layout})
    return (headers[0] if headers else ""), rows, issues


def extract_statement_lines(path: Path, fs_div: str, year: int, report_type: str) -> StatementExtraction:
    raw = path.read_text(encoding="utf-8")
    result = StatementExtraction(path=path, fs_div=fs_div, report_type=report_type)
    section_code = SECTION_BY_FS_DIV[fs_div]
    section = _sections(raw).get(section_code)
    if section is None:
        result.issues.append(f"section {section_code} not found")
        return result
    statements = _locate_statements(_blocks(raw, *section))
    expected_end = f"{year}년{PERIOD_END[report_type]}"
    for statement, labels in (("BS", BS_LINES), ("CIS", CIS_LINES)):
        info = statements.get(statement)
        if info is None:
            result.issues.append(f"{statement} not found")
            continue
        header = re.sub(r"(?<=년|월)0", "", info["header"])
        column, rows, issues = _current_column(info["table"])
        period_ok = expected_end in header
        unit_ok = info["unit"] in UNIT_SCALE
        result.statements[statement] = {
            "line": info["line"], "unit": info["unit"], "period_ok": period_ok,
            "column": column, "layouts": sorted({r["layout"] for r in rows}),
        }
        result.issues += [f"{statement} {issue}" for issue in issues]
        if not period_ok:
            result.issues.append(f"{statement} period end {expected_end} not in header")
        if not unit_ok:
            result.issues.append(f"{statement} unit not confirmed")
        if not (period_ok and unit_ok):
            continue
        targets = dict(labels)
        if statement == "CIS":
            targets.update(AUX_CIS_LINES)
        for metric_code, names in targets.items():
            hit = next((r for r in rows if r["name"] in names), None)
            if hit is None:
                continue
            value = parse_amount(hit["text"])
            if value is None:
                result.issues.append(f"{metric_code}: no amount in '{hit['text']}'")
                continue
            store = result.aux if metric_code in AUX_CIS_LINES else result.lines
            store[metric_code] = StatementLine(
                metric_code=metric_code, statement=statement, presented_text=hit["text"],
                presented_value=value, unit=info["unit"], scale=UNIT_SCALE[info["unit"]],
                account_name=hit["account"], section_code=section_code,
                table_line=info["line"], row_index=hit["row"], column_header=column,
                layout=hit["layout"],
            )
    result.summary = _summary_balances(raw, fs_div)
    return result


def _summary_balances(raw: str, fs_div: str) -> dict[str, dict]:
    """Current-period balance lines of the summary statement of financial position.

    The summary section repeats key balances in a separate table.  The
    consolidated (CFS) or separate (OFS) summary is chosen by its heading.
    """
    section = _sections(raw).get(SUMMARY_SECTION)
    if section is None:
        return {}
    heading, unit = "", None
    for kind, value, line in _blocks(raw, *section):
        text = _compact(value if kind == "p" else " ".join(c["text"] for r in value for c in r))
        if kind == "p" or (kind == "table" and len(value) <= 2):
            heading = heading + text if kind == "table" else text
            found_unit = _UNIT.search(text)
            unit = found_unit.group(1) if found_unit else unit
            continue
        consolidated = "연결" in heading
        if (fs_div == "CFS") != consolidated or unit not in UNIT_SCALE:
            continue
        rows = {}
        for index, row in enumerate(value):
            if not row or len(row) < 2:
                continue
            amount = next((parse_amount(c["text"]) for c in row[1:] if _compact(c["text"])), None)
            name = line_name(row[0]["text"])
            if amount is not None and name not in rows:
                rows[name] = {"value": amount * UNIT_SCALE[unit], "row_index": index,
                              "table_line": line, "unit": unit, "account": row[0]["text"]}
        if "자산총계" in rows:
            return rows
    return {}


NOTES_SECTION_BY_FS_DIV = {"CFS": "D-0-3-3-0", "OFS": "D-0-3-5-0"}
# Attached audit reports (whole document = statements + notes) by statement scope.
AUDIT_REPORT_ACODE = {"CFS": "00761", "OFS": "00760"}
_DOCUMENT_CODE = re.compile(r'<DOCUMENT-NAME[^>]*ACODE="(\d+)"')
COMBINED_LOAN_NOTE = re.compile(r"상각후원가측정대출채권및기타금융자산의(내용|구성내역)")
# "대출채권의 내용" / "상각후원가측정대출채권의 구성내역"; the combined line reads
# "대출채권및기타금융자산의", so it cannot match this pattern.
LOAN_DETAIL_NOTE = re.compile(r"대출채권의(내용|구성내역)")


def _current_values(table: list[list[dict]]) -> tuple[str | None, dict[str, int], str | None]:
    """Unit (if inside the table), first-value-column amounts by line name, column header."""
    unit, header, values = None, None, {}
    for row in table:
        texts = [c["text"] for c in row]
        found = _UNIT.search(_compact(" ".join(texts)))
        if found and len(row) <= 2:
            unit = found.group(1)
            continue
        if header is None and len(row) >= 2 and parse_amount(texts[1]) is None:
            header = " ".join(t for t in texts[1:] if t)
            continue
        if len(row) >= 2:
            amount = parse_amount(texts[1])
            name = line_name(texts[0])
            if amount is not None and name not in values:
                values[name] = amount
    return unit, values, header


def _block_text(kind: str, value) -> str:
    return value if kind == "p" else " ".join(c["text"] for row in value for c in row)


def _loan_detail(table: list[list[dict]]) -> dict | None:
    """Net loans and whether the allowance is deducted, in either note layout."""
    text = _compact(" ".join(c["text"] for row in table for c in row))
    allowance = "손실충당금" in text or "대손충당금" in text
    _, values, _ = _current_values(table)
    if "합계" in values:  # rows layout: components, deductions, total
        return {"layout": "rows", "net": values["합계"], "allowance_deducted": allowance}
    for row in table:  # columns layout: one loans row ending with the carrying amount
        if row and line_name(row[0]["text"]) in {"대출채권", "상각후원가측정대출채권"}:
            amounts = [parse_amount(c["text"]) for c in row[1:]]
            amounts = [a for a in amounts if a is not None]
            if amounts:
                return {"layout": "columns", "net": amounts[-1], "allowance_deducted": allowance,
                        "columns": amounts}
    return None


def extract_combined_loan_note(path: Path, fs_div: str) -> dict:
    """Split of a combined 'loans and other financial assets' line from the notes.

    Returns the current-period composition (KRW) and the loans detail with
    locators, or ``issues`` when a table, unit or column cannot be read.
    """
    raw = path.read_text(encoding="utf-8")
    section_code = NOTES_SECTION_BY_FS_DIV[fs_div]
    section = _sections(raw, coded_only=True).get(section_code)
    document_code = _DOCUMENT_CODE.search(raw[:2000])
    if section is None and document_code and document_code.group(1) == AUDIT_REPORT_ACODE[fs_div]:
        section_code, section = f"audit_report_{document_code.group(1)}", (0, len(raw))
    result: dict = {"section_code": section_code, "issues": []}
    if section is None:
        result["issues"].append(f"section {section_code} not found")
        return result
    blocks = _blocks(raw, *section)
    for name, pattern in (("composition", COMBINED_LOAN_NOTE), ("loan_detail", LOAN_DETAIL_NOTE)):
        # The note title is a paragraph in older filings and a cell of a small
        # heading table (with the period and unit) in newer ones.
        hit = next((i for i, (kind, value, _) in enumerate(blocks)
                    if pattern.search(_compact(_block_text(kind, value)))
                    and (kind == "p" or len(value) <= 3)), None)
        if hit is None:
            result["issues"].append(f"{name}: paragraph not found")
            continue
        unit, table, table_line, period_label = None, None, None, None
        for kind, value, line in blocks[hit:hit + 6]:
            if kind == "p" or (kind == "table" and len(value) <= 3 and not _loan_detail(value)):
                cells = [value] if kind == "p" else [c["text"] for row in value for c in row]
                for text in cells:
                    found = _UNIT.search(_compact(text))
                    unit = found.group(1) if found else unit
                    if _compact(text).startswith("당") and len(_compact(text)) <= 6:
                        period_label = text
                continue
            table_unit, values, header = _current_values(value)
            unit = table_unit or unit
            if values or _loan_detail(value):
                table, table_line = value, line
                break
        if table is None or unit not in UNIT_SCALE:
            result["issues"].append(f"{name}: table or unit not readable")
            continue
        scale = UNIT_SCALE[unit]
        entry = {"paragraph": blocks[hit][1], "paragraph_line": blocks[hit][2], "table_line": table_line,
                 "unit": unit, "period_label": period_label}
        if name == "composition":
            _, values, header = _current_values(table)
            entry.update(column_header=header, values={k: v * scale for k, v in values.items()})
        else:
            detail = _loan_detail(table)
            if detail is None:
                result["issues"].append("loan_detail: loans row not found")
                continue
            entry.update(layout=detail["layout"], net=detail["net"] * scale,
                         allowance_deducted=detail["allowance_deducted"])
        result[name] = entry
    return result
