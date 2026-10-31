from __future__ import annotations

from app.models import LineItem
from app.services.linegroup import group_line_items


def _item(id_: int, desc: str, qty: str | None, amount: str | None, spec: str | None = None) -> LineItem:
    item = LineItem(document_id=1, seq=id_, description=desc, quantity=float(qty) if qty else None, amount=float(amount) if amount else None, spec=spec)
    item.id = id_
    return item


def test_sub_rows_merge_into_one_logical_line():
    """LS2628 invoice: EU OAK FACE VENEER 38.030 total split into CD10/EF3 sub-rows."""
    items = [
        _item(1, "EU OAK FACE VENEER", "38.030", "41686.836"),
        _item(2, "CD10USD/M2", "11.490", "23826"),
        _item(3, "EF3USD/M2", "26.540", "17860.836"),
    ]
    logical = group_line_items(items, [])
    assert len(logical) == 1
    row = logical[0].as_row(1)
    assert float(row["line.quantity"]) == 38.03
    assert float(row["line.amount"]) == 41686.836
    assert "23826" in (row["line.components"] or "")
    assert row["line.has_sub_total"] is None


def test_distinct_products_stay_separate():
    items = [_item(1, "OAK VENEER", "10", "1000"), _item(2, "PINE BOARD", "5", "500")]
    logical = group_line_items(items, [])
    assert len(logical) == 2
    assert [l.description for l in logical] == ["OAK VENEER", "PINE BOARD"]


def test_rows_without_totals_are_not_merged_into_one_amount():
    items = [_item(1, "OAK VENEER", "10", "1000"), _item(2, "OAK VENEER", "5", "500")]
    logical = group_line_items(items, [])
    assert len(logical) == 2
    assert all(l.has_sub_total for l in logical)
