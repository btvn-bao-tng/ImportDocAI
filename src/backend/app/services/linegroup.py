from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..models import ExtractedField, LineItem


@dataclass
class LogicalLine:
    description: str | None
    unit: str | None
    quantity: str | None
    amount: str | None
    unit_price: str | None
    item_ids: list[int] = field(default_factory=list)
    components: list[dict[str, str | None]] = field(default_factory=list)
    has_sub_total: bool = False

    def as_row(self, index: int) -> dict[str, str | None]:
        return {
            "line.no": str(index),
            "line.description": self.description,
            "line.quantity": self.quantity,
            "line.unit": self.unit,
            "line.unit_price": self.unit_price,
            "line.amount": self.amount,
            "line.description": self.description,
            "line.quantity": self.quantity,
            "line.unit": self.unit,
            "line.unit_price": self.unit_price,
            "line.amount": self.amount,
            "line.components": " | ".join(
                f"{c.get('spec') or ''} {c.get('quantity') or ''} {c.get('amount') or ''}".strip()
                for c in self.components
                if any(c.values())
            )
            or None,
            "line.has_sub_total": "1" if self.has_sub_total else None,
        }


def _label(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", text).lower()).strip()


def _num(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _candidate_groups(logical: list[LogicalLine]) -> list[list[LogicalLine]]:
    """Group candidate sub-rows: same description label, or a code-like item under a product line."""
    by_label: dict[str, list[LogicalLine]] = {}
    for line in logical:
        by_label.setdefault(_label(line.description), []).append(line)

    groups = [m for m in by_label.values() if len(m) > 1]
    ungrouped = [m[0] for m in by_label.values() if len(m) == 1]

    if groups:
        return groups + [[line] for line in ungrouped]

    # Fallback for invoice layouts listing a product followed by its spec/quantity rows:
    # the first row carries the description and the total; the code rows follow it.
    head = logical[0]
    if _looks_like_product_head(head):
        return [logical]
    return [[line] for line in logical]


def _looks_like_product_head(line: LogicalLine) -> bool:
    if not line.description or not line.quantity or not line.amount:
        return False
    # product descriptions are words; sub-row codes are short alphanumeric tokens
    return " " in line.description.strip() or len(line.description) > 12


def group_line_items(items: list[LineItem], fields: list[ExtractedField]) -> list[LogicalLine]:
    """Collapse sub-rows of one product into a single logical line.

    Invoice layouts often repeat the product description across spec/quantity rows and print a
    single total at the end; writing each physical row would duplicate quantity and amount in
    the export. Rows are grouped by description label and merged when their quantities add up to
    a declared total, or when they share a description with no per-row amount.
    """
    by_item: dict[int, dict[str, str | None]] = {}
    for f in fields:
        if f.line_item_id is None:
            continue
        key = f.field_key.split(".", 1)[1]
        by_item.setdefault(f.line_item_id, {})[key] = f.normalized_value

    logical = [
        LogicalLine(
            description=item.description,
            unit=item.unit,
            quantity=str(item.quantity) if item.quantity is not None else None,
            amount=str(item.amount) if item.amount is not None else None,
            unit_price=str(item.unit_price) if item.unit_price is not None else None,
            item_ids=[item.id],
            components=[{"spec": item.spec, "quantity": by_item.get(item.id, {}).get("quantity"), "amount": by_item.get(item.id, {}).get("amount")}],
            has_sub_total=bool(item.amount is not None),
        )
        for item in items
    ]
    if len(logical) <= 1:
        for line in logical:
            line.has_sub_total = False
        return logical

    merged: list[LogicalLine] = []
    for members in _candidate_groups(logical):
        if len(members) == 1:
            members[0].has_sub_total = False
            merged.append(members[0])
            continue
        head = members[0]
        parts = members[1:]
        head_qty, head_amount = _num(head.quantity), _num(head.amount)
        part_qty = sum(_num(p.quantity) or 0 for p in parts)
        part_amount = sum(_num(p.amount) or 0 for p in parts)
        qty_ok = bool(head_qty) and abs(part_qty - head_qty) / head_qty < 0.02
        amount_ok = bool(head_amount) and abs(part_amount - head_amount) <= 0.05
        if qty_ok and (amount_ok or not head_amount):
            head.components = [
                {"spec": m.components[0]["spec"], "quantity": m.quantity, "amount": m.amount} for m in members
            ]
            head.item_ids = [i for m in members for i in m.item_ids]
            head.has_sub_total = False
            merged.append(head)
            continue
        merged.extend(members)

    order = {item.id: idx for idx, item in enumerate(items)}
    merged.sort(key=lambda l: min(order.get(i, 0) for i in l.item_ids))
    for line in merged:
        if len(line.item_ids) == 1 and len(logical) > 1:
            line.has_sub_total = True
    return merged
