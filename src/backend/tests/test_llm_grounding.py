from __future__ import annotations

from app.services.llm import ground_quote, locate_quote, normalize_bbox


def test_ground_quote_matches_normalized_whitespace():
    page = "Gross Weight :  26,400.000 KGS\nNet Weight: 26.000 KGS"
    assert ground_quote("26,400.000 KGS", page) is True
    assert ground_quote("gross   weight", page) is True
    assert ground_quote("45,000 KGS", page) is False
    assert ground_quote("", page) is False
    assert ground_quote(None, page) is False


def test_ground_quote_rejects_hallucination_against_other_document():
    invoice = "TOTAL AMOUNT: USD 41,686.84"
    assert ground_quote("67,175.4552", invoice) is False


def test_locate_quote_returns_bbox_from_words():
    words = [
        {"text": "Gross", "x0": 10, "top": 20, "x1": 40, "bottom": 32},
        {"text": "Weight", "x0": 45, "top": 20, "x1": 80, "bottom": 32},
        {"text": "26,400.000", "x0": 85, "top": 20, "x1": 130, "bottom": 32},
    ]
    bbox = locate_quote("Gross Weight", words)
    assert bbox == [10, 20, 80, 32]
    assert locate_quote("missing text", words) is None


def test_normalize_bbox_clamps_to_page():
    assert normalize_bbox([0, 0, 100, 200], 100, 200) == [0.0, 0.0, 1.0, 1.0]
    assert normalize_bbox([-50, 0, 150, 100], 100, 200) == [0.0, 0.0, 1.0, 0.5]
    assert normalize_bbox(None, 100, 200) is None
