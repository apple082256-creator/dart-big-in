from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "원": Decimal(1),
    "KRW": Decimal(1),
    "천원": Decimal(1_000),
    "백만원": Decimal(1_000_000),
    "억원": Decimal(100_000_000),
    "조원": Decimal(1_000_000_000_000),
}
SCALE_ERROR_FACTORS = (Decimal(10), Decimal(100), Decimal(1_000), Decimal(100_000_000))

COMPOUND_MONEY_PATTERN = re.compile(
    r"(?<![\d.])(?=\d)((?:\d[\d,]*(?:\.\d+)?\s*조\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*억\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*백만\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*천\s*)?"
    r"(?:\d[\d,]*(?:\.\d+)?\s*)?)원"
)


def _decimal_value(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as error:
        raise ValueError(f"숫자로 변환할 수 없습니다: {value!r}") from error


def normalize_financial_value(
    value: str | int | float | Decimal,
    unit: str,
    target_unit: str = "KRW",
) -> int | Decimal:
    """Convert an explicitly unit-labelled financial value deterministically."""
    normalized_unit = unit.strip()
    if target_unit != "KRW":
        raise ValueError("현재 target_unit은 KRW만 지원합니다.")
    if normalized_unit not in UNIT_MULTIPLIERS:
        raise ValueError(f"지원하지 않는 금액 단위입니다: {unit!r}")
    result = _decimal_value(value) * UNIT_MULTIPLIERS[normalized_unit]
    return int(result) if result == result.to_integral_value() else result


def detect_scale_factor(
    value: str | int | float | Decimal,
    expected: str | int | float | Decimal,
) -> int | None:
    """Return a common unit-error factor when two non-zero values differ by that scale."""
    actual_value, expected_value = abs(_decimal_value(value)), abs(_decimal_value(expected))
    if not actual_value or not expected_value:
        return None
    ratio = max(actual_value, expected_value) / min(actual_value, expected_value)
    for factor in SCALE_ERROR_FACTORS:
        if abs(ratio - factor) <= factor * Decimal("0.001"):
            return int(factor)
    return None


def format_krw(value: str | int | float | Decimal) -> str:
    amount = _decimal_value(value)
    if amount == amount.to_integral_value():
        return f"{int(amount):,}원"
    return f"{amount:,.2f}원"


def _trim_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def format_eok(value: str | int | float | Decimal) -> str:
    amount = _decimal_value(value) / UNIT_MULTIPLIERS["억원"]
    if amount == amount.to_integral_value():
        return f"{int(amount):,}억원"
    return f"{format(amount, ',f').rstrip('0').rstrip('.')}억원"


def format_trillion(value: str | int | float | Decimal, places: int = 2) -> str:
    quantum = Decimal(1).scaleb(-places)
    amount = (_decimal_value(value) / UNIT_MULTIPLIERS["조원"]).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{amount:,.{places}f}조원"


def format_korean_large(value: str | int | float | Decimal) -> str:
    original = int(_decimal_value(value))
    sign = "-" if original < 0 else ""
    amount = abs(original)
    trillion, remainder = divmod(amount, int(UNIT_MULTIPLIERS["조원"]))
    eok = remainder // int(UNIT_MULTIPLIERS["억원"])
    if trillion and eok:
        return f"{sign}{trillion:,}조 {eok:,}억원"
    if trillion:
        return f"{sign}{trillion:,}조원"
    return f"{sign}{eok:,}억원" if eok else format_krw(original)


def normalized_value_record(
    value: str | int | float | Decimal,
    unit: str,
    *,
    original_text: str | None = None,
    label: str | None = None,
    source_type: str = "explicit_unit",
) -> dict:
    normalized = normalize_financial_value(value, unit)
    multiplier = int(UNIT_MULTIPLIERS[unit])
    raw_value = str(value).strip()
    return {
        "label": label,
        "original_text": original_text or f"{raw_value}{unit}",
        "raw_value": raw_value,
        "raw_unit": unit,
        "parsed_value": raw_value,
        "parsed_unit": unit,
        "normalized_value": normalized,
        "normalized_unit": "KRW",
        "unit_conversion_applied": multiplier != 1,
        "conversion_formula": f"value × {multiplier:,}",
        "display_value_krw": format_krw(normalized),
        "display_value_eok": format_eok(normalized),
        "display_value_trillion": format_trillion(normalized),
        "display_value_korean": format_korean_large(normalized),
        "source_type": source_type,
    }


def _compound_record(expression: str) -> dict:
    components = re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(조|억|백만|천)", expression)
    if not components:
        number = re.search(r"\d[\d,]*(?:\.\d+)?", expression)
        if not number:
            raise ValueError(f"금액 표현을 파싱할 수 없습니다: {expression!r}")
        return normalized_value_record(number.group(), "원", original_text=expression)
    unit_map = {"조": "조원", "억": "억원", "백만": "백만원", "천": "천원"}
    total = sum(
        (_decimal_value(number) * UNIT_MULTIPLIERS[unit_map[unit]] for number, unit in components),
        Decimal(0),
    )
    record = normalized_value_record(total, "원", original_text=expression, source_type="compound_unit")
    record["raw_value"] = expression.removesuffix("원").strip()
    record["raw_unit"] = "복합원화단위"
    record["parsed_value"] = [{"value": number, "unit": unit_map[unit]} for number, unit in components]
    record["parsed_unit"] = "복합원화단위"
    record["unit_conversion_applied"] = True
    record["conversion_formula"] = " + ".join(
        f"{number} × {int(UNIT_MULTIPLIERS[unit_map[unit]]):,}" for number, unit in components
    )
    return record


def extract_monetary_facts(text: str, max_facts: int = 80) -> list[dict]:
    """Extract explicit Korean money expressions and key facility-investment table rows."""
    facts: list[dict] = []
    seen: set[tuple[str | None, int | Decimal]] = set()

    def add(record: dict) -> None:
        key = (record.get("label"), record["normalized_value"])
        if key not in seen and len(facts) < max_facts:
            seen.add(key)
            facts.append(record)

    marker = text.find("(시설투자 현황)")
    if marker >= 0:
        block = text[marker:]
        unit_match = re.search(r"단위\s*:\s*(천원|백만원|억원|조원|원)", block)
        if unit_match:
            unit = unit_match.group(1)
            labels = ("DS 부문", "SDC", "기 타", "합 계")
            for index, label in enumerate(labels):
                start = block.find(label, unit_match.end())
                if start < 0:
                    continue
                ends = [block.find(next_label, start + len(label)) for next_label in labels[index + 1:]]
                end = min((position for position in ends if position >= 0), default=len(block))
                numbers = re.findall(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])", block[start:end])
                if numbers:
                    raw_value = numbers[-1]
                    add(normalized_value_record(
                        raw_value, unit, original_text=f"{raw_value}{unit}",
                        label=label, source_type="facility_investment_table",
                    ))

    for match in COMPOUND_MONEY_PATTERN.finditer(text):
        add(_compound_record(match.group(0)))

    unit_matches = list(re.finditer(r"단위\s*:\s*(천원|백만원|억원|조원|원)", text))
    for index, match in enumerate(unit_matches):
        unit = match.group(1)
        end = unit_matches[index + 1].start() if index + 1 < len(unit_matches) else len(text)
        for raw_value in re.findall(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?![\d.])", text[match.end():end]):
            add(normalized_value_record(
                raw_value, unit, original_text=f"{raw_value}{unit}",
                label="표 금액", source_type="tabular_unit",
            ))
    return facts
