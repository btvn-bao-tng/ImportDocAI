from __future__ import annotations

from decimal import Decimal

from app.services.normalize import (
    detect_number_locale,
    normalize_company,
    normalize_currency,
    normalize_date,
    normalize_unit,
    number_to_str,
    parse_decimal,
)


def test_dates_from_requirements():
    assert normalize_date("SEP.18,2026") == "2026-09-18"
    assert normalize_date("2026-09-18") == "2026-09-18"
    assert normalize_date("18/09/2026") == "2026-09-18"
    assert normalize_date("2026年9月18日") == "2026-09-18"
    assert normalize_date("SEP 09 2026") == "2026-09-09"
    assert normalize_date("09 SEP 2026") == "2026-09-09"


def test_date_returns_none_when_ambiguous_or_invalid():
    assert normalize_date("not a date") is None
    assert normalize_date("") is None
    assert normalize_date(None) is None
    assert normalize_date("2026-13-45") is None


def test_number_locale_detection():
    assert detect_number_locale("41,686.84") == "period_decimal"
    assert detect_number_locale("41.686,836") == "comma_decimal"
    assert detect_number_locale("26400.000") == "period_decimal"
    assert detect_number_locale("38,03") == "comma_decimal"


def test_decimal_parsing_keeps_precision():
    assert parse_decimal("41,686.84") == Decimal("41686.84")
    assert parse_decimal("41.686,836") == Decimal("41686.836")
    assert parse_decimal("26400.000 KGS") == Decimal("26400.000")
    assert parse_decimal("38,03") == Decimal("38.03")
    assert parse_decimal("67,175.4552") == Decimal("67175.4552")


def test_decimal_parsing_never_rounds():
    assert str(parse_decimal("67,175.4552")) == "67175.4552"
    assert str(parse_decimal("41.686,836")) == "41686.836"


def test_number_to_str():
    assert number_to_str(Decimal("41686.84")) == "41686.84"
    assert number_to_str(Decimal("26400.000")) == "26400"
    assert number_to_str(Decimal("38.030")) == "38.03"


def test_currency_and_units():
    assert normalize_currency("U.S. DOLLARS") == "USD"
    assert normalize_currency("USD") == "USD"
    assert normalize_currency("usd") == "USD"
    assert normalize_currency("人民币") == "CNY"
    assert normalize_unit("KGS") == "kg"
    assert normalize_unit("CBM") == "m³"


def test_company_names_are_not_merged():
    assert normalize_company("  DALIAN   CHUNLING  WOOD ") == "DALIAN CHUNLING WOOD"
    assert normalize_company("DALIAN CHUNLING WOOD INDUSTRY CO., LTD.") != normalize_company("DALIAN CHUNLING")


def test_wrong_model_locale_hint_cannot_corrupt_amount():
    """Regression from LS2628 insurance policy: 45,855.52 must never become 45.85552."""
    assert parse_decimal("45,855.52", "comma_decimal") == Decimal("45855.52")
    assert parse_decimal("41,686.84", "comma_decimal") == Decimal("41686.84")
    assert parse_decimal("41.686,836", "period_decimal") == Decimal("41686.836")
    assert parse_decimal("26400.000", "comma_decimal") == Decimal("26400000")
