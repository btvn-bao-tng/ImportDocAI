from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

CURRENCY_ALIASES = {
    "USD": "USD", "US$": "USD", "U.S.$": "USD", "U.S. DOLLARS": "USD", "US DOLLARS": "USD",
    "UNITED STATES DOLLAR": "USD", "DOLLAR": "USD", "DOLLARS": "USD", "美金": "USD", "美元": "USD",
    "VND": "VND", "VNĐ": "VND", "VIETNAM DONG": "VND", "人民币": "CNY", "CNY": "CNY", "RMB": "CNY",
    "元": "CNY", "EUR": "EUR", "EURO": "EUR", "欧元": "EUR", "HKD": "HKD", "港币": "HKD",
}

UNIT_ALIASES = {
    "CBM": "m³", "M3": "m³", "M³": "m³", "立方米": "m³",
    "KGS": "kg", "KG": "kg", "KILOGRAM": "kg", "KILOGRAMS": "kg", "公斤": "kg", "千克": "kg",
    "MT": "t", "TON": "t", "TONS": "t", "吨": "t",
    "PCS": "pcs", "PIECE": "pcs", "PIECES": "pcs", "件": "pcs",
    "PALLETS": "pallets", "PLT": "pallets", "托盘": "pallets",
    "CTNS": "cartons", "CARTONS": "cartons", "纸箱": "cartons",
}


def normalize_date(value: str | None) -> str | None:
    """Normalize common invoice date formats to ISO 8601 (YYYY-MM-DD).

    Returns None when the input is not a confidently parseable date; never guesses.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _iso(y, mo, d)

    m = re.fullmatch(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _iso(y, mo, d)

    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,\s*(\d{4})", text)
    if m:
        mon = _month(m.group(1))
        if mon:
            return _iso(int(m.group(3)), mon, int(m.group(2)))

    m = re.fullmatch(r"(\d{1,2})[ -]([A-Za-z]{3,9})\.?[ -](\d{4})", text)
    if m:
        mon = _month(m.group(2))
        if mon:
            return _iso(int(m.group(3)), mon, int(m.group(1)))

    m = re.fullmatch(r"([A-Za-z]{3,9})\.?[ -](\d{1,2})[ -](\d{4})", text)
    if m:
        mon = _month(m.group(1))
        if mon:
            return _iso(int(m.group(3)), mon, int(m.group(2)))

    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:
        a, b, y = (int(g) for g in m.groups())
        if a > 12:
            return _iso(y, b, a)
        if b > 12:
            return _iso(y, a, b)
        return _iso(y, b, a)  # default to day-first (VN/CN convention)
    return None


def _month(token: str) -> int | None:
    key = token.strip().upper().rstrip(".")
    if len(key) > 4:
        key = key[:4] if key[:4] in MONTHS else key[:3]
    return MONTHS.get(key)


def _iso(year: int, month: int, day: int) -> str | None:
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2200):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


# Strict Vietnamese thousands grouping with TWO or more dot groups
# ('607.301.890', '1.234.567'): a decimal separator appears at most once in any
# locale, so repeated groups are unambiguously grouping. A single trailing
# 3-digit group ('38.030', '1.180') stays hint-driven by design (see
# _resolve_locale), and irregular patterns ('1.2.3') stay unparseable.
_GROUPED = re.compile(r"[+-]?\d{1,3}(\.\d{3}){2,}")


def detect_number_locale(value: str | None) -> str:
    """Return 'comma_decimal' or 'period_decimal' based on separator placement."""
    if not value:
        return "period_decimal"
    text = str(value).strip()
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if last_comma == -1:
        # Two or more 3-digit dot groups are unambiguously thousands grouping
        # ('607.301.890' = 607301890), never a fraction, regardless of any
        # document-level hint.
        if _GROUPED.fullmatch(text):
            return "comma_decimal"
        return "period_decimal"
    if last_dot == -1:
        digits_after_comma = len(text) - last_comma - 1
        return "comma_decimal" if digits_after_comma != 3 else "period_decimal"
    return "comma_decimal" if last_comma > last_dot else "period_decimal"


def parse_decimal(value: str | None, locale: str | None = None) -> Decimal | None:
    """Parse a number string into Decimal without float rounding.

    Handles '41,686.84', '41.686,836', '26400.000', '38,03', '1 234,56'.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("\u00a0", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"[^\d,.+-]", "", text)
    if not text or not re.search(r"\d", text):
        return None
    loc = _resolve_locale(text, locale)
    if loc == "comma_decimal":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        num = Decimal(text)
    except InvalidOperation:
        return None
    return -num if negative else num


def _resolve_locale(text: str, locale: str | None) -> str:
    """Prefer the string itself over a model hint whenever it is unambiguous.

    Both separators present (45,855.52 / 41.686,836) is decided by position. A trailing
    group of exactly three digits (26400.000, 1.180) is ambiguous to a reader but usually a
    thousands group or thousands-decimal notation, so the document-level hint wins there.
    """
    heuristic = detect_number_locale(text)
    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        return heuristic
    if not (has_comma or has_dot):
        return heuristic
    if not has_comma and _GROUPED.fullmatch(text):
        # Two or more dot groups cannot be a decimal point even in a
        # period-decimal document; the grouping read always wins over the hint.
        return "comma_decimal"
    if locale in {"comma_decimal", "period_decimal"}:
        return locale
    return heuristic


def quantize(value: Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    q = Decimal(1).scaleb(-places)
    return value.quantize(q)


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip().upper()
    return CURRENCY_ALIASES.get(key) or (key if re.fullmatch(r"[A-Z]{3}", key) else None)


def normalize_unit(value: str | None) -> str | None:
    if not value:
        return None
    key = str(value).strip().upper().rstrip(".")
    return UNIT_ALIASES.get(key, str(value).strip())


def normalize_company(value: str | None) -> str | None:
    """Whitespace-only normalization.

    Company names are deliberately NOT merged or fuzzy-matched: requirement FR-04
    forbids collapsing two firms because their names look similar.
    """
    if not value:
        return None
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_volume_cbm(value: str | None) -> tuple[Decimal | None, str | None]:
    num = parse_decimal(value)
    return num, ("m³" if num is not None else None)


def number_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")
